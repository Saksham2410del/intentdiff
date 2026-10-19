"""Build the evaluation cases and score each reviewer on them.

    python -m eval.run_eval                                    # non-AI baselines, no API key needed
    python -m eval.run_eval --systems b2,b3,intentdiff --split test
    python -m eval.run_eval --systems intentdiff,intentdiff-norag   # does retrieval actually help?

Every case is labelled by execution: a script is "match" if its effect on the
database is identical to the gold script's effect, otherwise "mismatch".
Nobody - human or LLM - decides the labels by reading the SQL.
"""
import argparse
import json
import math
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import yaml

from eval.mutate import mutants
from intentdiff import config
from intentdiff.baselines import b0_lint, b1_blast_radius, b2_ticket_ids, b3_llm_only
from intentdiff.dryrun import DryRunError, dry_run
from intentdiff.history import TicketHistory
from intentdiff.llm import GeminiClient, LLMError
from intentdiff.review import BLOCKED, MATCH, MISMATCH, UNVERIFIABLE, review
from intentdiff.schema import load_schema

HERE = Path(__file__).parent
FLAGGED = (MISMATCH, BLOCKED)


@dataclass
class Case:
    ticket_id: str
    category: str
    split: str
    source: str   # gold / rewrite / bad / mutant:<operator> / ambiguous
    label: str    # match / mismatch / escalate
    ticket: str
    sql: str


def build_cases(tickets, db_path, schema):
    cases, failing = [], Counter()
    for t in tickets:
        base = dict(ticket_id=t["id"], category=t["category"], split=t["split"], ticket=t["ticket"])
        if t.get("expect") == "escalate":
            cases.append(Case(**base, source="ambiguous", label="escalate", sql=t["script"]))
            continue

        gold = dry_run(db_path, t["gold"], schema)
        variants = [("gold", t["gold"])]
        variants += [("rewrite", s) for s in t.get("rewrites", [])]
        variants += [("bad", s) for s in t.get("bad", [])]
        variants += [(f"mutant:{op}", s) for op, s in mutants(t["gold"])]

        for source, sql in variants:
            try:
                effect = dry_run(db_path, sql, schema)
            except DryRunError:
                # fails outright (e.g. a broken foreign key) - every reviewer blocks
                # these, so keeping them would only make all the numbers look better
                failing[source] += 1
                continue
            label = "match" if effect.same_as(gold) else "mismatch"
            if source == "rewrite" and label != "match":
                raise ValueError(f"{t['id']}: this rewrite doesn't have the same effect as the gold script:\n{sql}")
            cases.append(Case(**base, source=source, label=label, sql=sql))
    return cases, failing


def make_systems(names, db_path, schema):
    llm = history = None

    def get_llm():
        nonlocal llm
        llm = llm or GeminiClient()
        return llm

    def get_history():
        nonlocal history
        history = history or TicketHistory.load()
        return history

    def b3(case):
        try:
            return b3_llm_only(case.ticket, case.sql, db_path, schema, get_llm())
        except LLMError as e:
            return UNVERIFIABLE, [str(e)]

    def full(case, with_history=True):
        result = review(case.ticket, case.sql, db_path, llm=get_llm(), schema=schema,
                        history=get_history() if with_history else None)
        return result.verdict, result.reasons

    available = {
        "b0": lambda c: b0_lint(c.ticket, c.sql, db_path, schema),
        "b1": lambda c: b1_blast_radius(c.ticket, c.sql, db_path, schema),
        "b2": lambda c: b2_ticket_ids(c.ticket, c.sql, db_path, schema),
        "b3": b3,
        "intentdiff": full,
        # same pipeline without the similar past tickets, to measure what retrieval adds
        "intentdiff-norag": lambda c: full(c, with_history=False),
    }
    unknown = set(names) - set(available)
    if unknown:
        raise SystemExit(f"Unknown systems: {', '.join(sorted(unknown))}. Choose from {', '.join(available)}")
    return {name: available[name] for name in names}, lambda: llm


