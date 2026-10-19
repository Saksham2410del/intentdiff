"""API tests. These need the real database, so they skip in CI."""
import pytest
from conftest import FakeLLM, spec

from intentdiff import config

pytestmark = pytest.mark.skipif(not config.DB_PATH.exists(), reason="needs data/bank.db - run scripts/build_db.py")

TICKET = "Client 2045 gave us petr.dvorak@seznam.cz as their email, please add it."
GOOD = "UPDATE client SET email = 'petr.dvorak@seznam.cz' WHERE client_id = 2045;"
WRONG = "UPDATE client SET email = 'petr.dvorak@seznam.cz' WHERE client_id = 2054;"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    folder = tmp_path_factory.mktemp("store")
    config.REVIEWS_DB = folder / "reviews.db"
    config.CHECKPOINTS_DB = folder / "checkpoints.db"
    import app.server as server
    return server


@pytest.fixture
def client(server):
    from fastapi.testclient import TestClient

    server.llm = FakeLLM(spec(GOOD, columns=["client.email"], quoted=["2045"]))
    return TestClient(server.app)


def test_dry_run_endpoint_blocks_ddl(client):
    assert client.post("/dry-run", json={"sql": "DROP TABLE client;"}).status_code == 422


def test_review_and_approve(client):
    body = client.post("/reviews", json={"ticket": TICKET, "sql": GOOD}).json()
    assert body["verdict"] == "MATCH"

    decision = {"reviewer": "dba", "approve": True, "sql_sha256": body["sql_sha256"]}
    assert client.post(f"/reviews/{body['id']}/decision", json=decision).status_code == 200
    assert len(client.get(f"/reviews/{body['id']}").json()["decisions"]) == 1


def test_approval_is_tied_to_the_reviewed_script(client):
    body = client.post("/reviews", json={"ticket": TICKET, "sql": GOOD}).json()
    decision = {"reviewer": "dba", "approve": True, "sql_sha256": "hash-of-an-edited-script"}
    assert client.post(f"/reviews/{body['id']}/decision", json=decision).status_code == 409


def test_approving_a_mismatch_needs_a_reason(client):
    body = client.post("/reviews", json={"ticket": TICKET, "sql": WRONG}).json()
    assert body["verdict"] == "MISMATCH"

    decision = {"reviewer": "dba", "approve": True, "sql_sha256": body["sql_sha256"]}
    assert client.post(f"/reviews/{body['id']}/decision", json=decision).status_code == 422

    decision["reason"] = "requester confirmed 2054 is the right client"
    assert client.post(f"/reviews/{body['id']}/decision", json=decision).status_code == 200


def test_ambiguous_ticket_pauses_and_continues_with_the_answer(server):
    from fastapi.testclient import TestClient

    unclear = spec("", ambiguities=["Which client called?"])
    server.llm = FakeLLM(unclear, unclear, spec(GOOD, columns=["client.email"], quoted=["2045"]))
    client = TestClient(server.app)

    body = client.post("/reviews", json={"ticket": "Please fix the email of the client who called.", "sql": GOOD}).json()
    assert body["verdict"] == "UNVERIFIABLE"
    assert body["waiting_for_answer"]

    resumed = client.post(f"/reviews/{body['id']}/answer", json={"answer": "client 2045"}).json()
    assert resumed["verdict"] == "MATCH"
    assert resumed["answers"] == ["client 2045"]
    # a finished review can't be answered again
    assert client.post(f"/reviews/{resumed['id']}/answer", json={"answer": "x"}).status_code == 409
