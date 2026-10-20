import json
from typing import Any

import pytest

from bizviz.context import AppContext
from bizviz.db.connection import Connection
from bizviz.plaid_fake import FakePlaidClient
from bizviz.plaid_types import PlaidError, PlaidNetworkError
from bizviz.sync.transactions import sync_item_transactions
from tests.factories import make_account, make_item, make_override, make_txn
from tests.sync.helpers import (
    data_version,
    item_get,
    item_row,
    load,
    plaid_account,
    plaid_txn,
    sync_page,
)

MUTATION = PlaidError("TRANSACTIONS_ERROR", "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION")


def setup_item(db: Connection, **kw: Any) -> None:
    make_item(db, **kw)
    make_account(db, "acc-1")


def override(db: Connection, tid: str) -> Any:
    return db.execute("SELECT * FROM tx_overrides WHERE transaction_id = ?", (tid,)).fetchone()


def txn_ids(db: Connection) -> set[str]:
    return {r[0] for r in db.execute("SELECT transaction_id FROM transactions").fetchall()}


def test_two_pages_applied_together_and_cursor_saved_after_final_page(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db, transactions_cursor="c0")
    fake_plaid.queue("transactions_sync", load("sync_page_1"))
    fake_plaid.queue("transactions_sync", load("sync_page_2"))
    fake_plaid.queue("item_get", item_get())
    before = data_version(db)

    out = sync_item_transactions(app_ctx, "item-1")

    assert (out.ok, out.added, out.modified, out.removed) == (True, 2, 0, 0)
    cursors = [c[1][1] for c in fake_plaid.calls if c[0] == "transactions_sync"]
    assert cursors == ["c0", "cursor-1"]
    item = item_row(db)
    assert item["transactions_cursor"] == "cursor-2"
    assert item["historical_complete"] == 1
    assert item["last_success_at"] == "2026-10-15T15:00:00Z"
    assert item["last_attempt_at"] == "2026-10-15T15:00:00Z"
    assert data_version(db) > before
    first = db.execute("SELECT * FROM transactions WHERE transaction_id = 'tx-1'").fetchone()
    assert first["amount"] == 12.35
    assert (first["pfc_primary"], first["pfc_detailed"]) == (
        "FOOD_AND_DRINK",
        "FOOD_AND_DRINK_COFFEE",
    )
    assert first["merchant_name"] == "Coffee Shop"
    assert first["currency"] == "USD"
    assert first["pending"] == 0
    assert txn_ids(db) == {"tx-1", "tx-2"}
    log = db.execute("SELECT * FROM sync_log WHERE job = 'transactions'").fetchall()
    assert [(r["ok"], r["item_id"]) for r in log] == [(1, "item-1")]


