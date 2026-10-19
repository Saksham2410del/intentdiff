"""Check every data fix changed in a pull request. Used by the GitHub Action.

Each fix lives in fixes/ as two files with the same name:

    fixes/INC-2031.txt    the ticket, copied from the ticketing system
    fixes/INC-2031.sql    the script that should carry it out

    python scripts/check_pr_fixes.py origin/main > comment.md

Prints a Markdown report (the Action posts it as a PR comment) and exits with
1 if any script didn't get a MATCH, so the check shows up red on the PR.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # so `intentdiff` imports when run as a script

from intentdiff import config  # noqa: E402
from intentdiff.history import TicketHistory  # noqa: E402
from intentdiff.review import MATCH, render_markdown, review  # noqa: E402
from intentdiff.schema import load_schema  # noqa: E402


def changed_fixes(base):
    """Names of the fixes whose ticket or script changed since `base`."""
    out = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=AM", f"{base}...HEAD", "--", "fixes/"],
        capture_output=True, text=True, check=True, cwd=ROOT,
    ).stdout
    names = {Path(line).stem for line in out.splitlines() if line.endswith((".sql", ".txt"))}
    return sorted(names)


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    names = changed_fixes(base)
    if not names:
        print("No data fixes changed in this pull request.")
        return

    schema = load_schema(config.DB_PATH, config.NOTES_PATH)
    history = TicketHistory.load()
    print("## IntentDiff\n")
    all_match = True
    for name in names:
        ticket, script = ROOT / "fixes" / f"{name}.txt", ROOT / "fixes" / f"{name}.sql"
        if not ticket.exists() or not script.exists():
            print(f"### {name}: missing file\n\nEvery fix needs both `{name}.txt` (the ticket) and `{name}.sql`.\n")
            all_match = False
            continue
        result = review(ticket.read_text(encoding="utf-8"), script.read_text(encoding="utf-8"),
                        schema=schema, history=history)
        print(render_markdown(result, f"{name}.sql"))
        all_match = all_match and result.verdict == MATCH

    print("---\nA MATCH still needs a human approval on this PR. "
          "Anything else goes back to the author, or needs a written reason to override.")
    sys.exit(0 if all_match else 1)


if __name__ == "__main__":
    main()
