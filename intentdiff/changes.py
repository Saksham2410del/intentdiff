"""What a script did to the data, and how two such effects differ."""
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class RowChange:
    table: str
    key: tuple
    action: str               # "insert", "update" or "delete"
    before: dict | None = None
    after: dict | None = None

    @property
    def changed_columns(self):
        if self.action == "update":
            return {c for c in self.after if self.after[c] != self.before[c]}
        return set((self.after or self.before).keys())


@dataclass
class ChangeSet:
    rows: dict = field(default_factory=dict)        # (table, key) -> RowChange
    counts: Counter = field(default_factory=Counter)
    truncated: bool = False  # too many rows to keep; only `counts` is complete

    def add(self, change):
        self.rows[(change.table, change.key)] = change
        self.counts[change.table] += 1

    @property
    def total(self):
        return sum(self.counts.values())

    def same_as(self, other):
        """Exactly the same effect, every column included. Used to label eval cases."""
        if self.truncated or other.truncated:
            return self.truncated and other.truncated and self.counts == other.counts
        if self.rows.keys() != other.rows.keys():
            return False
        return all(
            a.action == b.action and a.after == b.after
            for a, b in ((self.rows[k], other.rows[k]) for k in self.rows)
        )

    def summary(self):
        if not self.total:
            return "no rows changed"
        actions = Counter((c.table, c.action) for c in self.rows.values())
        if self.truncated:
            return ", ".join(f"{t}: {n:,} rows" for t, n in sorted(self.counts.items()))
        return ", ".join(f"{t}: {n} {a}d" if a != "insert" else f"{t}: {n} inserted"
                         for (t, a), n in sorted(actions.items()))

    def to_records(self, limit=50):
        out = []
        for change in list(self.rows.values())[:limit]:
            out.append({
                "table": change.table,
                "key": list(change.key),
                "action": change.action,
                "columns": sorted(change.changed_columns),
                "before": change.before,
                "after": change.after,
            })
        return out


@dataclass
class Issue:
    kind: str                 # extra_row / missing_row / different_action / wrong_value / unrequested_column / size
    table: str
    key: tuple = ()
    column: str | None = None
    script_value: object = None
    expected_value: object = None

    def describe(self):
        where = f"{self.table} {self._key_text()}"
        if self.kind == "extra_row":
            return f"{where}: changed by the script, but the ticket doesn't ask for it"
        if self.kind == "missing_row":
            return f"{where}: the ticket asks for this change, but the script doesn't make it"
        if self.kind == "different_action":
            return f"{where}: script does {self.script_value}, ticket implies {self.expected_value}"
        if self.kind == "wrong_value":
            return f"{where}: {self.column} becomes {self.script_value!r}, expected {self.expected_value!r}"
        if self.kind == "unrequested_column":
            return f"{where}: script also changes {self.column}, which the ticket doesn't mention"
        return f"{self.table}: script changes {self.script_value:,} rows, the ticket implies {self.expected_value:,}"

    def _key_text(self):
        return ",".join(str(k) for k in self.key)

    def to_dict(self):
        return {**self.__dict__, "key": list(self.key), "text": self.describe()}


def compare(script, expected, specified_columns):
    """Every way the script's effect differs from the expected effect.

    `specified_columns` are the "table.column" names whose new value the ticket
    actually states. Only those get their values compared - for other columns
    we can't know what the requester wanted.
    """
    if script.truncated or expected.truncated:
        if script.total != expected.total:
            return [Issue("size", "*", script_value=script.total, expected_value=expected.total)]
        return []  # the caller decides what to do with two huge, same-size changes

    issues = []
    for table, key in script.rows.keys() - expected.rows.keys():
        issues.append(Issue("extra_row", table, key))
    for table, key in expected.rows.keys() - script.rows.keys():
        issues.append(Issue("missing_row", table, key))

    for row_id in script.rows.keys() & expected.rows.keys():
        mine, ref = script.rows[row_id], expected.rows[row_id]
        table, key = row_id
        if mine.action != ref.action:
            issues.append(Issue("different_action", table, key,
                                script_value=mine.action, expected_value=ref.action))
            continue
        if mine.action == "delete":
            continue

        wanted = {c.split(".", 1)[1] for c in specified_columns if c.startswith(table + ".")}
        if mine.action == "update":
            for col in sorted(mine.changed_columns - ref.changed_columns - wanted):
                issues.append(Issue("unrequested_column", table, key, column=col,
                                    script_value=mine.after[col], expected_value=mine.before[col]))
        for col in sorted(wanted):
            if mine.after.get(col) != ref.after.get(col):
                issues.append(Issue("wrong_value", table, key, column=col,
                                    script_value=mine.after.get(col), expected_value=ref.after.get(col)))

    return sorted(issues, key=lambda i: (i.kind, i.table, i.key, i.column or ""))


def agree(a, b, specified_columns):
    """Do two effects match in both directions?"""
    return not compare(a, b, specified_columns) and not compare(b, a, specified_columns)
