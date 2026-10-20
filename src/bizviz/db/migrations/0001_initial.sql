-- Money is REAL dollars (rounded to cents at ingestion), dates are 'YYYY-MM-DD',
-- timestamps are ISO-8601 UTC, booleans are INTEGER 0/1, JSON arrays are TEXT.

CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO meta(key, value) VALUES ('schema_version', '1'), ('data_version', '0');

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE items (
    item_id TEXT PRIMARY KEY,
    institution_id TEXT,
    institution_name TEXT,
    access_token TEXT NOT NULL,
    link_mode TEXT NOT NULL CHECK (link_mode IN ('bank', 'investment', 'loan')),
    status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'needs_attention', 'error')),
    error_code TEXT,
    consent_expires_at TEXT,
    transactions_cursor TEXT,
    historical_complete INTEGER NOT NULL DEFAULT 0,
    investments_history_complete INTEGER NOT NULL DEFAULT 0,
    products TEXT NOT NULL DEFAULT '[]',
    unavailable_steps TEXT NOT NULL DEFAULT '[]',
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_daily_at TEXT,
    last_daily_attempt_at TEXT,
    last_holdings_at TEXT,
    last_inv_tx_at TEXT,
    created_at TEXT NOT NULL,
    removed_at TEXT,
    duplicate_of TEXT
);

CREATE TABLE accounts (
    account_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES items(item_id),
    name TEXT,
    official_name TEXT,
    mask TEXT,
    type TEXT NOT NULL,
    subtype TEXT,
    ownership TEXT NOT NULL DEFAULT 'mine' CHECK (ownership IN ('mine', 'joint')),
    included INTEGER NOT NULL DEFAULT 1,
    current_balance REAL,
    available_balance REAL,
    currency TEXT,
    balance_as_of TEXT,
    reviewed INTEGER NOT NULL DEFAULT 0,
    gone_at TEXT,
    duplicate_of_account TEXT,
    superseded_by TEXT
);

