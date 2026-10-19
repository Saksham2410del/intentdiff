"""Command line entry point.

    python -m intentdiff check --ticket ticket.txt --sql fix.sql
    python -m intentdiff effect --sql fix.sql        # just show what a script changes (no LLM)
"""
import argparse
import json
import sys
from pathlib import Path

from intentdiff import config
from intentdiff.dryrun import DryRunError, dry_run
from intentdiff.history import TicketHistory
from intentdiff.review import render_markdown, render_text, review
from intentdiff.schema import load_schema


def main(argv=None):
    parser = argparse.ArgumentParser(prog="intentdiff")
    parser.add_argument("--db", type=Path, default=config.DB_PATH, help="SQLite database to dry-run against")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="compare a script with the ticket it is meant to fix")
    check.add_argument("--ticket", type=Path, required=True)
    check.add_argument("--sql", type=Path, required=True)
    check.add_argument("--no-history", action="store_true", help="don't show the model similar past tickets")
    output = check.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="print the full result as JSON")
    output.add_argument("--markdown", action="store_true", help="print a short Markdown report, e.g. for a PR comment")

    effect = sub.add_parser("effect", help="show what a script changes, without any LLM")
    effect.add_argument("--sql", type=Path, required=True)
    effect.add_argument("--rows", type=int, default=20)

    args = parser.parse_args(argv)
    if not args.db.exists():
        sys.exit(f"Database not found: {args.db}  (run scripts/build_db.py first)")
    schema = load_schema(args.db, config.NOTES_PATH)

    if args.command == "effect":
        try:
            changes = dry_run(args.db, args.sql.read_text(encoding="utf-8"), schema)
        except DryRunError as e:
            sys.exit(f"Blocked: {e}")
        print(changes.summary())
        for record in changes.to_records(args.rows):
            print(f"  {record['action']:6} {record['table']} {record['key']}  {', '.join(record['columns'])}")
        return

    result = review(
        args.ticket.read_text(encoding="utf-8"),
        args.sql.read_text(encoding="utf-8"),
        db_path=args.db,
        schema=schema,
        history=None if args.no_history else TicketHistory.load(),
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    elif args.markdown:
        print(render_markdown(result, args.sql.name))
    else:
        print(render_text(result))
    # non-zero exit code so this can sit in a CI job or a pre-execution hook
    sys.exit(0 if result.verdict == "MATCH" else 1)


if __name__ == "__main__":
    main()
