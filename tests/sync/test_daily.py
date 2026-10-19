import json
from datetime import timedelta
from typing import Any

import pytest

from bizviz.context import AppContext
from bizviz.db.connection import Connection
from bizviz.errors import NotFoundError
from bizviz.plaid_fake import FakePlaidClient
from bizviz.plaid_types import PlaidError, PlaidNetworkError
from bizviz.sync.daily import forced_sync, run_daily_job
from tests.factories import make_account, make_item
from tests.sync.helpers import item_get, item_row, load, plaid_account, plaid_txn, sync_page

HOLDINGS_ERROR = PlaidError("ITEM_ERROR", "INSTITUTION_DOWN")


def setup_brokerage(db: Connection, **kw: Any) -> None:
    make_item(db, products=["transactions", "investments"], **kw)
    make_account(db, "acc-chk", "item-1", name="Total Checking", mask="0001")
    make_account(db, "acc-inv", "item-1", type="investment", subtype="brokerage", mask="9999")


def snapshots(db: Connection) -> dict[str, str]:
    return {
        r["account_id"]: r["date"]
        for r in db.execute("SELECT account_id, date FROM balance_snapshots").fetchall()
    }


def queue_happy_path(plaid: FakePlaidClient) -> None:
    plaid.queue("investments_holdings_get", load("holdings"))
    plaid.queue("accounts_get", load("accounts_get"))
    inv = load("inv_transactions")
    plaid.queue("investments_transactions_get", inv)


