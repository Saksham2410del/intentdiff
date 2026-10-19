"""Web server: a JSON API (how a ticketing system would call IntentDiff) and a
small review page for demos.

    uvicorn app.server:app --reload        then open http://127.0.0.1:8000

Every review and every approval is stored. An approval is tied to the exact
script that was reviewed (by hash), so editing the script afterwards voids it.

When a ticket is ambiguous, the review pauses and shows the questions for the
requester. The paused run is saved by LangGraph's checkpointer, so it can be
continued with the answer later - even after a restart.
"""
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from eval.mutate import mutants
from intentdiff import __version__, config
from intentdiff.dryrun import DryRunError, dry_run
from intentdiff.graph import build_graph, run, sqlite_checkpointer
from intentdiff.history import TicketHistory
from intentdiff.llm import GeminiClient
from intentdiff.review import BLOCKED, MATCH, Review
from intentdiff.schema import load_schema

STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL,
    ticket_sha  TEXT NOT NULL,
    sql_sha     TEXT NOT NULL,
    db_sha      TEXT NOT NULL,
    model       TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    report      TEXT NOT NULL
);
-- append-only: nothing ever updates or deletes a decision
CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY,
    review_id   INTEGER NOT NULL REFERENCES reviews (id),
    decided_at  TEXT NOT NULL,
    reviewer    TEXT NOT NULL,
    approved    INTEGER NOT NULL,
    reason      TEXT
);
"""

app = FastAPI(title="IntentDiff", version=__version__)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
schema = load_schema(config.DB_PATH, config.NOTES_PATH)
history = TicketHistory.load()
checkpointer = sqlite_checkpointer(config.CHECKPOINTS_DB)
llm = GeminiClient()


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def store():
    conn = sqlite3.connect(config.REVIEWS_DB)
    conn.row_factory = sqlite3.Row
    return conn


DB_SHA = file_sha256(config.DB_PATH)  # which version of the data the reviews ran against
with closing(store()) as _conn:
    _conn.executescript(STORE_SCHEMA)


def make_graph():
    # built per request so it always uses the current `llm` (the tests swap it)
    return build_graph(config.DB_PATH, schema, llm, history=history,
                       ask_requester=True, checkpointer=checkpointer)


def save(result, thread_id, waiting):
    report = {**result.to_dict(), "thread_id": thread_id, "waiting_for_answer": waiting}
    with closing(store()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO reviews (created_at, ticket_sha, sql_sha, db_sha, model, verdict, report) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), sha256(result.ticket), sha256(result.sql), DB_SHA,
             llm.model, result.verdict, json.dumps(report, default=str)),
        )
    return cur.lastrowid


def run_review(ticket, sql):
    thread_id = uuid.uuid4().hex
    result, waiting = run(make_graph(), llm, Review(ticket=ticket, sql=sql), thread_id=thread_id)
    return save(result, thread_id, waiting), result, waiting


def continue_review(review_id, answer):
    """Resume a review that stopped to ask the requester, now with their answer."""
    with closing(store()) as conn:
        row = conn.execute("SELECT report FROM reviews WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "review not found")
    report = json.loads(row["report"])
    if not report.get("waiting_for_answer"):
        raise HTTPException(409, "this review isn't waiting for an answer")
    if not answer.strip():
        raise HTTPException(422, "the answer is empty")

    result, waiting = run(make_graph(), llm, answer=answer, thread_id=report["thread_id"])
    return save(result, report["thread_id"], waiting), result, waiting


# ---------------------------------------------------------------- JSON API

class ReviewRequest(BaseModel):
    ticket: str
    sql: str


class AnswerRequest(BaseModel):
    answer: str


class DryRunRequest(BaseModel):
    sql: str


class DecisionRequest(BaseModel):
    reviewer: str
    approve: bool
    sql_sha256: str      # hash of the script the reviewer is actually looking at
    reason: str = ""


@app.get("/health")
def health():
    return {"status": "ok", "model": llm.model, "db_sha256": DB_SHA, "retrieval": history.mode}


@app.post("/dry-run")
def api_dry_run(req: DryRunRequest):
    try:
        changes = dry_run(config.DB_PATH, req.sql, schema)
    except DryRunError as e:
        raise HTTPException(422, f"blocked: {e}")
    return {"summary": changes.summary(), "total": changes.total, "rows": changes.to_records(50)}


@app.post("/reviews")
def api_create_review(req: ReviewRequest):
    review_id, result, waiting = run_review(req.ticket, req.sql)
    return {"id": review_id, "sql_sha256": sha256(req.sql), "waiting_for_answer": waiting, **result.to_dict()}


@app.post("/reviews/{review_id}/answer")
def api_answer(review_id: int, req: AnswerRequest):
    new_id, result, waiting = continue_review(review_id, req.answer)
    return {"id": new_id, "sql_sha256": sha256(result.sql), "waiting_for_answer": waiting, **result.to_dict()}


@app.get("/reviews/{review_id}")
def api_get_review(review_id: int):
    with closing(store()) as conn:
        row = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "review not found")
        decisions = conn.execute(
            "SELECT decided_at, reviewer, approved, reason FROM decisions WHERE review_id = ? ORDER BY id",
            (review_id,),
        ).fetchall()
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "sql_sha256": row["sql_sha"],
        "db_sha256": row["db_sha"],
        "model": row["model"],
        **json.loads(row["report"]),
        "decisions": [dict(d) for d in decisions],
    }


@app.post("/reviews/{review_id}/decision")
def api_decide(review_id: int, req: DecisionRequest):
    with closing(store()) as conn, conn:
        row = conn.execute("SELECT verdict, sql_sha FROM reviews WHERE id = ?", (review_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "review not found")
        if req.sql_sha256 != row["sql_sha"]:
            raise HTTPException(409, "this is not the script that was reviewed - run a new review")
        if req.approve and row["verdict"] == BLOCKED:
            raise HTTPException(422, "a blocked script can't be approved")
        if req.approve and row["verdict"] != MATCH and not req.reason.strip():
            raise HTTPException(422, f"the verdict was {row['verdict']} - approving it anyway needs a written reason")

        conn.execute(
            "INSERT INTO decisions (review_id, decided_at, reviewer, approved, reason) VALUES (?, ?, ?, ?, ?)",
            (review_id, datetime.now(timezone.utc).isoformat(), req.reviewer, int(req.approve), req.reason),
        )
    return {"review_id": review_id, "approved": req.approve, "verdict": row["verdict"]}


# ---------------------------------------------------------------- demo page

def load_examples():
    """Tickets from the eval set, each with its correct script and a few broken ones."""
    tickets = yaml.safe_load((config.ROOT / "eval" / "tickets.yaml").read_text(encoding="utf-8"))
    examples = {}
    for t in tickets:
        scripts = {}
        if "gold" in t:
            scripts["correct script"] = t["gold"]
        else:
            scripts["engineer's script"] = t["script"]
        for i, sql in enumerate(t.get("bad", []), 1):
            scripts[f"known mistake {i}"] = sql
        if "gold" in t:
            for op, sql in mutants(t["gold"]):
                scripts.setdefault(f"mutation: {op}", sql)  # one of each kind is plenty
        examples[t["id"]] = {"label": f"{t['id']} - {t['category']}", "ticket": t["ticket"], "scripts": scripts}
    return examples


EXAMPLES = load_examples()


def render(request, **context):
    context.setdefault("example", "")
    context.setdefault("script_name", "")
    return templates.TemplateResponse(request, "index.html", {
        "examples": EXAMPLES,
        "past": history.tickets,
        "has_key": bool(os.getenv("GEMINI_API_KEY")),
        "model": llm.model,
        **context,
    })


async def read_form(request):
    # HTML forms are url-encoded, so the standard library is enough - no python-multipart needed
    body = (await request.body()).decode("utf-8")
    return {key: values[0] for key, values in parse_qs(body, keep_blank_values=True).items()}


@app.get("/", response_class=HTMLResponse)
def page(request: Request, example: str = "T03", script: str = "known mistake 1"):
    ex = EXAMPLES.get(example) or next(iter(EXAMPLES.values()))
    name = script if script in ex["scripts"] else next(iter(ex["scripts"]))
    return render(request, ticket=ex["ticket"], sql=ex["scripts"][name], example=example, script_name=name)


@app.post("/", response_class=HTMLResponse)
async def page_submit(request: Request):
    form = await read_form(request)
    ticket, sql = form.get("ticket", ""), form.get("sql", "")
    context = {"ticket": ticket, "sql": sql,
               "example": form.get("example", ""), "script_name": form.get("script_name", "")}

    # dry runs and LLM calls are blocking, so keep them off the event loop
    if form.get("action") == "dry_run":
        try:
            context["dry"] = await run_in_threadpool(dry_run, config.DB_PATH, sql, schema)
        except DryRunError as e:
            context["message"] = f"Blocked: {e}"
    else:
        review_id, result, waiting = await run_in_threadpool(run_review, ticket, sql)
        context.update(review_id=review_id, result=result, waiting=waiting, sql_sha=sha256(sql))
    return render(request, **context)


@app.post("/answer", response_class=HTMLResponse)
async def page_answer(request: Request):
    form = await read_form(request)
    context = {"ticket": form.get("ticket", ""), "sql": form.get("sql", "")}
    try:
        review_id, result, waiting = await run_in_threadpool(
            continue_review, int(form.get("review_id", 0)), form.get("answer", "")
        )
        context.update(review_id=review_id, result=result, waiting=waiting, sql_sha=sha256(result.sql))
    except HTTPException as e:
        context["message"] = f"Couldn't continue: {e.detail}"
    return render(request, **context)


@app.post("/decide", response_class=HTMLResponse)
async def page_decide(request: Request):
    form = await read_form(request)
    req = DecisionRequest(
        reviewer=form.get("reviewer", "").strip() or "anonymous",
        approve=form.get("decision") == "approve",
        sql_sha256=form.get("sql_sha256", ""),
        reason=form.get("reason", ""),
    )
    review_id = int(form.get("review_id", 0))
    try:
        api_decide(review_id, req)
        message = f"Review #{review_id} {'approved' if req.approve else 'rejected'} by {req.reviewer}."
    except HTTPException as e:
        message = f"Not saved: {e.detail}"
    return render(request, ticket=form.get("ticket", ""), sql=form.get("sql", ""), message=message)
