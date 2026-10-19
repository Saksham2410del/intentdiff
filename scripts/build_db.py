"""Build data/bank.db from the raw Berka (PKDD'99) files.

Download the .asc files from https://github.com/jlacko/berka-dataset into
data/raw/ first, then run:

    python scripts/build_db.py
"""
import csv
import random
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "bank.db"

FREQUENCY = {
    "POPLATEK MESICNE": "monthly",
    "POPLATEK TYDNE": "weekly",
    "POPLATEK PO OBRATU": "after_transaction",
}
# VYBER isn't listed in the official description, but it's a cash
# withdrawal, so it counts as a debit.
TRANS_TYPE = {"PRIJEM": "credit", "VYDAJ": "debit", "VYBER": "debit"}
OPERATION = {
    "VYBER KARTOU": "card_withdrawal",
    "VKLAD": "cash_credit",
    "PREVOD Z UCTU": "collection_from_bank",
    "VYBER": "cash_withdrawal",
    "PREVOD NA UCET": "remittance_to_bank",
}
PURPOSE = {
    "POJISTNE": "insurance",
    "SLUZBY": "statement_fee",
    "UROK": "interest",
    "SANKC. UROK": "penalty_interest",
    "SIPO": "household",
    "DUCHOD": "pension",
    "UVER": "loan_payment",
    "LEASING": "leasing",
}
LOAN_STATUS = {
    "A": "finished_ok",
    "B": "finished_unpaid",
    "C": "running_ok",
    "D": "running_in_debt",
}

# Only used to make the synthetic emails look a bit less robotic.
FIRST_NAMES = [
    "jan", "petr", "pavel", "tomas", "martin", "jiri", "lukas", "jakub",
    "jana", "eva", "marie", "lucie", "petra", "katerina", "tereza", "hana",
]
DOMAINS = ["seznam.cz", "email.cz", "centrum.cz", "gmail.com"]


def read_asc(name):
    with open(RAW / f"{name}.asc", newline="", encoding="ascii") as f:
        rows = csv.reader(f, delimiter=";", quotechar='"')
        next(rows)  # header
        yield from rows


def iso_date(raw):
    """930705 (or '931107 00:00:00') -> 1993-07-05. All dates are in the 1900s."""
    s = raw.strip()[:6]
    return f"19{s[:2]}-{s[2:4]}-{s[4:6]}"


def birth_date_and_gender(birth_number):
    """Women have 50 added to the month: 706213 -> (1970-12-13, F)."""
    yy, mm, dd = birth_number[:2], int(birth_number[2:4]), birth_number[4:6]
    gender = "M"
    if mm > 50:
        gender, mm = "F", mm - 50
    return f"19{yy}-{mm:02d}-{dd}", gender


def num(value, cast=float):
    value = value.strip()
    return None if value in ("", "?") else cast(value)


def text(value, mapping=None):
    value = value.strip()
    if not value:
        return None
    return mapping.get(value, value.lower()) if mapping else value


def load(conn):
    conn.executemany(
        "INSERT INTO district VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            (int(r[0]), r[1], r[2], num(r[3], int), num(r[8], int), num(r[9]),
             num(r[10], int), num(r[11]), num(r[12]), num(r[13], int),
             num(r[14], int), num(r[15], int))
            for r in read_asc("district")
        ),
    )
    conn.executemany(
        "INSERT INTO account (account_id, district_id, frequency, created_on) VALUES (?,?,?,?)",
        ((int(r[0]), int(r[1]), FREQUENCY[r[2]], iso_date(r[3])) for r in read_asc("account")),
    )
    conn.executemany(
        "INSERT INTO client (client_id, birth_date, gender, district_id) VALUES (?,?,?,?)",
        ((int(r[0]), *birth_date_and_gender(r[1]), int(r[2])) for r in read_asc("client")),
    )
    conn.executemany(
        "INSERT INTO disp VALUES (?,?,?,?)",
        ((int(r[0]), int(r[1]), int(r[2]), "owner" if r[3] == "OWNER" else "user")
         for r in read_asc("disp")),
    )
    conn.executemany(
        "INSERT INTO card VALUES (?,?,?,?)",
        ((int(r[0]), int(r[1]), r[2], iso_date(r[3])) for r in read_asc("card")),
    )
    conn.executemany(
        "INSERT INTO loan VALUES (?,?,?,?,?,?,?)",
        ((int(r[0]), int(r[1]), iso_date(r[2]), int(r[3]), int(r[4]), float(r[5]),
          LOAN_STATUS[r[6]]) for r in read_asc("loan")),
    )
    conn.executemany(
        "INSERT INTO standing_order VALUES (?,?,?,?,?,?)",
        ((int(r[0]), int(r[1]), r[2], r[3], float(r[4]), text(r[5], PURPOSE))
         for r in read_asc("order")),
    )
    conn.executemany(
        "INSERT INTO trans VALUES (?,?,?,?,?,?,?,?,?,?)",
        ((int(r[0]), int(r[1]), iso_date(r[2]), TRANS_TYPE[r[3]], text(r[4], OPERATION),
          float(r[5]), float(r[6]), text(r[7], PURPOSE), text(r[8]), text(r[9]))
         for r in read_asc("trans")),
    )


def add_synthetic_columns(conn):
    rng = random.Random(42)

    updates = []
    client_ids = [row[0] for row in conn.execute("SELECT client_id FROM client ORDER BY client_id")]
    for client_id in client_ids:
        # leave some gaps on purpose - "clients with no email on file" is a real ticket type
        email = None
        if rng.random() > 0.06:
            email = f"{rng.choice(FIRST_NAMES)}.{client_id}@{rng.choice(DOMAINS)}"
        phone = None
        if rng.random() > 0.10:
            phone = f"+420 {rng.choice('67')}{rng.randint(10, 99)} {rng.randint(100, 999)} {rng.randint(100, 999)}"
        updates.append((email, phone, client_id))
    conn.executemany("UPDATE client SET email = ?, phone = ? WHERE client_id = ?", updates)

    # Close about 3% of accounts. Never one with a running loan, and the
    # closing date is the day after its last transaction so the data stays believable.
    candidates = [row[0] for row in conn.execute("""
        SELECT account_id FROM account
        WHERE account_id NOT IN (SELECT account_id FROM loan WHERE status LIKE 'running%')
        ORDER BY account_id
    """)]
    closed = rng.sample(candidates, k=len(candidates) * 3 // 100)
    conn.executemany("""
        UPDATE account
        SET status = 'closed',
            closed_on = (SELECT date(MAX(t.date), '+1 day') FROM trans t
                         WHERE t.account_id = account.account_id)
        WHERE account_id = ?
    """, [(a,) for a in closed])


def main():
    missing = [n for n in ("account", "card", "client", "disp", "district", "loan", "order", "trans")
               if not (RAW / f"{n}.asc").exists()]
    if missing:
        raise SystemExit(f"Missing raw files in {RAW}: {', '.join(missing)}")

    start = time.time()
    OUT.unlink(missing_ok=True)
    conn = sqlite3.connect(OUT)
    conn.execute("PRAGMA foreign_keys = ON")  # catches any broken references while loading
    conn.executescript((ROOT / "data" / "schema.sql").read_text())

    with conn:
        load(conn)
        add_synthetic_columns(conn)

    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    for table in tables:
        count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        print(f"{table:15} {count:>9,}")
    conn.close()
    print(f"\nWrote {OUT} in {time.time() - start:.1f}s")


if __name__ == "__main__":
    main()
