"""The result of a check, and the ways to show it.

The steps themselves live in graph.py: static policy -> dry run -> similar
past tickets -> ticket reading -> comparison -> verdict. The verdict is always
decided in plain code. The LLM only proposes a reference fix; it never gets to
say "this looks fine".
"""
from dataclasses import dataclass, field

from intentdiff import config
from intentdiff.llm import GeminiClient
from intentdiff.schema import load_schema

MATCH = "MATCH"                  # script does exactly what the ticket asks
MISMATCH = "MISMATCH"            # script does something different - details in `issues`
UNVERIFIABLE = "UNVERIFIABLE"    # couldn't tell; a person has to look
BLOCKED = "BLOCKED"              # script breaks the rules or fails on a copy of the data


@dataclass
class Review:
    ticket: str
    sql: str
    verdict: str = ""
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    intents: list = field(default_factory=list)
    script_changes: object = None
    reference_changes: object = None
    examples: list = field(default_factory=list)   # ids of the past tickets shown to the model
    answers: list = field(default_factory=list)    # the requester's answers to our questions
    attempts: int = 0                              # times the ticket was read in the last round
    stats: dict = field(default_factory=dict)

    def finish(self, verdict, reasons):
        self.verdict = verdict
        self.reasons = list(reasons)
        return self

    def to_dict(self, max_rows=50):
        return {
            "verdict": self.verdict,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "issues": [i.to_dict() for i in self.issues[:max_rows]],
            "issue_count": len(self.issues),
            "intents": [s.model_dump() for s in self.intents],
            "script_effect": self.script_changes.summary() if self.script_changes else None,
            "script_changes": self.script_changes.to_records(max_rows) if self.script_changes else [],
            "expected_effect": self.reference_changes.summary() if self.reference_changes else None,
            "similar_tickets": self.examples,
            "answers": self.answers,
            "attempts": self.attempts,
            "stats": self.stats,
        }


def review(ticket, sql, db_path=None, llm=None, schema=None, history=None, samples=config.INTENT_SAMPLES):
    """Run the whole check once, start to finish, and return the finished Review.

    Pass a TicketHistory as `history` to show the model similar past tickets.
    An ambiguous ticket ends as UNVERIFIABLE here - pausing to ask the requester
    is only done by the web app, where somebody can actually answer.
    """
    from intentdiff.graph import build_graph, run  # graph.py imports this module, so import it late

    db_path = db_path or config.DB_PATH
    schema = schema or load_schema(db_path, config.NOTES_PATH)
    llm = llm or GeminiClient()
    graph = build_graph(db_path, schema, llm, history=history, samples=samples)
    result, _ = run(graph, llm, Review(ticket=ticket, sql=sql))
    return result


def summarise(issues):
    counts = {}
    for issue in issues:
        counts[issue.kind] = counts.get(issue.kind, 0) + 1
    labels = {
        "extra_row": "rows changed that the ticket doesn't ask for",
        "missing_row": "rows the ticket asks for that the script doesn't touch",
        "wrong_value": "values that differ from what the ticket asks",
        "unrequested_column": "extra columns changed",
        "different_action": "rows where the script does a different kind of change",
        "size": "the overall size of the change is different",
    }
    lines = [f"{n} {labels[kind]}" for kind, n in counts.items()]
    return lines + [i.describe() for i in issues[:5]]


def render_text(result):
    """Plain-text report for the command line."""
    out = [f"Verdict: {result.verdict}", ""]
    out += [f"  {r}" for r in result.reasons]
    if result.warnings:
        out += ["", "Warnings:"] + [f"  {w}" for w in result.warnings]
    if result.script_changes is not None:
        out += ["", f"Script effect:   {result.script_changes.summary()}"]
    if result.reference_changes is not None:
        out += [f"Ticket expects:  {result.reference_changes.summary()}"]
    if result.intents:
        out += ["", f"Reading of the ticket: {result.intents[0].intent_summary}"]
    if result.examples:
        out += [f"Similar past tickets shown to the model: {', '.join(result.examples)}"]
    if result.attempts > 1:
        out += [f"The ticket was read {result.attempts} times (earlier SQL failed on the copy)"]
    out += ["", f"({result.stats.get('seconds', 0)}s, "
                f"{result.stats.get('input_tokens', 0)} in / {result.stats.get('output_tokens', 0)} out tokens)"]
    return "\n".join(out)


def render_markdown(result, title):
    """Short report for a pull request comment."""
    out = [f"### {title}: `{result.verdict}`", ""]
    out += [f"- {r}" for r in result.reasons]
    out += [f"- warning: {w}" for w in result.warnings]
    out.append("")
    if result.script_changes is not None:
        out.append(f"**Script changes:** {result.script_changes.summary()}  ")
    if result.reference_changes is not None:
        out.append(f"**Ticket asks for:** {result.reference_changes.summary()}  ")
    if result.intents:
        out.append(f"**Reading of the ticket:** {result.intents[0].intent_summary}")
    if result.issues:
        out += ["", "<details><summary>All differences</summary>", ""]
        out += [f"- {i.describe()}" for i in result.issues[:50]]
        if len(result.issues) > 50:
            out.append(f"- ... and {len(result.issues) - 50} more")
        out += ["", "</details>"]
    return "\n".join(out) + "\n"
