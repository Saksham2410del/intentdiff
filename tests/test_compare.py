from intentdiff.baselines import ticket_ids
from intentdiff.changes import agree, compare
from intentdiff.dryrun import dry_run

FIX = "UPDATE client SET email = 'new@x.cz' WHERE client_id = 100;"


def kinds(issues):
    return sorted(i.kind for i in issues)


def test_identical_effects_have_no_issues(db, schema):
    a = dry_run(db, FIX, schema)
    b = dry_run(db, "UPDATE client SET email = 'new@x.cz' WHERE client_id IN (100);", schema)
    assert compare(a, b, {"client.email"}) == []
    assert agree(a, b, {"client.email"})


def test_wrong_id_shows_extra_and_missing_row(db, schema):
    expected = dry_run(db, FIX, schema)
    script = dry_run(db, "UPDATE client SET email = 'new@x.cz' WHERE client_id = 101;", schema)
    assert kinds(compare(script, expected, {"client.email"})) == ["extra_row", "missing_row"]


def test_wrong_value_on_a_specified_column(db, schema):
    expected = dry_run(db, FIX, schema)
    script = dry_run(db, "UPDATE client SET email = 'new@x.com' WHERE client_id = 100;", schema)
    issues = compare(script, expected, {"client.email"})
    assert kinds(issues) == ["wrong_value"]
    assert issues[0].script_value == "new@x.com"


def test_extra_column_is_flagged(db, schema):
    expected = dry_run(db, FIX, schema)
    script = dry_run(db, "UPDATE client SET email = 'new@x.cz', phone = NULL WHERE client_id = 101 OR client_id = 100;", schema)
    assert "unrequested_column" not in kinds(compare(script, expected, {"client.email"}))  # 101 is an extra row anyway

    script = dry_run(db, "UPDATE client SET email = 'new@x.cz', district_id = 4 WHERE client_id = 100;", schema)
    assert kinds(compare(script, expected, {"client.email"})) == ["unrequested_column"]


def test_size_mismatch_when_too_big_to_compare(db, schema):
    expected = dry_run(db, FIX, schema)
    script = dry_run(db, "UPDATE client SET district_id = 1;", schema, max_rows=1)
    assert kinds(compare(script, expected, {"client.email"})) == ["size"]


def test_ticket_ids_skip_dates_and_amounts():
    text = "Client 2045 paid 1,500.50 CZK on 1997-03-10. Also check account 704."
    assert ticket_ids(text) == {2045, 704}
