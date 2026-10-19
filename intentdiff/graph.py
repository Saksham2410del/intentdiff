"""The check as a LangGraph workflow.

    check_policy -> dry_run_script -> find_similar -> read_ticket -> run_readings -> compare_effects
                                                           ^             |
                                                           +--- retry ---+   the model's SQL failed on the copy

    read_ticket -> ask_requester -> read_ticket              the ticket is ambiguous and a person can answer

The flow is fixed - this isn't an agent choosing its own next step. LangGraph
is here for the two loops and for pausing a review while it waits for the
requester (the paused run is saved by a checkpointer and picked up later).

Every verdict is still decided by plain code in the nodes below. The model
only ever writes a reference fix.
"""
import sqlite3
import time
from typing import TypedDict

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from intentdiff import config
from intentdiff.changes import agree, compare
from intentdiff.dryrun import DryRunError, dry_run
from intentdiff.intent import IntentError, derive_intent
from intentdiff.llm import LLMError
from intentdiff.policy import check_sql
from intentdiff.review import BLOCKED, MATCH, MISMATCH, UNVERIFIABLE, summarise

# Our own classes travel through saved checkpoints, and LangGraph only loads
# types it has been told about.
SAVED_TYPES = [
    ("intentdiff.review", "Review"),
    ("intentdiff.changes", "ChangeSet"),
    ("intentdiff.changes", "RowChange"),
    ("intentdiff.changes", "Issue"),
    ("intentdiff.intent", "IntentSpec"),
    ("intentdiff.history", "PastTicket"),
]


class State(TypedDict, total=False):
    review: object      # the Review being filled in
    examples: list      # approved fixes for similar past tickets
    feedback: str       # why the last reading was thrown away, sent back to the model
    attempts: int       # how many times the ticket has been read in this round
    questions: list     # open questions for the requester


def serializer():
    return JsonPlusSerializer(allowed_msgpack_modules=SAVED_TYPES)


def sqlite_checkpointer(path=config.CHECKPOINTS_DB):
    """Keeps paused reviews in a SQLite file, so they survive a restart."""
    conn = sqlite3.connect(path, check_same_thread=False)
    return SqliteSaver(conn, serde=serializer())


def with_answers(ticket, answers):
    if not answers:
        return ticket
    return ticket.rstrip() + "\n\nAnswers from the requester:\n" + "\n".join(f"- {a}" for a in answers)


