"""Static checks that run before any SQL touches even a copy of the database.

The same rules apply to the engineer's script and to the SQL the LLM writes -
neither one is trusted.
"""
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

WRITES = (exp.Update, exp.Delete, exp.Insert)
# BEGIN/COMMIT around a fix is normal, and a SELECT doesn't change anything
HARMLESS = (exp.Select, exp.Transaction, exp.Commit)
# A dry run is only useful if running the script again gives the same result
NOT_REPEATABLE = (exp.Rand, exp.CurrentDate, exp.CurrentTime, exp.CurrentTimestamp)
BANNED_FUNCTIONS = {"random", "randomblob"}


@dataclass
class PolicyResult:
    problems: list[str] = field(default_factory=list)
    written_tables: set[str] = field(default_factory=set)
    statements: int = 0  # number of data-changing statements

    @property
    def ok(self):
        return not self.problems


def check_sql(sql, schema):
    result = PolicyResult()
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except sqlglot.errors.ParseError as e:
        result.problems.append(f"could not parse the SQL ({str(e).splitlines()[0]})")
        return result

    if not statements:
        result.problems.append("the script is empty")

    for stmt in statements:
        if isinstance(stmt, HARMLESS):
            continue
        if not isinstance(stmt, WRITES):
            result.problems.append(f"{stmt.key.upper()} is not allowed in a data fix")
            continue

        result.statements += 1
        target = _target_table(stmt)
        result.written_tables.add(target)

        for name in _referenced_tables(stmt):
            if name not in schema.tables:
                result.problems.append(f"unknown table '{name}'")
        if target in schema.tables and not schema.tables[target].primary_key:
            result.problems.append(f"'{target}' has no primary key, so its changes can't be tracked")

        if _is_not_repeatable(stmt):
            result.problems.append(
                "uses the current time or random values, so a dry run can't predict the real effect"
            )

    return result


def _target_table(stmt):
    target = stmt.this
    if isinstance(target, exp.Schema):  # INSERT INTO t (a, b) ...
        target = target.this
    return target.name.lower()


def _referenced_tables(stmt):
    # CTE names show up as tables in sqlglot, so leave them out
    ctes = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    return {t.name.lower() for t in stmt.find_all(exp.Table)} - ctes


def _is_not_repeatable(stmt):
    if stmt.find(*NOT_REPEATABLE):
        return True
    for node in stmt.find_all(exp.Literal):
        if node.is_string and node.this.strip().lower() == "now":
            return True
    for node in stmt.find_all(exp.Anonymous):
        if node.name.lower() in BANNED_FUNCTIONS:
            return True
    return False