def test_historical_complete_only_on_final_status(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_item(db)
    fake_plaid.queue("transactions_sync", sync_page(cursor="c1", status="INITIAL_UPDATE_COMPLETE"))
    fake_plaid.queue("item_get", item_get())
    sync_item_transactions(app_ctx, "item-1")
    assert item_row(db)["historical_complete"] == 0
    assert item_row(db)["transactions_cursor"] == "c1"


def test_cursor_unchanged_when_second_page_fails(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db, transactions_cursor="c0")
    fake_plaid.queue("transactions_sync", load("sync_page_1"))
    fake_plaid.queue("transactions_sync", PlaidError("ITEM_ERROR", "INSTITUTION_DOWN"))
    out = sync_item_transactions(app_ctx, "item-1")
    assert (out.ok, out.kind, out.error_code) == (False, "transient", "INSTITUTION_DOWN")
    assert item_row(db)["transactions_cursor"] == "c0"
    assert txn_ids(db) == set()
    assert item_row(db)["status"] == "ok"
    log = db.execute("SELECT * FROM sync_log").fetchone()
    assert (log["ok"], log["error_code"], log["transient"]) == (0, "INSTITUTION_DOWN", 1)


def test_mutation_during_pagination_restarts_from_original_cursor(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db, transactions_cursor="c0")
    page1 = load("sync_page_1")
    fake_plaid.queue("transactions_sync", page1)
    fake_plaid.queue("transactions_sync", MUTATION)
    fake_plaid.queue("transactions_sync", page1)
    fake_plaid.queue("transactions_sync", load("sync_page_2"))
    fake_plaid.queue("item_get", item_get())
    out = sync_item_transactions(app_ctx, "item-1")
    assert out.ok
    assert out.added == 2
    cursors = [c[1][1] for c in fake_plaid.calls if c[0] == "transactions_sync"]
    assert cursors == ["c0", "cursor-1", "c0", "cursor-1"]
    assert item_row(db)["transactions_cursor"] == "cursor-2"


def test_three_failed_restarts_fail_as_transient(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db, transactions_cursor="c0")
    for _ in range(4):
        fake_plaid.queue("transactions_sync", load("sync_page_1"))
        fake_plaid.queue("transactions_sync", MUTATION)
    out = sync_item_transactions(app_ctx, "item-1")
    assert (out.ok, out.kind) == (False, "transient")
    assert fake_plaid.count("transactions_sync") == 8
    assert item_row(db)["transactions_cursor"] == "c0"
    assert item_row(db)["status"] == "ok"
    assert txn_ids(db) == set()


def test_new_account_in_page_inserted_before_transactions_unknown_account_skipped(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db)
    fake_plaid.queue(
        "transactions_sync",
        sync_page(
            added=[
                plaid_txn("t-new", "acc-new"),
                plaid_txn("t-ghost", "acc-ghost"),
                plaid_txn("t-new-2", "acc-new"),
            ],
            accounts=[plaid_account("acc-new", mask="0009")],
        ),
    )
    fake_plaid.queue("item_get", item_get())
    out = sync_item_transactions(app_ctx, "item-1")
    assert out.ok
    acc = db.execute("SELECT * FROM accounts WHERE account_id = 'acc-new'").fetchone()
    assert (acc["reviewed"], acc["ownership"], acc["included"]) == (0, "mine", 1)
    assert txn_ids(db) == {"t-new", "t-new-2"}


def test_removed_transactions_deleted_but_overrides_remain(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_item(db)
    make_txn(db, "gone", "acc-1", "2026-10-01", 5.0)
    make_txn(db, "stays", "acc-1", "2026-10-02", 6.0)
    make_override(db, "gone", category_id="plaid:OTHER")
    fake_plaid.queue("transactions_sync", sync_page(removed=["gone", "never-seen"]))
    fake_plaid.queue("item_get", item_get())
    out = sync_item_transactions(app_ctx, "item-1")
    assert out.removed == 2
    assert txn_ids(db) == {"stays"}
    assert override(db, "gone")["category_id"] == "plaid:OTHER"


def test_modified_transaction_is_updated(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_item(db)
    make_txn(db, "t1", "acc-1", "2026-10-01", 5.0, pending=True)
    fake_plaid.queue(
        "transactions_sync",
        sync_page(modified=[plaid_txn("t1", date="2026-10-02", amount=7.0, merchant_name="M")]),
    )
    fake_plaid.queue("item_get", item_get())
    out = sync_item_transactions(app_ctx, "item-1")
    assert out.modified == 1
    row = db.execute("SELECT * FROM transactions WHERE transaction_id = 't1'").fetchone()
    assert (row["date"], row["amount"], row["pending"], row["merchant_name"]) == (
        "2026-10-02",
        7.0,
        0,
        "M",
    )


class TestOverrideCarryOver:
    def run(
        self,
        ctx: AppContext,
        plaid: FakePlaidClient,
        added: list[dict[str, Any]],
        removed: list[str],
    ) -> None:
        plaid.queue("transactions_sync", sync_page(added=added, removed=removed))
        plaid.queue("item_get", item_get())
        assert sync_item_transactions(ctx, "item-1").ok

    def test_by_pending_transaction_id(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        setup_item(db)
        make_txn(db, "P", "acc-1", "2026-10-01", 5.0, pending=True)
        make_override(db, "P", category_id="plaid:FOOD_AND_DRINK", flow_class="spending")
        self.run(
            app_ctx,
            fake_plaid,
            [plaid_txn("Q", date="2026-10-03", amount=5.5, pending_transaction_id="P")],
            ["P"],
        )
        copied = override(db, "Q")
        assert (copied["category_id"], copied["flow_class"]) == (
            "plaid:FOOD_AND_DRINK",
            "spending",
        )
        assert override(db, "P") is not None

    def test_existing_override_never_overwritten(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        setup_item(db)
        make_txn(db, "P", "acc-1", "2026-10-01", 5.0, pending=True)
        make_txn(db, "Q", "acc-1", "2026-10-03", 5.0)
        make_override(db, "P", category_id="plaid:FOOD_AND_DRINK")
        make_override(db, "Q", category_id="plaid:TRAVEL")
        self.run(
            app_ctx,
            fake_plaid,
            [plaid_txn("Q", date="2026-10-03", pending_transaction_id="P")],
            ["P"],
        )
        assert override(db, "Q")["category_id"] == "plaid:TRAVEL"

    def test_fallback_same_amount_closest_date_wins(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        setup_item(db)
        make_txn(db, "P", "acc-1", "2026-10-01", 20.0, pending=True, name="CAFE")
        make_override(db, "P", category_id="plaid:FOOD_AND_DRINK")
        self.run(
            app_ctx,
            fake_plaid,
            [
                plaid_txn("far", date="2026-10-07", amount=20.0, name="Other"),
                plaid_txn("near", date="2026-10-03", amount=20.0, name="Other"),
                plaid_txn("wrong-amount", date="2026-10-01", amount=21.0, name="Other"),
            ],
            ["P"],
        )
        assert override(db, "near") is not None
        assert override(db, "far") is None
        assert override(db, "wrong-amount") is None

    def test_fallback_same_merchant_key_with_different_amount(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        setup_item(db)
        make_txn(db, "P", "acc-1", "2026-10-01", 20.0, pending=True, merchant_name="Cafe  Roma")
        make_override(db, "P", flow_class="spending")
        self.run(
            app_ctx,
            fake_plaid,
            [
                plaid_txn("m", date="2026-10-02", amount=24.0, merchant_name="cafe roma"),
                plaid_txn("n", date="2026-10-02", amount=24.0, merchant_name="Else"),
            ],
            ["P"],
        )
        assert override(db, "m")["flow_class"] == "spending"
        assert override(db, "n") is None

    def test_fallback_merchant_key_ignores_processor_prefix_and_digits(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        setup_item(db)
        make_txn(db, "P", "acc-1", "2026-10-01", 20.0, pending=True, name="SQ *COFFEE 1234")
        make_override(db, "P", flow_class="spending")
        self.run(
            app_ctx,
            fake_plaid,
            [
                plaid_txn("m", date="2026-10-02", amount=23.5, name="SQ *COFFEE 5678"),
                plaid_txn("n", date="2026-10-02", amount=23.5, name="SQ *TEA 5678"),
            ],
            ["P"],
        )
        assert override(db, "m")["flow_class"] == "spending"
        assert override(db, "n") is None

    def test_fallback_never_matches_on_unknown_merchant_key(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        setup_item(db)
        make_txn(db, "P1", "acc-1", "2026-10-01", 20.0, pending=True, name="")
        make_txn(db, "P2", "acc-1", "2026-10-01", 30.0, pending=True, name="#1234")
        make_override(db, "P1", flow_class="spending")
        make_override(db, "P2", flow_class="spending")
        self.run(
            app_ctx,
            fake_plaid,
            [
                plaid_txn("x", date="2026-10-02", amount=99.0, name=""),
                plaid_txn("y", date="2026-10-02", amount=98.0, name="777"),
            ],
            ["P1", "P2"],
        )
        assert db.execute("SELECT count(*) FROM tx_overrides").fetchone()[0] == 2

    def test_fallback_limits(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        setup_item(db)
        make_account(db, "acc-2", "item-1", mask="0002")
        make_txn(db, "P", "acc-1", "2026-10-01", 20.0, pending=True)
        make_override(db, "P", category_id="plaid:OTHER")
        self.run(
            app_ctx,
            fake_plaid,
            [
                plaid_txn("too-late", date="2026-10-09", amount=20.0),
                plaid_txn("other-acct", "acc-2", date="2026-10-01", amount=20.0),
                plaid_txn("pending-new", date="2026-10-01", amount=20.0, pending=True),
            ],
            ["P"],
        )
        assert db.execute("SELECT count(*) FROM tx_overrides").fetchone()[0] == 1

    def test_fallback_tie_goes_to_lowest_id_and_skips_candidates_with_override(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        setup_item(db)
        make_txn(db, "P", "acc-1", "2026-10-05", 20.0, pending=True)
        make_override(db, "P", category_id="plaid:OTHER")
        make_txn(db, "a1", "acc-1", "2026-10-05", 20.0)
        make_override(db, "a1", category_id="plaid:TRAVEL")
        self.run(
            app_ctx,
            fake_plaid,
            [
                plaid_txn("a1", date="2026-10-05", amount=20.0),
                plaid_txn("a3", date="2026-10-06", amount=20.0),
                plaid_txn("a2", date="2026-10-04", amount=20.0),
            ],
            ["P"],
        )
        assert override(db, "a1")["category_id"] == "plaid:TRAVEL"
        assert override(db, "a2")["category_id"] == "plaid:OTHER"
        assert override(db, "a3") is None

    def test_fallback_not_used_when_carried_by_pending_id(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        setup_item(db)
        make_txn(db, "P", "acc-1", "2026-10-05", 20.0, pending=True)
        make_override(db, "P", category_id="plaid:OTHER")
        self.run(
            app_ctx,
            fake_plaid,
            [
                plaid_txn("Q", date="2026-10-06", amount=20.0, pending_transaction_id="P"),
                plaid_txn("R", date="2026-10-05", amount=20.0),
            ],
            ["P"],
        )
        assert override(db, "Q") is not None
        assert override(db, "R") is None

    def test_removed_posted_transaction_does_not_carry(
        self, app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
    ) -> None:
        setup_item(db)
        make_txn(db, "P", "acc-1", "2026-10-05", 20.0, pending=False)
        make_override(db, "P", category_id="plaid:OTHER")
        self.run(app_ctx, fake_plaid, [plaid_txn("R", date="2026-10-05", amount=20.0)], ["P"])
        assert override(db, "R") is None


def test_never_calls_sync_without_transactions_product(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db, "inv-only", products=["investments"], link_mode="investment")
    make_item(db, "unavail", products=["transactions"], unavailable_steps='["transactions"]')
    make_item(db, "removed", removed_at="2026-05-01T00:00:00Z")
    make_item(db, "unknown", products=[])
    for item_id in ("inv-only", "unavail", "removed", "unknown", "missing"):
        out = sync_item_transactions(app_ctx, item_id)
        assert out.ok
    assert fake_plaid.calls == []
    assert db.execute("SELECT count(*) FROM sync_log").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("error", "status", "error_code", "kind"),
    [
        (
            PlaidError("ITEM_ERROR", "ITEM_LOGIN_REQUIRED"),
            "needs_attention",
            "ITEM_LOGIN_REQUIRED",
            "needs_attention",
        ),
        (PlaidError("ITEM_ERROR", "INSTITUTION_DOWN"), "ok", None, "transient"),
        (PlaidNetworkError("timeout"), "ok", None, "transient"),
        (PlaidError("ITEM_ERROR", "SOMETHING_NEW"), "error", "SOMETHING_NEW", "error"),
    ],
)
def test_item_error_mapping_on_sync(
    app_ctx: AppContext,
    db: Connection,
    fake_plaid: FakePlaidClient,
    error: Exception,
    status: str,
    error_code: str | None,
    kind: str,
) -> None:
    setup_item(db)
    fake_plaid.queue("transactions_sync", error)
    out = sync_item_transactions(app_ctx, "item-1")
    assert (out.ok, out.kind) == (False, kind)
    item = item_row(db)
    assert (item["status"], item["error_code"]) == (status, error_code)
    assert item["last_attempt_at"] == "2026-10-15T15:00:00Z"
    log = db.execute("SELECT * FROM sync_log").fetchone()
    assert log["transient"] == (1 if kind == "transient" else 0)
    if isinstance(error, PlaidNetworkError):
        assert log["error_code"] == "NETWORK_ERROR"


def test_unavailable_error_adds_transactions_step(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_item(db)
    fake_plaid.queue("transactions_sync", PlaidError("ITEM_ERROR", "PRODUCTS_NOT_SUPPORTED"))
    out = sync_item_transactions(app_ctx, "item-1")
    assert out.kind == "unavailable"
    assert json.loads(item_row(db)["unavailable_steps"]) == ["transactions"]
    assert item_row(db)["status"] == "ok"
    sync_item_transactions(app_ctx, "item-1")
    assert fake_plaid.count("transactions_sync") == 1


def test_item_get_updates_products_consent_and_recovers_status(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_item(db, status="needs_attention", error_code="ITEM_LOGIN_REQUIRED")
    fake_plaid.queue("transactions_sync", sync_page())
    fake_plaid.queue("item_get", item_get(["transactions", "investments"], billed=["liabilities"]))
    out = sync_item_transactions(app_ctx, "item-1")
    assert out.ok
    item = item_row(db)
    assert json.loads(item["products"]) == ["investments", "liabilities", "transactions"]
    assert item["consent_expires_at"] == "2027-03-01T00:00:00Z"
    assert (item["status"], item["error_code"]) == ("ok", None)


def test_item_get_error_maps_to_status(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_item(db)
    fake_plaid.queue("transactions_sync", sync_page())
    fake_plaid.queue(
        "item_get",
        item_get(error={"error_type": "ITEM_ERROR", "error_code": "ITEM_LOGIN_REQUIRED"}),
    )
    out = sync_item_transactions(app_ctx, "item-1")
    assert out.ok
    assert item_row(db)["status"] == "needs_attention"
    assert item_row(db)["error_code"] == "ITEM_LOGIN_REQUIRED"


def test_item_get_failure_keeps_synced_data_but_reports_failure(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    setup_item(db)
    fake_plaid.queue("transactions_sync", sync_page(added=[plaid_txn("t1")]))
    fake_plaid.queue("item_get", PlaidError("ITEM_ERROR", "INSTITUTION_DOWN"))
    out = sync_item_transactions(app_ctx, "item-1")
    assert (out.ok, out.kind) == (False, "transient")
    assert txn_ids(db) == {"t1"}
