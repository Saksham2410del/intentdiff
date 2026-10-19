"""MCP server, so an AI coding agent (Claude Code, Cursor, ...) can check a
data fix against its ticket before it runs anything.

    python -m intentdiff.mcp_server        # talks MCP over stdin/stdout

Tools:
    check_data_fix(ticket, sql)   the full check: verdict plus every difference
    preview_effect(sql)           what the script would change - no LLM involved
    describe_schema()             tables and columns of the bank database

Everything here is read-only: scripts only ever run on throwaway copies.
The agent gets a verdict, never the power to approve - that stays with a person.
"""
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from intentdiff import config
from intentdiff.dryrun import DryRunError, dry_run
from intentdiff.history import TicketHistory
from intentdiff.llm import GeminiClient
from intentdiff.review import review
from intentdiff.schema import load_schema

server = MCPServer(
    "intentdiff",
    instructions=(
        "Call check_data_fix before running any UPDATE, DELETE or INSERT on the bank database. "
        "Only run a script when the verdict is MATCH and a person has approved it. "
        "If the verdict is UNVERIFIABLE, pass the questions on to the user instead of answering them yourself."
    ),
)
READ_ONLY = ToolAnnotations(read_only_hint=True)

# Loaded on first use so the server starts fast. Tests swap these out.
db_path = config.DB_PATH
schema = None
llm = None
history = None


def _load():
    global schema, llm, history
    if schema is None:
        schema = load_schema(db_path, config.NOTES_PATH)
    if llm is None:
        llm = GeminiClient()
    if history is None:
        history = TicketHistory.load()


@server.tool(annotations=READ_ONLY)
def check_data_fix(ticket: str, sql: str) -> dict:
    """Check that a SQL data-fix script changes exactly what its change ticket asks for.

    Pass the ticket text exactly as the requester wrote it - don't summarise or
    "clarify" it - and the script you intend to run. The script is dry-run on a
    copy of the database and compared with an independent reading of the ticket.

    verdict:
      MATCH         the script changes exactly what the ticket asks for
      MISMATCH      it changes something else; see "differences"
      UNVERIFIABLE  the ticket is unclear or couldn't be checked; ask the user the questions in "reasons"
      BLOCKED       the script breaks the rules (DDL, PRAGMA, now(), ...) or fails on the copy
    """
    _load()
    result = review(ticket, sql, db_path=db_path, llm=llm, schema=schema, history=history)
    # The model's own reference SQL is left out on purpose. If an agent could
    # copy it, the two independent versions of the fix would become one.
    return {
        "verdict": result.verdict,
        "reasons": result.reasons,
        "warnings": result.warnings,
        "differences": [i.describe() for i in result.issues[:30]],
        "difference_count": len(result.issues),
        "script_effect": result.script_changes.summary() if result.script_changes else None,
        "expected_effect": result.reference_changes.summary() if result.reference_changes else None,
    }


@server.tool(annotations=READ_ONLY)
def preview_effect(sql: str) -> dict:
    """Show which rows a SQL script would change, by running it on a throwaway copy of the database.

    No LLM is involved and nothing real is modified. Useful for looking at a
    script's effect before asking for the full check.
    """
    _load()
    try:
        changes = dry_run(db_path, sql, schema)
    except DryRunError as e:
        return {"blocked": str(e)}
    return {"summary": changes.summary(), "total": changes.total, "rows": changes.to_records(20)}


@server.tool(annotations=READ_ONLY)
def describe_schema() -> str:
    """Tables, columns, keys and allowed values of the bank database."""
    _load()
    return schema.describe()


if __name__ == "__main__":
    server.run()
