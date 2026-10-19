from conftest import FakeLLM, spec
from langgraph.checkpoint.memory import InMemorySaver

from intentdiff.graph import build_graph, run, serializer, sqlite_checkpointer
from intentdiff.history import PastTicket, TicketHistory
from intentdiff.review import MATCH, UNVERIFIABLE, Review

TICKET = "Client 100 has a new email address: new@x.cz. Please update it."
GOOD = "UPDATE client SET email = 'new@x.cz' WHERE client_id = 100;"
READING = spec(GOOD, columns=["client.email"], quoted=["100", "new@x.cz"])
# passes every static check, but district 99 doesn't exist, so the foreign key fails on the copy
BROKEN = spec("UPDATE client SET email = 'new@x.cz', district_id = 99 WHERE client_id = 100;",
              columns=["client.email"], quoted=["100"])
UNCLEAR = spec("", ambiguities=["Which client do you mean?"])


def start(graph, llm, thread_id=None):
    return run(graph, llm, Review(ticket=TICKET, sql=GOOD), thread_id=thread_id)


def test_failing_reference_sql_goes_back_to_the_model_with_the_error(db, schema):
    llm = FakeLLM(BROKEN, READING)
    result, _ = start(build_graph(db, schema, llm), llm)
    assert result.verdict == MATCH
    assert result.attempts == 2
    assert "FOREIGN KEY" in llm.prompts[-1]


def test_gives_up_when_the_sql_keeps_failing(db, schema):
    llm = FakeLLM(BROKEN)
    result, _ = start(build_graph(db, schema, llm), llm)
    assert result.verdict == UNVERIFIABLE
    assert result.attempts == 3
    assert "failed on the copy" in result.reasons[0]


def test_similar_past_tickets_are_shown_to_the_model(db, schema):
    # BM25 needs a few documents before a word counts as rare enough to score
    past = [
        PastTicket("H1", "Client 7 changed their email to a@b.cz", "UPDATE client SET email = 'a@b.cz' WHERE client_id = 7;"),
        PastTicket("H2", "Delete the leasing standing order on account 5", "DELETE FROM standing_order WHERE order_id = 1;"),
        PastTicket("H3", "Extend loan 6 by 12 months", "UPDATE loan SET duration_months = duration_months + 12 WHERE loan_id = 6;"),
    ]
    history = TicketHistory(past, vectors=False)
    llm = FakeLLM(READING)
    result, _ = start(build_graph(db, schema, llm, history=history), llm)
    assert result.verdict == MATCH
    assert result.examples[0] == "H1"
    assert "WHERE client_id = 7" in llm.prompts[0]


def test_ambiguous_ticket_waits_for_the_requester(db, schema):
    llm = FakeLLM(UNCLEAR, UNCLEAR, READING)
    graph = build_graph(db, schema, llm, ask_requester=True, checkpointer=InMemorySaver(serde=serializer()))

    result, waiting = start(graph, llm, thread_id="t1")
    assert waiting
    assert result.verdict == UNVERIFIABLE
    assert "Which client do you mean?" in result.reasons

    result, waiting = run(graph, llm, answer="client 100, the one who called", thread_id="t1")
    assert not waiting
    assert result.verdict == MATCH
    assert result.answers == ["client 100, the one who called"]
    assert "the one who called" in llm.prompts[-1]


def test_paused_review_survives_a_restart(db, schema, tmp_path):
    path = tmp_path / "checkpoints.db"
    llm = FakeLLM(UNCLEAR, UNCLEAR, READING)
    graph = build_graph(db, schema, llm, ask_requester=True, checkpointer=sqlite_checkpointer(path))
    _, waiting = start(graph, llm, thread_id="t2")
    assert waiting

    # a brand new graph and checkpointer, as after restarting the server
    graph = build_graph(db, schema, llm, ask_requester=True, checkpointer=sqlite_checkpointer(path))
    result, _ = run(graph, llm, answer="client 100", thread_id="t2")
    assert result.verdict == MATCH
    assert result.script_changes.total == 1


def test_without_a_person_to_ask_ambiguity_ends_the_review(db, schema):
    llm = FakeLLM(UNCLEAR)
    result, waiting = start(build_graph(db, schema, llm), llm)
    assert not waiting
    assert result.verdict == UNVERIFIABLE
