import sqlglot

from eval.mutate import mutants

SQL = "UPDATE card SET type = 'gold' WHERE type = 'classic' AND issued_on < '1995-01-01' AND card_id IN (104, 747);"


def test_mutants_are_valid_and_unique():
    found = list(mutants(SQL))
    texts = [sql for _, sql in found]
    assert len(texts) == len(set(texts))
    for sql in texts:
        sqlglot.parse(sql, read="sqlite")  # raises if a mutant is broken


def test_expected_operators_fire():
    ops = {op for op, _ in mutants(SQL)}
    assert {"wrong_id", "boundary", "and_to_or", "drop_condition", "drop_where",
            "drop_list_item", "wrong_value"} <= ops


def test_between_becomes_an_exclusive_range():
    sql = "UPDATE trans SET purpose = 'interest' WHERE date BETWEEN '1997-01-01' AND '1997-06-30';"
    boundary = [s for op, s in mutants(sql) if op == "boundary"]
    assert any("< '1997-06-30'" in s for s in boundary)
