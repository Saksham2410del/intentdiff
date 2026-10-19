import sqlite3

import pytest

from intentdiff.dryrun import DryRunError, _execute, dry_run


def test_update_is_recorded_with_before_and_after(db, schema):
    changes = dry_run(db, "UPDATE client SET email = 'new@x.cz' WHERE client_id = 100;", schema)
    assert changes.total == 1
    change = changes.rows[("client", (100,))]
    assert change.action == "update"
    assert change.before["email"] == "jan.100@seznam.cz"
    assert change.after["email"] == "new@x.cz"
    assert change.changed_columns == {"email"}


def test_delete_and_insert(db, schema):
    sql = """
        DELETE FROM trans WHERE trans_id = 2;
        INSERT INTO standing_order (account_id, bank_to, account_to, amount, purpose)
        VALUES (10, 'AB', '123456', 500, 'insurance');
    """
    changes = dry_run(db, sql, schema)
    actions = sorted(c.action for c in changes.rows.values())
    assert actions == ["delete", "insert"]


def test_setting_null_counts_as_a_change(db, schema):
    changes = dry_run(db, "UPDATE client SET phone = NULL WHERE client_id = 101;", schema)
    assert changes.total == 1


def test_updating_to_the_same_value_changes_nothing(db, schema):
    changes = dry_run(db, "UPDATE client SET email = 'jan.100@seznam.cz' WHERE client_id = 100;", schema)
    assert changes.total == 0


def test_original_database_is_untouched(db, schema):
    dry_run(db, "DELETE FROM trans;", schema)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM trans").fetchone()[0] == 2
    conn.close()


def test_foreign_keys_are_enforced(db, schema):
    # account 10 still has transactions, just like production would refuse this
    with pytest.raises(DryRunError, match="FOREIGN KEY"):
        dry_run(db, "DELETE FROM account WHERE account_id = 10;", schema)


def test_sqlite_itself_refuses_attach(tmp_path):
    # Even if the static check were bypassed, the authorizer stops it.
    path = tmp_path / "x.db"
    sqlite3.connect(path).close()
    with pytest.raises(DryRunError, match="not authorized"):
        _execute(path, f"ATTACH DATABASE '{tmp_path / 'evil.db'}' AS evil;", timeout=5)


def test_huge_changes_are_counted_not_collected(db, schema):
    changes = dry_run(db, "UPDATE client SET phone = NULL;", schema, max_rows=1)
    assert changes.truncated
    assert changes.total == 2  # client 100 already had no phone
