"""Run SQL on a throwaway copy of the database and record exactly what changed."""
import shutil
import sqlite3
import tempfile
import time
from collections import OrderedDict
from pathlib import Path

from intentdiff import config
from intentdiff.changes import ChangeSet, RowChange
from intentdiff.policy import check_sql
from intentdiff.schema import readonly_uri

# Everything else (ATTACH, PRAGMA, CREATE, DROP, ...) is refused by SQLite
# itself, even if something slipped past the static check.
ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_TRANSACTION,
    sqlite3.SQLITE_SAVEPOINT,
    sqlite3.SQLITE_RECURSIVE,
}
BANNED_FUNCTIONS = {"random", "randomblob", "load_extension"}

# Same database file + same SQL always gives the same effect (the policy bans
# anything non-deterministic), so results can be reused. Mostly helps the eval,
# where the same reference fix is dry-run once per script variant.
_cache = OrderedDict()
CACHE_SIZE = 256


class DryRunError(Exception):
    pass


def _authorizer(action, arg1, arg2, db_name, source):
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in BANNED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK if action in ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def dry_run(db_path, sql, schema, timeout=config.DRY_RUN_TIMEOUT_S, max_rows=config.MAX_DIFF_ROWS):
    """Apply `sql` to a copy of `db_path` and return the resulting ChangeSet.

    The original database is never opened for writing.
    """
    policy = check_sql(sql, schema)
    if not policy.ok:
        raise DryRunError("; ".join(policy.problems))

    stat = Path(db_path).stat()
    cache_key = (str(Path(db_path).resolve()), stat.st_mtime_ns, stat.st_size, sql, max_rows)
    if cache_key in _cache:
        _cache.move_to_end(cache_key)
        return _cache[cache_key]

    workdir = Path(tempfile.mkdtemp(prefix="intentdiff_"))
    snapshot = workdir / "snapshot.db"
    try:
        shutil.copyfile(db_path, snapshot)
        _execute(snapshot, sql, timeout)
        tables = schema.affected_tables(policy.written_tables)
        changes = _diff(db_path, snapshot, schema, tables, max_rows)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    _cache[cache_key] = changes
    if len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return changes


def _execute(path, sql, timeout):
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")  # same rules as production
        conn.set_authorizer(_authorizer)
        deadline = time.monotonic() + timeout
        conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
        conn.executescript(sql)
        conn.commit()
    except sqlite3.Error as e:
        if "interrupted" in str(e):
            raise DryRunError(f"took longer than {timeout}s") from e
        raise DryRunError(str(e)) from e
    finally:
        conn.close()


def _diff(base_path, snapshot_path, schema, tables, max_rows):
    conn = sqlite3.connect(readonly_uri(snapshot_path), uri=True)
    try:
        conn.execute("ATTACH DATABASE ? AS base", (readonly_uri(base_path),))
        changes = ChangeSet()
        for name in sorted(tables):
            _diff_table(conn, schema.tables[name], changes, max_rows)
        return changes
    finally:
        conn.close()


def _diff_table(conn, table, changes, max_rows):
    t = table.name
    if not table.primary_key:
        raise DryRunError(f"'{t}' has no primary key, so its changes can't be tracked")

    cols_a = ", ".join(f'a."{c}"' for c in table.columns)
    cols_b = ", ".join(f'b."{c}"' for c in table.columns)
    same_key = " AND ".join(f'a."{k}" = b."{k}"' for k in table.primary_key)
    others = [c for c in table.columns if c not in table.primary_key]

    queries = [
        ("delete", f'SELECT {cols_b} FROM base."{t}" b '
                   f'WHERE NOT EXISTS (SELECT 1 FROM main."{t}" a WHERE {same_key})'),
        ("insert", f'SELECT {cols_a} FROM main."{t}" a '
                   f'WHERE NOT EXISTS (SELECT 1 FROM base."{t}" b WHERE {same_key})'),
    ]
    if others:
        # IS NOT is SQLite's null-safe "not equal", so NULL -> 'x' counts as a change
        differs = " OR ".join(f'a."{c}" IS NOT b."{c}"' for c in others)
        queries.append(("update", f'SELECT {cols_b}, {cols_a} FROM base."{t}" b '
                                  f'JOIN main."{t}" a ON {same_key} WHERE {differs}'))

    n = len(table.columns)
    for action, query in queries:
        room = max_rows - len(changes.rows)
        rows = conn.execute(f"{query} LIMIT {room + 1}").fetchall()
        if len(rows) > room or changes.truncated:
            # Too big to compare row by row. Keep counting, stop collecting.
            changes.truncated = True
            changes.counts[t] += conn.execute(f"SELECT COUNT(*) FROM ({query})").fetchone()[0]
            continue

        for row in rows:
            if action == "update":
                before, after = dict(zip(table.columns, row[:n])), dict(zip(table.columns, row[n:]))
            elif action == "delete":
                before, after = dict(zip(table.columns, row)), None
            else:
                before, after = None, dict(zip(table.columns, row))
            source = after or before
            key = tuple(source[k] for k in table.primary_key)
            changes.add(RowChange(t, key, action, before, after))
