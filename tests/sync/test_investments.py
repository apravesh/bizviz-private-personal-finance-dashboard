from datetime import date
from typing import Any

import pytest

from bizviz.context import AppContext
from bizviz.db.connection import Connection
from bizviz.plaid_fake import FakePlaidClient
from bizviz.plaid_types import PlaidError
from bizviz.sync.investments import capture_holdings, fetch_investment_transactions
from tests.factories import make_account, make_item
from tests.sync.helpers import data_version, deep, item_row, load


def setup_investment_item(db: Connection, **kw: Any) -> None:
    make_item(db, products=["investments"], link_mode="investment", **kw)
    make_account(db, "acc-inv", "item-1", type="investment", subtype="brokerage", mask="9999")


def holdings_rows(db: Connection, account_id: str = "acc-inv") -> dict[tuple[str, str], Any]:
    rows = db.execute(
        "SELECT * FROM holdings_snapshots WHERE account_id = ?", (account_id,)
    ).fetchall()
    return {(r["security_id"], r["date"]): r for r in rows}


def test_capture_holdings_stores_snapshot_securities_and_dates(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_investment_item(db)
    fake_plaid.queue("investments_holdings_get", load("holdings"))
    before = data_version(db)
    dates = capture_holdings(app_ctx, "item-1")
    assert dates == {"acc-inv": date(2026, 10, 14)}
    rows = holdings_rows(db)
    assert set(rows) == {("sec-aapl", "2026-10-14"), ("sec-vti", "2026-10-14")}
    aapl = rows[("sec-aapl", "2026-10-14")]
    assert (aapl["quantity"], aapl["institution_price"], aapl["cost_basis"]) == (
        10.0,
        190.5,
        1500.0,
    )
    assert aapl["institution_price_as_of"] == "2026-10-14"
    assert aapl["captured_at"].endswith("Z")
    sec = db.execute("SELECT * FROM securities WHERE security_id = 'sec-vti'").fetchone()
    assert (sec["ticker_symbol"], sec["type"], sec["is_cash_equivalent"]) == ("VTI", "etf", 0)
    assert item_row(db)["last_holdings_at"] == "2026-10-15"
    assert data_version(db) > before


def test_second_capture_same_date_replaces_rows(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_investment_item(db)
    fake_plaid.queue("investments_holdings_get", load("holdings"))
    capture_holdings(app_ctx, "item-1")
    second = load("holdings")
    second["holdings"] = [second["holdings"][1]]
    second["holdings"][0]["institution_price_as_of"] = "2026-10-14"
    second["holdings"][0]["quantity"] = 6.0
    fake_plaid.queue("investments_holdings_get", second)
    capture_holdings(app_ctx, "item-1")
    rows = holdings_rows(db)
    assert set(rows) == {("sec-vti", "2026-10-14")}
    assert rows[("sec-vti", "2026-10-14")]["quantity"] == 6.0


def test_snapshot_date_capped_at_today_and_falls_back_to_today(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_investment_item(db)
    make_account(db, "acc-empty", "item-1", type="investment", subtype="ira", mask="1111")
    resp = load("holdings")
    resp["holdings"][0]["institution_price_as_of"] = "2026-10-20"
    resp["accounts"].append(
        {**resp["accounts"][0], "account_id": "acc-empty", "mask": "1111", "subtype": "ira"}
    )
    fake_plaid.queue("investments_holdings_get", resp)
    dates = capture_holdings(app_ctx, "item-1")
    assert dates == {"acc-inv": date(2026, 10, 15), "acc-empty": date(2026, 10, 15)}
    assert {k[1] for k in holdings_rows(db)} == {"2026-10-15"}


def inv_page(start: int, n: int, total: int) -> dict[str, Any]:
    page = deep(load("inv_transactions"))
    template = page["investment_transactions"][0]
    page["investment_transactions"] = [
        {**template, "investment_transaction_id": f"itx-{start + i}"} for i in range(n)
    ]
    page["total_investment_transactions"] = total
    return page


def test_first_run_fetches_730_days_with_pagination(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_investment_item(db)
    fake_plaid.queue("investments_transactions_get", inv_page(0, 500, 1100))
    fake_plaid.queue("investments_transactions_get", inv_page(500, 500, 1100))
    fake_plaid.queue("investments_transactions_get", inv_page(1000, 100, 1100))
    fetch_investment_transactions(app_ctx, "item-1")
    calls = [c for c in fake_plaid.calls if c[0] == "investments_transactions_get"]
    assert [c[2]["offset"] for c in calls] == [0, 500, 1000]
    assert {c[2]["count"] for c in calls} == {500}
    assert calls[0][1] == ("access-item-1", date(2024, 10, 15), date(2026, 10, 15))
    assert db.execute("SELECT count(*) FROM investment_transactions").fetchone()[0] == 1100
    assert db.execute("SELECT 1 FROM securities WHERE security_id = 'sec-old'").fetchone()
    item = item_row(db)
    assert item["investments_history_complete"] == 1
    assert item["last_inv_tx_at"] == "2026-10-15"
    row = db.execute(
        "SELECT * FROM investment_transactions WHERE investment_transaction_id='itx-0'"
    ).fetchone()
    assert (row["amount"], row["quantity"], row["type"]) == (381.0, 2.0, "buy")


def test_later_runs_fetch_30_days_and_upsert(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_investment_item(db, investments_history_complete=1)
    fake_plaid.queue("investments_transactions_get", inv_page(0, 3, 3))
    fake_plaid.queue("investments_transactions_get", inv_page(0, 3, 3))
    fetch_investment_transactions(app_ctx, "item-1")
    fetch_investment_transactions(app_ctx, "item-1")
    calls = [c for c in fake_plaid.calls if c[0] == "investments_transactions_get"]
    assert calls[0][1][1] == date(2026, 9, 15)
    assert db.execute("SELECT count(*) FROM investment_transactions").fetchone()[0] == 3


def test_empty_page_stops_pagination(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_investment_item(db)
    fake_plaid.queue("investments_transactions_get", inv_page(0, 2, 5))
    fake_plaid.queue("investments_transactions_get", inv_page(2, 0, 5))
    fetch_investment_transactions(app_ctx, "item-1")
    assert fake_plaid.count("investments_transactions_get") == 2


def test_product_not_ready_leaves_history_incomplete(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_investment_item(db)
    fake_plaid.queue(
        "investments_transactions_get", PlaidError("INVESTMENTS_ERROR", "PRODUCT_NOT_READY")
    )
    with pytest.raises(PlaidError):
        fetch_investment_transactions(app_ctx, "item-1")
    item = item_row(db)
    assert item["investments_history_complete"] == 0
    assert item["last_inv_tx_at"] is None
