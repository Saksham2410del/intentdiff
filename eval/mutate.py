"""Generate plausible wrong versions of a correct script.

The operators are adapted from SQL mutation testing (Tuya et al., "Mutating
database queries", 2007) and picked to look like mistakes people really make
in data fixes: a typo in an ID, < instead of <=, a forgotten condition...

A mutant is NOT automatically "wrong". It is labelled by running it and
comparing its effect with the correct script, because some mutants happen to
change exactly the same rows on this data (the classic "equivalent mutant").
"""
import sqlglot
from sqlglot import exp

FLIPPED = {exp.LT: exp.LTE, exp.LTE: exp.LT, exp.GT: exp.GTE, exp.GTE: exp.GT}


def _each(stmt, node_type, keep=lambda node: True):
    """Yield (copy of stmt, matching node inside that copy), one node at a time."""
    total = sum(1 for n in stmt.find_all(node_type) if keep(n))
    for i in range(total):
        copy = stmt.copy()
        node = [n for n in copy.find_all(node_type) if keep(n)][i]
        yield copy, node


def _in_where(node):
    return node.find_ancestor(exp.Where) is not None


def wrong_id(stmt):
    for copy, lit in _each(stmt, exp.Literal, lambda n: n.is_int and _in_where(n)):
        lit.replace(exp.Literal.number(int(lit.this) + 1))
        yield copy


def swapped_digits(stmt):
    for copy, lit in _each(stmt, exp.Literal, lambda n: n.is_int and _in_where(n) and len(n.this) >= 3):
        digits = lit.this
        if digits[-1] != digits[-2]:
            lit.replace(exp.Literal.number(digits[:-2] + digits[-1] + digits[-2]))
            yield copy


def boundary(stmt):
    for copy, node in _each(stmt, tuple(FLIPPED)):
        node.replace(FLIPPED[type(node)](this=node.this, expression=node.expression))
        yield copy
    # BETWEEN a AND b written as >= a AND < b - forgets the last day
    for copy, node in _each(stmt, exp.Between):
        node.replace(exp.and_(
            exp.GTE(this=node.this.copy(), expression=node.args["low"].copy()),
            exp.LT(this=node.this.copy(), expression=node.args["high"].copy()),
        ))
        yield copy


def and_to_or(stmt):
    for copy, node in _each(stmt, exp.And, _in_where):
        node.replace(exp.Or(this=node.this, expression=node.expression))
        yield copy


def drop_condition(stmt):
    where = stmt.args.get("where")
    if where is None or not isinstance(where.this, exp.And):
        return
    parts = list(where.this.flatten())
    for i in range(len(parts)):
        copy = stmt.copy()
        rest = [p.copy() for j, p in enumerate(parts) if j != i]
        copy.set("where", exp.Where(this=exp.and_(*rest)))
        yield copy


def drop_where(stmt):
    if stmt.args.get("where") is not None:
        copy = stmt.copy()
        copy.set("where", None)
        yield copy


def drop_list_item(stmt):
    for copy, node in _each(stmt, exp.In, lambda n: len(n.expressions) >= 2):
        for i in range(len(node.expressions)):
            item_copy = copy.copy()
            target = [n for n in item_copy.find_all(exp.In) if n.sql() == node.sql()][0]
            target.set("expressions", [e for j, e in enumerate(target.expressions) if j != i])
            yield item_copy


def wrong_value(stmt):
    """Change a literal that is being written (SET or VALUES), not one used for matching."""
    def written(node):
        return not _in_where(node) and node.find_ancestor(exp.Subquery) is None

    for copy, lit in _each(stmt, exp.Literal, written):
        if lit.is_string:
            s = lit.this
            if len(s) < 2:
                continue
            mid = len(s) // 2
            new = s[:mid - 1] + s[mid] + s[mid - 1] + s[mid + 1:]  # two letters swapped
            if new == s:
                continue
            lit.replace(exp.Literal.string(new))
        elif lit.is_int:
            lit.replace(exp.Literal.number(int(lit.this) + 1))
        else:
            lit.replace(exp.Literal.number(float(lit.this) * 10))
        yield copy


OPERATORS = [
    ("wrong_id", wrong_id),
    ("swapped_digits", swapped_digits),
    ("boundary", boundary),
    ("and_to_or", and_to_or),
    ("drop_condition", drop_condition),
    ("drop_where", drop_where),
    ("drop_list_item", drop_list_item),
    ("wrong_value", wrong_value),
]


def _render(statements):
    return ";\n".join(s.sql(dialect="sqlite") for s in statements) + ";"


def mutants(sql):
    """Yield (operator name, mutated SQL). Each mutant changes exactly one thing."""
    statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    seen = {_render(statements)}
    for i, stmt in enumerate(statements):
        for name, operator in OPERATORS:
            for mutated in operator(stmt):
                text = _render(statements[:i] + [mutated] + statements[i + 1:])
                if text not in seen:
                    seen.add(text)
                    yield name, text