def build_graph(db_path, schema, llm, history=None, samples=config.INTENT_SAMPLES,
                ask_requester=False, checkpointer=None):
    """Wire the steps together.

    history        - a TicketHistory; turns on the retrieval step
    ask_requester  - pause on an ambiguous ticket instead of ending as UNVERIFIABLE.
                     Needs a checkpointer, because the run has to be saved while it waits.
    """
    if ask_requester and checkpointer is None:
        raise ValueError("pausing for the requester needs a checkpointer")

    def check_policy(state):
        result = state["review"]
        policy = check_sql(result.sql, schema)
        if not policy.ok:
            result.finish(BLOCKED, policy.problems)
        return {"review": result}

    def dry_run_script(state):
        result = state["review"]
        try:
            result.script_changes = dry_run(db_path, result.sql, schema)
        except DryRunError as e:
            result.finish(BLOCKED, [f"the script fails on a copy of the database: {e}"])
            return {"review": result}
        if result.script_changes.total > config.LARGE_CHANGE_ROWS:
            result.warnings.append(f"large change ({result.script_changes.total:,} rows) - needs a senior approver")
        return {"review": result}

    def find_similar(state):
        result = state["review"]
        if history is None:
            return {"examples": []}
        found = history.search(result.ticket)
        result.examples = [t.id for t in found]
        return {"review": result, "examples": found}

    def read_ticket(state):
        result = state["review"]
        ticket = with_answers(result.ticket, result.answers)
        result.attempts = state.get("attempts", 0) + 1
        try:
            result.intents = [
                derive_intent(ticket, schema, llm, variant=v,
                              examples=state.get("examples", []), feedback=state.get("feedback", ""))
                for v in range(samples)
            ]
        except (IntentError, LLMError) as e:
            # Fail closed: no usable reading of the ticket means no verdict, never a pass.
            result.finish(UNVERIFIABLE, [f"couldn't get a usable reading of the ticket: {e}"])
            return {"review": result, "attempts": result.attempts}

        questions = list(dict.fromkeys(q for spec in result.intents for q in spec.ambiguities))
        if questions:
            result.finish(UNVERIFIABLE, ["the ticket is ambiguous - ask the requester:", *questions])
        return {"review": result, "attempts": result.attempts, "questions": questions, "feedback": ""}

    def ask_requester_node(state):
        # The run stops here and is saved. interrupt() hands back the answer once
        # someone resumes it, which can be hours later, after a restart.
        answer = interrupt({"questions": state["questions"]})
        result = state["review"]
        result.answers.append(str(answer).strip())
        result.verdict, result.reasons = "", []
        return {"review": result, "questions": [], "attempts": 0, "feedback": ""}

    def run_readings(state):
        """Dry-run every reading of the ticket and check that they agree."""
        result = state["review"]
        expected = []
        for spec in result.intents:
            try:
                changes = dry_run(db_path, spec.reference_sql, schema)
            except DryRunError as e:
                if result.attempts < config.MAX_READ_ATTEMPTS:
                    # back to read_ticket, with the error for the model to fix
                    return {"feedback": f"its SQL failed when run on a copy of the database ({e})"}
                result.finish(UNVERIFIABLE, [f"the reference fix failed on the copy: {e}"])
                return {"review": result}
            if spec.stated_row_count is not None and changes.total != spec.stated_row_count:
                result.finish(UNVERIFIABLE, [
                    f"the ticket says {spec.stated_row_count} rows, but the reading of it changes {changes.total}"
                ])
                return {"review": result}
            expected.append(changes)

        specified = {c for spec in result.intents for c in spec.specified_columns}
        result.reference_changes = expected[0]
        if not all(agree(expected[0], other, specified) for other in expected[1:]):
            result.finish(UNVERIFIABLE, [
                "two independent readings of the ticket change different rows - it can probably be read more than one way"
            ])
        return {"review": result}

    def compare_effects(state):
        """Compare effects, not SQL text."""
        result = state["review"]
        script, reference = result.script_changes, result.reference_changes
        if script.truncated and reference.truncated and script.total == reference.total:
            result.finish(UNVERIFIABLE, [f"both change {script.total:,} rows - too many to compare one by one"])
            return {"review": result}

        specified = {c for spec in result.intents for c in spec.specified_columns}
        result.issues = compare(script, reference, specified)
        if result.issues:
            result.finish(MISMATCH, summarise(result.issues))
        else:
            result.finish(MATCH, [f"the script changes exactly what the ticket asks for ({script.summary()})"])
        return {"review": result}

    # --- routing: stop as soon as there's a verdict, otherwise go on

    def after_policy(state):
        return END if state["review"].verdict else "dry_run_script"

    def after_dry_run(state):
        return END if state["review"].verdict else "find_similar"

    def after_reading(state):
        result = state["review"]
        if state.get("questions") and ask_requester and len(result.answers) < config.MAX_QUESTION_ROUNDS:
            return "ask_requester"
        return END if result.verdict else "run_readings"

    def after_running(state):
        if state["review"].verdict:
            return END
        return "read_ticket" if state.get("feedback") else "compare_effects"

    graph = StateGraph(State)
    graph.add_node("check_policy", check_policy)
    graph.add_node("dry_run_script", dry_run_script)
    graph.add_node("find_similar", find_similar)
    graph.add_node("read_ticket", read_ticket)
    graph.add_node("ask_requester", ask_requester_node)
    graph.add_node("run_readings", run_readings)
    graph.add_node("compare_effects", compare_effects)

    graph.add_edge(START, "check_policy")
    graph.add_conditional_edges("check_policy", after_policy, ["dry_run_script", END])
    graph.add_conditional_edges("dry_run_script", after_dry_run, ["find_similar", END])
    graph.add_edge("find_similar", "read_ticket")
    graph.add_conditional_edges("read_ticket", after_reading, ["ask_requester", "run_readings", END])
    graph.add_edge("ask_requester", "read_ticket")
    graph.add_conditional_edges("run_readings", after_running, ["read_ticket", "compare_effects", END])
    graph.add_edge("compare_effects", END)
    return graph.compile(checkpointer=checkpointer)


def run(graph, llm, review=None, answer=None, thread_id=None):
    """Start a review (pass `review`) or continue a paused one (pass `answer`).

    Returns (review, waiting). `waiting` is True when the run stopped to wait
    for the requester; continue it later with the same thread_id.
    """
    settings = {"recursion_limit": 50}
    if thread_id:
        settings["configurable"] = {"thread_id": thread_id}

    started = time.perf_counter()
    tokens_before = (getattr(llm, "input_tokens", 0), getattr(llm, "output_tokens", 0))
    start = {"review": review} if answer is None else Command(resume=answer)
    state = graph.invoke(start, settings)

    result = state["review"]
    result.stats = {
        "seconds": round(time.perf_counter() - started, 2),
        "input_tokens": getattr(llm, "input_tokens", 0) - tokens_before[0],
        "output_tokens": getattr(llm, "output_tokens", 0) - tokens_before[1],
    }
    return result, "__interrupt__" in state
