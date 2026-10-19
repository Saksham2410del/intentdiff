import pytest

from intentdiff.policy import check_sql


def test_simple_update_is_allowed(schema):
    result = check_sql("UPDATE client SET email = 'a@b.cz' WHERE client_id = 100;", schema)
    assert result.ok
    assert result.written_tables == {"client"}
    assert result.statements == 1


def test_begin_commit_and_select_are_fine(schema):
    sql = "BEGIN; SELECT * FROM client; UPDATE client SET phone = NULL WHERE client_id = 100; COMMIT;"
    assert check_sql(sql, schema).ok


@pytest.mark.parametrize("sql", [
    "DROP TABLE client;",
    "PRAGMA foreign_keys = OFF;",
    "ATTACH DATABASE 'other.db' AS other;",
    "CREATE TABLE x (a INTEGER);",
])
def test_non_data_statements_are_blocked(schema, sql):
    assert not check_sql(sql, schema).ok


@pytest.mark.parametrize("sql", [
    "UPDATE account SET closed_on = date('now') WHERE account_id = 10;",
    "UPDATE account SET closed_on = CURRENT_DATE WHERE account_id = 10;",
    "UPDATE trans SET amount = random() WHERE trans_id = 1;",
])
def test_non_repeatable_sql_is_blocked(schema, sql):
    result = check_sql(sql, schema)
    assert not result.ok
    assert "current time or random" in result.problems[0]


def test_unknown_table(schema):
    result = check_sql("UPDATE customers SET email = NULL WHERE id = 1;", schema)
    assert result.problems == ["unknown table 'customers'"]


def test_cte_names_are_not_mistaken_for_tables(schema):
    sql = """
        WITH old AS (SELECT client_id FROM client WHERE birth_date < '1960-01-01')
        UPDATE client SET phone = NULL WHERE client_id IN (SELECT client_id FROM old);
    """
    assert check_sql(sql, schema).ok


def test_garbage_is_reported_not_raised(schema):
    assert not check_sql("UPDATE SET WHERE;;(", schema).ok