def test_full_daily_job_runs_all_steps(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_brokerage(db)
    queue_happy_path(fake_plaid)
    out = run_daily_job(app_ctx, "item-1")
    assert out.steps == {"holdings": "ok", "balances": "ok", "investment_transactions": "ok"}
    assert out.complete
    assert out.investment_account_ids == ["acc-inv"]
    assert [c[0] for c in fake_plaid.calls] == [
        "investments_holdings_get",
        "accounts_get",
        "investments_transactions_get",
    ]
    item = item_row(db)
    assert item["last_daily_at"] == "2026-10-15"
    assert item["last_daily_attempt_at"] == "2026-10-15T15:00:00Z"
    assert snapshots(db) == {"acc-chk": "2026-10-15", "acc-inv": "2026-10-14"}
    chk = db.execute("SELECT * FROM accounts WHERE account_id='acc-chk'").fetchone()
    assert chk["current_balance"] == 1200.51
    log = db.execute("SELECT * FROM sync_log WHERE job = 'daily'").fetchall()
    assert [(r["ok"], r["error_code"]) for r in log] == [(1, None)]


def test_holdings_failure_still_snapshots_balances_and_leaves_daily_unset(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_brokerage(db)
    fake_plaid.queue("investments_holdings_get", HOLDINGS_ERROR)
    fake_plaid.queue("accounts_get", load("accounts_get"))
    fake_plaid.queue("investments_transactions_get", load("inv_transactions"))
    out = run_daily_job(app_ctx, "item-1")
    assert out.steps["holdings"] == "transient"
    assert out.steps["balances"] == "ok"
    assert out.steps["investment_transactions"] == "ok"
    assert not out.complete
    assert item_row(db)["last_daily_at"] is None
    assert snapshots(db) == {"acc-chk": "2026-10-15", "acc-inv": "2026-10-15"}
    log = db.execute("SELECT * FROM sync_log WHERE job = 'daily'").fetchone()
    assert (log["ok"], log["error_code"], log["transient"]) == (0, "INSTITUTION_DOWN", 1)


def test_unavailable_holdings_step_counts_as_complete(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_brokerage(db)
    fake_plaid.queue("investments_holdings_get", PlaidError("ITEM_ERROR", "PRODUCTS_NOT_SUPPORTED"))
    fake_plaid.queue("accounts_get", load("accounts_get"))
    fake_plaid.queue("investments_transactions_get", load("inv_transactions"))
    out = run_daily_job(app_ctx, "item-1")
    assert out.steps["holdings"] == "unavailable"
    assert out.complete
    item = item_row(db)
    assert json.loads(item["unavailable_steps"]) == ["holdings"]
    assert item["last_daily_at"] == "2026-10-15"
    assert item["status"] == "ok"


def test_unavailable_step_is_not_retried_next_day(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_brokerage(db, unavailable_steps='["holdings", "investment_transactions"]')
    fake_plaid.queue("accounts_get", load("accounts_get"))
    out = run_daily_job(app_ctx, "item-1")
    assert out.steps == {
        "holdings": "skipped",
        "balances": "ok",
        "investment_transactions": "skipped",
    }
    assert out.complete
    assert fake_plaid.count("investments_holdings_get") == 0


def test_hard_failures_set_item_status_and_continue(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_brokerage(db)
    fake_plaid.queue("investments_holdings_get", PlaidError("ITEM_ERROR", "ITEM_LOGIN_REQUIRED"))
    fake_plaid.queue("accounts_get", PlaidNetworkError("timeout"))
    fake_plaid.queue("investments_transactions_get", PlaidError("ITEM_ERROR", "WHAT"))
    out = run_daily_job(app_ctx, "item-1")
    assert out.steps == {
        "holdings": "failed",
        "balances": "transient",
        "investment_transactions": "failed",
    }
    assert not out.complete
    item = item_row(db)
    assert item["status"] == "error"
    assert item["error_code"] == "WHAT"
    log = db.execute("SELECT * FROM sync_log WHERE job = 'daily'").fetchone()
    assert (log["ok"], log["transient"], log["error_code"]) == (0, 0, "ITEM_LOGIN_REQUIRED")


def test_investment_transactions_product_not_ready_is_transient(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_brokerage(db)
    fake_plaid.queue("investments_holdings_get", load("holdings"))
    fake_plaid.queue("accounts_get", load("accounts_get"))
    fake_plaid.queue(
        "investments_transactions_get", PlaidError("INVESTMENTS_ERROR", "PRODUCT_NOT_READY")
    )
    out = run_daily_job(app_ctx, "item-1")
    assert out.steps["investment_transactions"] == "transient"
    assert item_row(db)["investments_history_complete"] == 0
    assert item_row(db)["last_daily_at"] is None
    assert db.execute("SELECT transient FROM sync_log").fetchone()[0] == 1


def test_steps_not_repeated_when_done_today(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_brokerage(db, last_holdings_at="2026-10-15", last_inv_tx_at="2026-10-15")
    fake_plaid.queue("accounts_get", load("accounts_get"))
    out = run_daily_job(app_ctx, "item-1")
    assert out.steps["holdings"] == "skipped"
    assert out.steps["investment_transactions"] == "skipped"
    assert snapshots(db)["acc-inv"] == "2026-10-15"


def test_item_without_investments_skips_investment_steps(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db, products=["transactions"])
    make_account(db, "acc-chk", "item-1")
    fake_plaid.queue("accounts_get", load("accounts_get"))
    out = run_daily_job(app_ctx, "item-1")
    assert out.steps == {
        "holdings": "skipped",
        "balances": "ok",
        "investment_transactions": "skipped",
    }
    assert out.investment_account_ids == ["acc-inv"]
    assert fake_plaid.count("investments_holdings_get") == 0


def test_item_with_zero_accounts_gets_balances_first(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db, products=["transactions", "investments"])
    queue_happy_path(fake_plaid)
    out = run_daily_job(app_ctx, "item-1")
    assert [c[0] for c in fake_plaid.calls][0] == "accounts_get"
    assert out.steps["holdings"] == "ok"
    assert out.investment_account_ids == ["acc-inv"]


def test_removed_item_is_skipped(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db, removed_at="2026-05-01T00:00:00Z")
    out = run_daily_job(app_ctx, "item-1")
    assert out.steps == {}
    assert fake_plaid.calls == []
    assert item_row(db)["last_daily_attempt_at"] is None


def test_balances_marks_missing_accounts_gone_and_clears_when_back(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db, products=["transactions"])
    make_account(db, "acc-chk", "item-1")
    make_account(db, "old", "item-1", mask="0002")
    resp = load("accounts_get")
    resp["accounts"] = [plaid_account("acc-chk")]
    fake_plaid.queue("accounts_get", resp)
    run_daily_job(app_ctx, "item-1")
    gone = db.execute("SELECT gone_at FROM accounts WHERE account_id='old'").fetchone()[0]
    assert gone == "2026-10-15"
    app_ctx.clock_holder.now += timedelta(days=1)  # type: ignore[attr-defined]
    resp["accounts"].append(plaid_account("old", mask="0002"))
    fake_plaid.queue("accounts_get", resp)
    run_daily_job(app_ctx, "item-1")
    assert db.execute("SELECT gone_at FROM accounts WHERE account_id='old'").fetchone()[0] is None


def test_daily_path_applies_duplicate_matching(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db, "other", products=["transactions"])
    make_account(db, "other-chk", "other", name="Total Checking", mask="0001")
    make_item(db, products=["transactions"])
    fake_plaid.queue("accounts_get", load("accounts_get"))
    run_daily_job(app_ctx, "item-1")
    row = db.execute("SELECT * FROM accounts WHERE account_id='acc-chk'").fetchone()
    assert (row["included"], row["duplicate_of_account"]) == (0, "other-chk")


class TestForcedSync:
    def test_fills_products_snapshots_balances_and_syncs_transactions(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        make_item(db, products=[])
        fake_plaid.queue("item_get", item_get(["transactions"]))
        fake_plaid.queue("accounts_get", load("accounts_get"))
        fake_plaid.queue(
            "transactions_sync",
            sync_page(added=[plaid_txn("t1", "acc-chk")], cursor="cx"),
        )
        fake_plaid.queue("item_get", item_get(["transactions"]))
        forced_sync(app_ctx, "item-1")
        assert [c[0] for c in fake_plaid.calls] == [
            "item_get",
            "accounts_get",
            "transactions_sync",
            "item_get",
        ]
        item = item_row(db)
        assert json.loads(item["products"]) == ["investments", "transactions"]
        assert item["last_attempt_at"] == "2026-10-15T15:00:00Z"
        assert item["transactions_cursor"] == "cx"
        assert db.execute("SELECT count(*) FROM accounts").fetchone()[0] == 2
        assert snapshots(db) == {"acc-chk": "2026-10-15", "acc-inv": "2026-10-15"}
        assert db.execute("SELECT count(*) FROM transactions").fetchone()[0] == 1

    def test_without_transactions_product_never_calls_sync(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        make_item(db, products=[], link_mode="investment")
        fake_plaid.queue("item_get", item_get(["investments"], billed=["investments"]))
        fake_plaid.queue("accounts_get", load("accounts_get"))
        forced_sync(app_ctx, "item-1")
        assert fake_plaid.count("transactions_sync") == 0
        assert snapshots(db)

    def test_item_get_failure_stops_and_records_attempt(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        make_item(db, products=[])
        fake_plaid.queue("item_get", PlaidNetworkError("timeout"))
        forced_sync(app_ctx, "item-1")
        assert [c[0] for c in fake_plaid.calls] == ["item_get"]
        item = item_row(db)
        assert item["last_attempt_at"] == "2026-10-15T15:00:00Z"
        assert item["products"] == "[]"
        log = db.execute("SELECT * FROM sync_log").fetchone()
        assert (log["ok"], log["error_code"], log["transient"]) == (0, "NETWORK_ERROR", 1)

    def test_login_required_marks_needs_attention(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        make_item(db)
        fake_plaid.queue("item_get", PlaidError("ITEM_ERROR", "ITEM_LOGIN_REQUIRED"))
        forced_sync(app_ctx, "item-1")
        assert item_row(db)["status"] == "needs_attention"

    def test_applies_duplicate_matching(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        make_item(db, "old", removed_at="2026-05-01T00:00:00Z")
        make_account(db, "old-chk", "old", name="Total Checking", mask="0001")
        make_item(db, products=[])
        fake_plaid.queue("item_get", item_get(["investments"], billed=[]))
        fake_plaid.queue("accounts_get", load("accounts_get"))
        forced_sync(app_ctx, "item-1")
        assert (
            db.execute("SELECT superseded_by FROM accounts WHERE account_id='old-chk'").fetchone()[
                0
            ]
            == "acc-chk"
        )

    def test_unknown_item_raises_and_removed_is_noop(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        with pytest.raises(NotFoundError):
            forced_sync(app_ctx, "missing")
        make_item(db, removed_at="2026-05-01T00:00:00Z")
        forced_sync(app_ctx, "item-1")
        assert fake_plaid.calls == []