def wilson(k, n, z=1.96):
    """95% confidence interval for a proportion. Honest about small samples."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(k, n, with_ci=False):
    if n == 0:
        return "-"
    text = f"{k}/{n} ({100 * k / n:.0f}%)"
    if with_ci:
        lo, hi = wilson(k, n)
        text += f" [{100 * lo:.0f}-{100 * hi:.0f}%]"
    return text


def summarise(cases, verdicts, timings):
    wrong = [i for i, c in enumerate(cases) if c.label == "mismatch"]
    right = [i for i, c in enumerate(cases) if c.label == "match"]
    unclear = [i for i, c in enumerate(cases) if c.label == "escalate"]

    lines = [
        "| System | Unsafe passes (wrong script -> MATCH) | False alarms (correct script flagged) "
        "| Clean passes | Escalated | Ambiguous tickets escalated | Avg time |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, v in verdicts.items():
        lines.append("| {} | {} | {} | {} | {} | {} | {:.2f}s |".format(
            name,
            rate(sum(v[i] == MATCH for i in wrong), len(wrong), with_ci=True),
            rate(sum(v[i] in FLAGGED for i in right), len(right), with_ci=True),
            rate(sum(v[i] == MATCH for i in right), len(right)),
            rate(sum(x == UNVERIFIABLE for x in v), len(v)),
            rate(sum(v[i] == UNVERIFIABLE for i in unclear), len(unclear)),
            sum(timings[name]) / max(len(timings[name]), 1),
        ))

    # Unsafe passes per category - this is where the AI should (or shouldn't) earn its keep
    lines += ["", "Unsafe passes by ticket category:", "",
              "| Category | " + " | ".join(verdicts) + " |",
              "|---|" + "---|" * len(verdicts)]
    by_cat = defaultdict(list)
    for i in wrong:
        by_cat[cases[i].category].append(i)
    for cat in sorted(by_cat):
        idx = by_cat[cat]
        cells = [rate(sum(v[i] == MATCH for i in idx), len(idx)) for v in verdicts.values()]
        lines.append(f"| {cat} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--systems", default="b0,b1,b2")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="all")
    parser.add_argument("--tickets", type=Path, default=HERE / "tickets.yaml")
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--limit", type=int, help="only the first N cases (for quick checks)")
    args = parser.parse_args()

    schema = load_schema(args.db, config.NOTES_PATH)
    tickets = yaml.safe_load(args.tickets.read_text(encoding="utf-8"))
    if args.split != "all":
        tickets = [t for t in tickets if t["split"] == args.split]

    print(f"Building cases from {len(tickets)} tickets...")
    cases, failing = build_cases(tickets, args.db, schema)
    cases = cases[:args.limit] if args.limit else cases
    labels = Counter(c.label for c in cases)
    equivalent = sum(1 for c in cases if c.source.startswith("mutant") and c.label == "match")
    print(f"{len(cases)} cases: {dict(labels)}; {equivalent} mutants turned out equivalent; "
          f"{sum(failing.values())} variants skipped because they fail outright")

    systems, get_llm = make_systems(args.systems.split(","), args.db, schema)
    verdicts = {name: [] for name in systems}
    reasons = {name: [] for name in systems}
    timings = {name: [] for name in systems}
    for n, case in enumerate(cases, 1):
        for name, run in systems.items():
            started = time.perf_counter()
            verdict, why = run(case)
            timings[name].append(time.perf_counter() - started)
            verdicts[name].append(verdict)
            reasons[name].append(why)
        if n % 25 == 0:
            print(f"  {n}/{len(cases)}")

    out_dir = HERE / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True)
    with open(out_dir / "cases.jsonl", "w", encoding="utf-8") as f:
        for i, case in enumerate(cases):
            row = asdict(case)
            row["results"] = {name: {"verdict": verdicts[name][i], "reasons": reasons[name][i]} for name in systems}
            f.write(json.dumps(row, default=str) + "\n")

    report = summarise(cases, verdicts, timings)
    llm = get_llm()
    header = [
        f"# Evaluation run {out_dir.name}",
        "",
        f"- tickets: {len(tickets)} ({args.split} split), cases: {len(cases)} {dict(labels)}",
        f"- equivalent mutants (relabelled as match): {equivalent}",
        f"- variants skipped because they fail outright: {sum(failing.values())}",
        "- times reuse dry runs cached while labelling, so they understate a cold run",
    ]
    if llm is not None:
        header.append(f"- model: {llm.model}, API calls: {llm.calls}, cache hits: {llm.cache_hits}, "
                      f"tokens: {llm.input_tokens} in / {llm.output_tokens} out")
    (out_dir / "summary.md").write_text("\n".join(header) + "\n\n" + report + "\n", encoding="utf-8")
    print("\n" + report)
    print(f"\nSaved to {out_dir}")


if __name__ == "__main__":
    main()
