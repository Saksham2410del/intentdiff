import json
import sqlite3

import pytest

from intentdiff import config
from intentdiff.schema import load_schema

# A tiny bank with the real schema, so the tests don't need the full dataset.
ROWS = """
INSERT INTO district (district_id, name, region) VALUES
    (1, 'Hl.m. Praha', 'Prague'),
    (4, 'Kladno', 'central Bohemia');

INSERT INTO account (account_id, district_id, frequency, created_on) VALUES
    (10, 1, 'monthly', '1995-01-01'),
    (11, 1, 'weekly', '1996-03-01'),
    (12, 4, 'monthly', '1997-07-15');

INSERT INTO client VALUES
    (100, '1970-01-01', 'M', 1, 'jan.100@seznam.cz', NULL),
    (101, '1980-05-05', 'F', 4, NULL, '+420 600 000 001'),
    (102, '1955-09-30', 'F', 4, 'eva.102@email.cz', '+420 700 000 002');

INSERT INTO disp VALUES
    (1000, 100, 10, 'owner'),
    (1001, 101, 11, 'owner'),
    (1002, 102, 12, 'owner');

INSERT INTO loan VALUES (500, 10, '1996-01-01', 100000, 24, 4166.0, 'running_ok');

INSERT INTO trans VALUES
    (1, 10, '1995-01-02', 'credit', 'cash_credit', 1000, 1000, NULL, NULL, NULL),
    (2, 10, '1995-02-02', 'debit', 'cash_withdrawal', 200, 800, NULL, NULL, NULL);
"""


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "bank.db"
    conn = sqlite3.connect(path)
    conn.executescript((config.ROOT / "data" / "schema.sql").read_text())
    conn.executescript(ROWS)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def schema(db):
    return load_schema(db, config.NOTES_PATH)


class FakeLLM:
    """Hands out canned answers in order - the last one repeats forever."""

    model = "fake"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []
        self.input_tokens = 0
        self.output_tokens = 0

    def generate_json(self, prompt, schema, temperature=0.0):
        self.prompts.append(prompt)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer)


def spec(sql, columns=(), quoted=(), ambiguities=(), rows=None):
    return {
        "intent_summary": "test",
        "reference_sql": sql,
        "specified_columns": list(columns),
        "stated_row_count": rows,
        "quoted_entities": list(quoted),
        "ambiguities": list(ambiguities),
    }
