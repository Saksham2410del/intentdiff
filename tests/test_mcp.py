import pytest
from conftest import FakeLLM, spec

from intentdiff import mcp_server
from intentdiff.history import TicketHistory

TICKET = "Client 100 has a new email address: new@x.cz. Please update it."
GOOD = "UPDATE client SET email = 'new@x.cz' WHERE client_id = 100;"
READING = spec(GOOD, columns=["client.email"], quoted=["100", "new@x.cz"])


@pytest.fixture
def tools(db, schema, monkeypatch):
    monkeypatch.setattr(mcp_server, "db_path", db)
    monkeypatch.setattr(mcp_server, "schema", schema)
    monkeypatch.setattr(mcp_server, "llm", FakeLLM(READING))
    monkeypatch.setattr(mcp_server, "history", TicketHistory.load(vectors=False))
    return mcp_server


def test_check_data_fix(tools):
    assert tools.check_data_fix(TICKET, GOOD)["verdict"] == "MATCH"

    report = tools.check_data_fix(TICKET, GOOD.replace("100", "101"))
    assert report["verdict"] == "MISMATCH"
    assert report["difference_count"] == 2


def test_agent_never_gets_the_reference_sql(tools):
    report = tools.check_data_fix(TICKET, GOOD.replace("100", "101"))
    assert GOOD not in str(report)


def test_preview_effect_and_blocking(tools):
    assert tools.preview_effect(GOOD)["total"] == 1
    assert "blocked" in tools.preview_effect("DROP TABLE client;")


def test_tools_are_registered_as_read_only():
    tools = {t.name: t for t in mcp_server.server._tool_manager.list_tools()}
    assert set(tools) == {"check_data_fix", "preview_effect", "describe_schema"}
    assert all(t.annotations.read_only_hint for t in tools.values())
