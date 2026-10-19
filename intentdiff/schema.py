"""Reads the table layout from the database itself, plus hand-written notes."""
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CASCADING_ACTIONS = {"CASCADE", "SET NULL", "SET DEFAULT"}


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


@dataclass
class Table:
    name: str
    columns: list[str]
    types: dict[str, str]
    primary_key: list[str]
    # (column, referenced table, referenced column, on delete, on update)
    foreign_keys: list[tuple] = field(default_factory=list)


@dataclass
class Schema:
    tables: dict[str, Table]
    has_triggers: bool = False
    notes: dict = field(default_factory=dict)

    def has_column(self, table, column):
        table = self.tables.get(table.lower())
        return table is not None and column.lower() in table.columns

    def affected_tables(self, written):
        """Tables whose rows can change when `written` tables are modified.

        Normally that's just the written tables, but cascading foreign keys and
        triggers can reach further, and we must diff those tables too.
        """
        if self.has_triggers:
            return set(self.tables)
        affected = set(written)
        grew = True
        while grew:
            grew = False
            for table in self.tables.values():
                if table.name in affected:
                    continue
                for _, ref_table, _, on_delete, on_update in table.foreign_keys:
                    cascades = on_delete in CASCADING_ACTIONS or on_update in CASCADING_ACTIONS
                    if ref_table in affected and cascades:
                        affected.add(table.name)
                        grew = True
                        break
        return affected

    def describe(self):
        """Schema as plain text for the LLM prompt."""
        lines = []
        for table in self.tables.values():
            note = self.notes.get(table.name, {})
            header = f"Table {table.name}"
            if note.get("about"):
                header += f"  -- {note['about']}"
            lines.append(header)

            refs = {fk[0]: f"{fk[1]}.{fk[2]}" for fk in table.foreign_keys}
            col_notes = note.get("columns", {})
            for col in table.columns:
                line = f"  {col} {table.types[col]}"
                if col in table.primary_key:
                    line += " (primary key)"
                if col in refs:
                    line += f" -> {refs[col]}"
                if col in col_notes:
                    line += f"  -- {col_notes[col]}"
                lines.append(line)
            lines.append("")
        return "\n".join(lines).strip()


def load_schema(db_path, notes_path=None):
    conn = sqlite3.connect(readonly_uri(db_path), uri=True)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        tables = {}
        for name in names:
            # table_info rows: (cid, name, type, notnull, default, pk position)
            info = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5] > 0]
            # foreign_key_list rows: (id, seq, table, from, to, on_update, on_delete, match)
            fks = [(r[3], r[2], r[4], r[6], r[5])
                   for r in conn.execute(f'PRAGMA foreign_key_list("{name}")')]
            tables[name] = Table(
                name=name,
                columns=[r[1] for r in info],
                types={r[1]: r[2] for r in info},
                primary_key=pk,
                foreign_keys=fks,
            )
        triggers = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger'").fetchone()[0]
    finally:
        conn.close()

    notes = {}
    if notes_path and Path(notes_path).exists():
        notes = yaml.safe_load(Path(notes_path).read_text(encoding="utf-8")) or {}
    return Schema(tables=tables, has_triggers=triggers > 0, notes=notes)
