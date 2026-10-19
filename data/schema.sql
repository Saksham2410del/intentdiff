-- Bank schema built from the PKDD'99 ("Berka") financial dataset.
-- Names are translated to English. Columns marked "synthetic" are not in
-- the original data; scripts/build_db.py generates them so that the usual
-- data-fix tickets (wrong email, close an account, ...) have something to touch.

CREATE TABLE district (
    district_id            INTEGER PRIMARY KEY,
    name                   TEXT NOT NULL,
    region                 TEXT NOT NULL,
    inhabitants            INTEGER,
    cities                 INTEGER,
    urban_ratio            REAL,
    avg_salary             INTEGER,
    unemployment_95        REAL,
    unemployment_96        REAL,
    entrepreneurs_per_1000 INTEGER,
    crimes_95              INTEGER,
    crimes_96              INTEGER
);

CREATE TABLE account (
    account_id   INTEGER PRIMARY KEY,
    district_id  INTEGER NOT NULL REFERENCES district (district_id),
    frequency    TEXT NOT NULL,                   -- monthly / weekly / after_transaction
    created_on   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',  -- synthetic: active / closed
    closed_on    TEXT                             -- synthetic
);

CREATE TABLE client (
    client_id    INTEGER PRIMARY KEY,
    birth_date   TEXT NOT NULL,
    gender       TEXT NOT NULL,                   -- decoded from the birth number
    district_id  INTEGER NOT NULL REFERENCES district (district_id),
    email        TEXT,                            -- synthetic
    phone        TEXT                             -- synthetic
);

CREATE TABLE disp (
    disp_id     INTEGER PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES client (client_id),
    account_id  INTEGER NOT NULL REFERENCES account (account_id),
    type        TEXT NOT NULL                     -- owner / user
);

CREATE TABLE card (
    card_id    INTEGER PRIMARY KEY,
    disp_id    INTEGER NOT NULL REFERENCES disp (disp_id),
    type       TEXT NOT NULL,                     -- junior / classic / gold
    issued_on  TEXT NOT NULL
);

CREATE TABLE loan (
    loan_id          INTEGER PRIMARY KEY,
    account_id       INTEGER NOT NULL REFERENCES account (account_id),
    granted_on       TEXT NOT NULL,
    amount           INTEGER NOT NULL,
    duration_months  INTEGER NOT NULL,
    monthly_payment  REAL NOT NULL,
    status           TEXT NOT NULL                -- finished_ok / finished_unpaid / running_ok / running_in_debt
);

-- "order" in the original data, renamed because ORDER is a reserved word
CREATE TABLE standing_order (
    order_id     INTEGER PRIMARY KEY,
    account_id   INTEGER NOT NULL REFERENCES account (account_id),
    bank_to      TEXT NOT NULL,
    account_to   TEXT NOT NULL,
    amount       REAL NOT NULL,
    purpose      TEXT
);

CREATE TABLE trans (
    trans_id         INTEGER PRIMARY KEY,
    account_id       INTEGER NOT NULL REFERENCES account (account_id),
    date             TEXT NOT NULL,
    type             TEXT NOT NULL,               -- credit / debit
    operation        TEXT,
    amount           REAL NOT NULL,
    balance          REAL NOT NULL,
    purpose          TEXT,
    partner_bank     TEXT,
    partner_account  TEXT
);

CREATE INDEX idx_trans_account ON trans (account_id);
CREATE INDEX idx_disp_client ON disp (client_id);
CREATE INDEX idx_disp_account ON disp (account_id);