CREATE TABLE transactions (
    transaction_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    date TEXT NOT NULL,
    authorized_date TEXT,
    amount REAL NOT NULL,
    currency TEXT,
    name TEXT,
    merchant_name TEXT,
    merchant_entity_id TEXT,
    pfc_primary TEXT,
    pfc_detailed TEXT,
    payment_channel TEXT,
    pending INTEGER NOT NULL DEFAULT 0,
    pending_transaction_id TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_transactions_account_date ON transactions(account_id, date);
CREATE INDEX idx_transactions_date ON transactions(date);

CREATE TABLE tx_overrides (
    transaction_id TEXT PRIMARY KEY,
    category_id TEXT,
    flow_class TEXT CHECK (flow_class IS NULL OR flow_class IN ('earning', 'spending', 'internal', 'ignored')),
    recurring INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE categories (
    category_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('plaid', 'system', 'custom')),
    applies_to TEXT NOT NULL CHECK (applies_to IN ('spending', 'earning', 'both', 'transfer')),
    hidden INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE merchant_rules (
    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_key TEXT NOT NULL UNIQUE,
    category_id TEXT,
    flow_class TEXT CHECK (flow_class IS NULL OR flow_class IN ('earning', 'spending', 'internal', 'ignored')),
    recurring INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE tx_classification (
    transaction_id TEXT PRIMARY KEY,
    merchant_key TEXT NOT NULL,
    merchant_display TEXT,
    mine_flow TEXT,
    mine_category_id TEXT,
    mine_recurring INTEGER,
    household_flow TEXT,
    household_category_id TEXT,
    household_recurring INTEGER,
    pair_transaction_id TEXT,
    pair_kind TEXT
);

CREATE TABLE recurring_groups (
    scope TEXT NOT NULL CHECK (scope IN ('mine', 'household')),
    merchant_key TEXT NOT NULL,
    merchant_display TEXT,
    cadence TEXT NOT NULL,
    typical_amount REAL,
    last_date TEXT,
    count INTEGER NOT NULL,
    active INTEGER NOT NULL,
    PRIMARY KEY (scope, merchant_key)
);

CREATE TABLE balance_snapshots (
    account_id TEXT NOT NULL,
    date TEXT NOT NULL,
    current REAL,
    available REAL,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (account_id, date)
);

CREATE TABLE securities (
    security_id TEXT PRIMARY KEY,
    ticker_symbol TEXT,
    name TEXT,
    type TEXT,
    is_cash_equivalent INTEGER NOT NULL DEFAULT 0,
    close_price REAL,
    close_price_as_of TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE holdings_snapshots (
    account_id TEXT NOT NULL,
    security_id TEXT NOT NULL,
    date TEXT NOT NULL,
    quantity REAL NOT NULL,
    institution_price REAL,
    institution_price_as_of TEXT,
    institution_value REAL,
    cost_basis REAL,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (account_id, security_id, date)
);

CREATE TABLE investment_transactions (
    investment_transaction_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    security_id TEXT,
    date TEXT NOT NULL,
    name TEXT,
    type TEXT,
    subtype TEXT,
    quantity REAL,
    amount REAL,
    price REAL,
    fees REAL
);
CREATE INDEX idx_investment_transactions_account_date ON investment_transactions(account_id, date);

CREATE TABLE prices (
    yahoo_symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    raw_close REAL NOT NULL,
    PRIMARY KEY (yahoo_symbol, date)
);

CREATE TABLE price_splits (
    yahoo_symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    ratio REAL NOT NULL,
    PRIMARY KEY (yahoo_symbol, date)
);

CREATE TABLE price_fetch_state (
    yahoo_symbol TEXT PRIMARY KEY,
    last_attempt_at TEXT,
    last_success_at TEXT,
    status TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    first_date TEXT,
    last_date TEXT
);

CREATE TABLE investment_estimates (
    account_id TEXT NOT NULL,
    date TEXT NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (account_id, date)
);

CREATE TABLE investment_estimate_meta (
    account_id TEXT PRIMARY KEY,
    anchor_date TEXT,
    window_start TEXT,
    window_end TEXT,
    excluded_count INTEGER NOT NULL DEFAULT 0,
    excluded_value REAL NOT NULL DEFAULT 0,
    warnings TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'ok'
);

CREATE TABLE device_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    device_name TEXT,
    user_agent TEXT,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE TABLE sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT,
    job TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    ok INTEGER,
    error_code TEXT,
    transient INTEGER NOT NULL DEFAULT 0
);

INSERT INTO categories(category_id, name, kind, applies_to) VALUES
    ('plaid:FOOD_AND_DRINK', 'Food & Drink', 'plaid', 'spending'),
    ('plaid:GENERAL_MERCHANDISE', 'General Merchandise', 'plaid', 'spending'),
    ('plaid:RENT_AND_UTILITIES', 'Rent & Utilities', 'plaid', 'spending'),
    ('plaid:TRANSPORTATION', 'Transportation', 'plaid', 'spending'),
    ('plaid:TRAVEL', 'Travel', 'plaid', 'spending'),
    ('plaid:ENTERTAINMENT', 'Entertainment', 'plaid', 'spending'),
    ('plaid:MEDICAL', 'Medical', 'plaid', 'spending'),
    ('plaid:PERSONAL_CARE', 'Personal Care', 'plaid', 'spending'),
    ('plaid:GENERAL_SERVICES', 'General Services', 'plaid', 'spending'),
    ('plaid:GOVERNMENT_AND_NON_PROFIT', 'Government & Non-profit', 'plaid', 'spending'),
    ('plaid:HOME_IMPROVEMENT', 'Home Improvement', 'plaid', 'spending'),
    ('plaid:BANK_FEES', 'Bank Fees', 'plaid', 'spending'),
    ('plaid:LOAN_PAYMENTS', 'Loan Payments', 'plaid', 'spending'),
    ('plaid:OTHER', 'Other', 'plaid', 'spending'),
    ('plaid:INCOME_SALARY', 'Salary', 'plaid', 'earning'),
    ('plaid:INCOME_CONTRACTOR', 'Contractor', 'plaid', 'earning'),
    ('plaid:INCOME_GIG_ECONOMY', 'Gig Economy', 'plaid', 'earning'),
    ('plaid:INCOME_DIVIDENDS', 'Dividends', 'plaid', 'earning'),
    ('plaid:INCOME_INTEREST_EARNED', 'Interest', 'plaid', 'earning'),
    ('plaid:INCOME_RETIREMENT_PENSION', 'Retirement & Pension', 'plaid', 'earning'),
    ('plaid:INCOME_RENTAL', 'Rental Income', 'plaid', 'earning'),
    ('plaid:INCOME_TAX_REFUND', 'Tax Refund', 'plaid', 'earning'),
    ('plaid:INCOME_UNEMPLOYMENT', 'Unemployment', 'plaid', 'earning'),
    ('plaid:INCOME_LONG_TERM_DISABILITY', 'Disability', 'plaid', 'earning'),
    ('plaid:INCOME_CHILD_SUPPORT', 'Child Support', 'plaid', 'earning'),
    ('plaid:INCOME_MILITARY', 'Military', 'plaid', 'earning'),
    ('plaid:INCOME_OTHER', 'Other Income', 'plaid', 'earning'),
    ('sys:to_joint', 'To joint account', 'system', 'spending'),
    ('sys:cc_unlinked', 'Credit card payment (unlinked card)', 'system', 'spending'),
    ('sys:transfers_out', 'Transfers out', 'system', 'spending'),
    ('sys:from_joint', 'From joint account', 'system', 'earning'),
    ('sys:money_in_others', 'Money in from others', 'system', 'earning'),
    ('sys:uncategorized', 'Uncategorized', 'system', 'both'),
    ('sys:transfer', 'Transfer', 'system', 'transfer');
