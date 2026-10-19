from conftest import FakeLLM, spec

from intentdiff.llm import LLMError
from intentdiff.review import BLOCKED, MATCH, MISMATCH, UNVERIFIABLE, review

TICKET = "Client 100 has a new email address: new@x.cz. Please update it."
GOOD = "UPDATE client SET email = 'new@x.cz' WHERE client_id = 100;"
READING = spec(GOOD, columns=["client.email"], quoted=["100", "new@x.cz"])


def run(db, schema, sql, *answers):
    llm = FakeLLM(*answers)
    return review(TICKET, sql, db, llm=llm, schema=schema), llm


def test_correct_script_matches(db, schema):
    result, _ = run(db, schema, GOOD, READING)
    assert result.verdict == MATCH


def test_wrong_client_is_a_mismatch(db, schema):
    result, _ = run(db, schema, "UPDATE client SET email = 'new@x.cz' WHERE client_id = 101;", READING)
    assert result.verdict == MISMATCH
    assert {i.kind for i in result.issues} == {"extra_row", "missing_row"}


def test_llm_never_sees_the_script(db, schema):
    script = "UPDATE client SET email = 'new@x.cz' WHERE client_id = 100 /* marker-7f3a */;"
    _, llm = run(db, schema, script, READING)
    assert llm.prompts
    assert all("marker-7f3a" not in p for p in llm.prompts)


def test_blocked_scripts_never_reach_the_llm(db, schema):
    result, llm = run(db, schema, "DROP TABLE client;", READING)
    assert result.verdict == BLOCKED
    assert llm.prompts == []


def test_llm_failure_is_never_a_pass(db, schema):
    result, _ = run(db, schema, GOOD, LLMError("rate limited"))
    assert result.verdict == UNVERIFIABLE


def test_ambiguous_ticket_is_escalated_with_questions(db, schema):
    unclear = spec("", ambiguities=["Which client called?"])
    result, _ = run(db, schema, GOOD, unclear)
    assert result.verdict == UNVERIFIABLE
    assert "Which client called?" in result.reasons


def test_two_readings_that_disagree_are_escalated(db, schema):
    other = spec("UPDATE client SET email = 'new@x.cz' WHERE client_id IN (100, 101);",
                 columns=["client.email"], quoted=["100"])
    result, _ = run(db, schema, GOOD, READING, other)
    assert result.verdict == UNVERIFIABLE


def test_made_up_quotes_are_rejected_and_retried(db, schema):
    invented = spec(GOOD, columns=["client.email"], quoted=["client 999"])
    result, llm = run(db, schema, GOOD, invented, READING)
    assert result.verdict == MATCH
    assert "rejected" in llm.prompts[1]


def test_stated_row_count_must_hold(db, schema):
    says_two = spec(GOOD, columns=["client.email"], rows=2)
    result, _ = run(db, schema, GOOD, says_two)
    assert result.verdict == UNVERIFIABLE
