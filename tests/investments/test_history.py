import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest

from bizviz.analytics.investment_history import rebuild_estimates
from bizviz.db.connection import Connection, get_meta, write_unit
from tests.factories import make_balance_snapshot, make_inv_txn
from tests.investments.helpers import (
    A,
    d,
    hold,
    put_prices,
    put_split,
    sec,
    set_complete,
    set_fetch_state,
    setup_account,
)

NY = ZoneInfo("America/New_York")
WINDOW_START = A - timedelta(days=730)
NO_ACTIVITY = "No investment activity was reported; the estimate assumes holdings were unchanged."
INCONSISTENT = "Some reported activity is inconsistent; the estimate may be incomplete."


def rebuild(db: Connection, account_ids: list[str] | None = None) -> None:
    rebuild_estimates(db, date(2026, 10, 19), NY, account_ids)


def est(db: Connection, account_id: str = "inv-1") -> dict[str, float]:
    rows = db.execute(
        "SELECT date, value FROM investment_estimates WHERE account_id = ?", (account_id,)
    ).fetchall()
    return {r["date"]: r["value"] for r in rows}


def meta(db: Connection, account_id: str = "inv-1") -> dict[str, object]:
    row = db.execute(
        "SELECT * FROM investment_estimate_meta WHERE account_id = ?", (account_id,)
    ).fetchone()
    assert row is not None
    out = dict(row)
    out["warnings"] = json.loads(out["warnings"])
    return out


def flat_prices(db: Connection, symbol: str = "AAA", price: float = 100.0) -> None:
    put_prices(db, symbol, WINDOW_START - timedelta(days=10), A, price)


def simple_account(db: Connection, quantity: float = 10, balance: float = 1000) -> None:
    """AAA at $100 flat; `balance` is the account's reported total."""
    setup_account(db, balance=balance)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", quantity, 100)
    flat_prices(db)


def test_basic_reconstruction_with_weekend_carry_forward(db: Connection) -> None:
    setup_account(db, balance=1650)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 10, 115)  # 2026-10-15 closes at 115
    put_prices(db, "AAA", WINDOW_START - timedelta(days=10), A, lambda x: 100.0 + x.day,
               weekdays_only=True)  # fmt: skip
    make_inv_txn(db, "t1", "inv-1", "s-aaa", d(-10), "buy", "buy", 4, 400)

    rebuild(db)

    values = est(db)
    # A-11 is Sunday 2026-10-04: 6 shares at Friday's close (102) + 500 + 400 cash
    assert values[d(-11)] == pytest.approx(6 * 102 + 900)
    assert values[d(-10)] == pytest.approx(10 * 105 + 500)
    assert values[d(-1)] == pytest.approx(10 * 114 + 500)
    assert len(values) == 730
    assert min(values) == WINDOW_START.isoformat()
    assert max(values) == d(-1)
    m = meta(db)
    assert (m["anchor_date"], m["window_start"], m["window_end"], m["status"]) == (
        A.isoformat(), WINDOW_START.isoformat(), d(-1), "ok",
    )  # fmt: skip
    assert (m["excluded_count"], m["excluded_value"]) == (0, 0)


def test_anchor_cash_is_balance_minus_noncash_holdings(db: Connection) -> None:
    # No cash holding at all; balance 1300 minus 1000 of AAA leaves 300 cash.
    simple_account(db, balance=1300)
    rebuild(db)
    assert est(db)[d(-1)] == pytest.approx(1300)
    assert est(db)[d(-300)] == pytest.approx(1300)


def test_anchor_cash_with_cash_holding_and_balance(db: Connection) -> None:
    simple_account(db, balance=1300)
    sec(db, "s-cash", "SPAXX", type="mutual fund", is_cash_equivalent=True)
    hold(db, "inv-1", "s-cash", 300, 1)
    rebuild(db)
    assert est(db)[d(-1)] == pytest.approx(1300)


def test_anchor_cash_without_balance_snapshot_uses_cash_holdings(db: Connection) -> None:
    setup_account(db)  # no balance snapshot
    sec(db, "s-aaa", "AAA")
    sec(db, "s-cash", "USD", type="cash")
    hold(db, "inv-1", "s-aaa", 10, 100)
    hold(db, "inv-1", "s-cash", 250, 1)
    flat_prices(db)
    rebuild(db)
    assert est(db)[d(-1)] == pytest.approx(1250)


def test_cash_equivalent_trades_skipped_and_dividends_change_cash(db: Connection) -> None:
    simple_account(db)
    sec(db, "s-cash", "SPAXX", type="mutual fund", is_cash_equivalent=True)
    make_inv_txn(db, "t1", "inv-1", "s-cash", d(-5), "buy", "buy", 50, 50)
    make_inv_txn(db, "t2", "inv-1", "s-cash", d(-4), "sell", "sell", -20, -20)
    make_inv_txn(db, "t3", "inv-1", "s-aaa", d(-3), "cash", "dividend", 0, -5)
    make_inv_txn(db, "t4", "inv-1", "s-cash", d(-2), "cash", "dividend", None, -2)
    rebuild(db)
    values = est(db)
    assert values[d(-1)] == pytest.approx(1000)
    assert values[d(-2)] == pytest.approx(1000)
    assert values[d(-3)] == pytest.approx(1000 - 2)  # t4 undone, t3 on this date is not
    assert values[d(-4)] == pytest.approx(1000 - 2 - 5)
    assert values[d(-10)] == pytest.approx(1000 - 7)  # cash-equivalent swaps never moved cash


def test_yahoo_split_divides_quantity_and_plaid_split_rows_are_ignored(db: Connection) -> None:
    setup_account(db, balance=1000)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 10, 100)
    split_day = A - timedelta(days=20)
    put_prices(db, "AAA", WINDOW_START - timedelta(days=10), A,
               lambda x: 200.0 if x < split_day else 100.0)  # fmt: skip
    put_split(db, "AAA", split_day, 2.0)
    make_inv_txn(db, "sp", "inv-1", "s-aaa", d(-20), "transfer", "split", 5, 0)
    make_inv_txn(db, "sd", "inv-1", "s-aaa", d(-20), "transfer", "stock distribution", 5, 0)
    rebuild(db)
    values = est(db)
    assert values[d(-19)] == pytest.approx(1000)
    assert values[d(-20)] == pytest.approx(1000)
    assert values[d(-21)] == pytest.approx(1000)  # 5 pre-split shares at the raw close of 200
    assert values[d(-200)] == pytest.approx(1000)


def test_split_look_alike_rows_near_a_yahoo_split_are_ignored(db: Connection) -> None:
    setup_account(db, balance=1000)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 10, 100)
    split_day = A - timedelta(days=20)
    put_prices(db, "AAA", WINDOW_START - timedelta(days=10), A,
               lambda x: 200.0 if x < split_day else 100.0)  # fmt: skip
    put_split(db, "AAA", split_day, 2.0)
    make_inv_txn(db, "adj", "inv-1", "s-aaa", d(-19), "adjustment", None, 5.02, 0)
    rebuild(db)
    assert est(db)[d(-21)] == pytest.approx(1000)


def test_non_matching_row_near_a_split_still_applies(db: Connection) -> None:
    setup_account(db, balance=1000)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 10, 100)
    split_day = A - timedelta(days=20)
    put_prices(db, "AAA", WINDOW_START - timedelta(days=10), A,
               lambda x: 200.0 if x < split_day else 100.0)  # fmt: skip
    put_split(db, "AAA", split_day, 2.0)
    make_inv_txn(db, "adj", "inv-1", "s-aaa", d(-19), "transfer", None, 1, 0)
    rebuild(db)
    assert est(db)[d(-21)] == pytest.approx(4.5 * 200)


def test_adjustment_far_from_split_is_not_ignored(db: Connection) -> None:
    setup_account(db, balance=1000)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 10, 100)
    flat_prices(db)
    put_split(db, "AAA", A - timedelta(days=20), 2.0)  # unrelated, 10 days away from the row
    make_inv_txn(db, "adj", "inv-1", "s-aaa", d(-5), "adjustment", None, 5, 0)
    rebuild(db)
    # the split halves 5 pre-split shares; prices are flat here so only the quantity matters
    assert est(db)[d(-6)] == pytest.approx(5 * 100)
    assert est(db)[d(-30)] == pytest.approx(2.5 * 100)


def test_local_date_of_capture_decides_t(db: Connection) -> None:
    # Captured 21:30 Eastern on Oct 15 == 01:30 UTC on Oct 16: T is Oct 15, not Oct 16.
    setup_account(db, balance=1000)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 10, 100, captured_at="2026-10-16T01:30:00Z")
    flat_prices(db)
    make_inv_txn(db, "on-a", "inv-1", "s-aaa", d(0), "buy", "buy", 2, 200)
    make_inv_txn(db, "after-t", "inv-1", "s-aaa", d(1), "buy", "buy", 5, 100)
    rebuild(db)
    # only the Oct 15 buy is undone: 8 shares + 200 cash; the Oct 16 buy is never undone
    assert est(db)[d(-1)] == pytest.approx(800 + 200)
    assert est(db)[d(-100)] == pytest.approx(800 + 200)


def test_transactions_between_a_and_t_are_undone(db: Connection) -> None:
    setup_account(db, balance=1000)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 10, 100, captured_at="2026-10-18T14:00:00Z")
    flat_prices(db)
    make_inv_txn(db, "between", "inv-1", "s-aaa", d(1), "buy", "buy", 3, 150)
    make_inv_txn(db, "after-t", "inv-1", "s-aaa", d(4), "buy", "buy", 5, 100)
    rebuild(db)
    assert est(db)[d(-1)] == pytest.approx(700 + 150)


def test_split_between_a_and_t_is_undone_in_order_with_transactions(db: Connection) -> None:
    setup_account(db, balance=1000)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 10, 100, captured_at="2026-10-18T14:00:00Z")
    split_day = A + timedelta(days=2)
    put_prices(db, "AAA", WINDOW_START - timedelta(days=10), A, 200.0)
    put_split(db, "AAA", split_day, 2.0)
    make_inv_txn(db, "pre-split-buy", "inv-1", "s-aaa", d(1), "buy", "buy", 1, 200)
    rebuild(db)
    # split undone first (10 -> 5), then the buy made in pre-split shares (5 -> 4)
    assert est(db)[d(-1)] == pytest.approx(4 * 200 + 200)


def test_unpriceable_security_is_excluded_but_dividends_still_apply(db: Connection) -> None:
    setup_account(db, balance=1050 + 1000)
    sec(db, "s-aaa", "AAA")
    sec(db, "s-private", None, name="Private fund")
    hold(db, "inv-1", "s-aaa", 10, 100)
    hold(db, "inv-1", "s-private", 5, 10)
    flat_prices(db)
    make_inv_txn(db, "buy", "inv-1", "s-private", d(-5), "buy", "buy", 3, 30)
    make_inv_txn(db, "sell", "inv-1", "s-private", d(-6), "sell", "sell", -1, -12)
    make_inv_txn(db, "div", "inv-1", "s-private", d(-3), "cash", "dividend", None, -2)
    rebuild(db)
    values = est(db)
    # cash is 2050 - 1000 - 50 = 1000; the private fund never enters values or cash
    assert values[d(-1)] == pytest.approx(2000)
    assert values[d(-3)] == pytest.approx(2000)
    assert values[d(-4)] == pytest.approx(1998)
    assert values[d(-10)] == pytest.approx(1998)
    m = meta(db)
    assert (m["excluded_count"], m["excluded_value"]) == (1, 50)


def test_sanity_check_marks_inconsistent_price_unpriceable(db: Connection) -> None:
    setup_account(db, balance=1000)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 10, 100)
    put_prices(db, "AAA", WINDOW_START - timedelta(days=10), A, 70.0)  # 30% below Plaid's price
    rebuild(db)
    m = meta(db)
    assert m["excluded_count"] == 1
    assert m["excluded_value"] == 1000
    assert "Price data for 1 holdings looked inconsistent and was excluded." in m["warnings"]
    assert est(db) == {}  # no cash and nothing priceable: every day is a leading zero


def test_sanity_check_within_tolerance_passes(db: Connection) -> None:
    setup_account(db, balance=1000)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 10, 100)
    put_prices(db, "AAA", WINDOW_START - timedelta(days=10), A, 120.0)
    rebuild(db)
    assert meta(db)["excluded_count"] == 0
    assert est(db)[d(-1)] == pytest.approx(1200)


def test_negative_quantity_is_clamped_with_warning(db: Connection) -> None:
    setup_account(db, balance=500)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 2, 100)
    flat_prices(db)
    make_inv_txn(db, "t1", "inv-1", "s-aaa", d(-5), "buy", "buy", 5, 0)
    rebuild(db)
    values = est(db)
    assert values[d(-6)] == pytest.approx(300)  # cash 300 only; quantity clamped to 0, not -3
    assert INCONSISTENT in meta(db)["warnings"]


def test_negative_cash_warns_without_clamping(db: Connection) -> None:
    simple_account(db, balance=1000)
    make_inv_txn(db, "t1", "inv-1", "s-aaa", d(-10), "sell", "sell", -4, -400)
    rebuild(db)
    # before the sale: 14 shares and -400 cash
    assert est(db)[d(-11)] == pytest.approx(1400 - 400)
    assert INCONSISTENT in meta(db)["warnings"]


def test_warning_when_no_investment_transactions(db: Connection) -> None:
    simple_account(db)
    rebuild(db)
    assert meta(db)["warnings"] == [NO_ACTIVITY]


def test_warning_when_activity_only_reported_recently(db: Connection) -> None:
    simple_account(db)
    make_inv_txn(db, "t1", "inv-1", "s-aaa", d(-100), "buy", "buy", 1, 100)
    rebuild(db)
    assert meta(db)["warnings"] == [
        f"Investment activity was only reported from {d(-100)};"
        " earlier estimates assume no activity before then."
    ]


def test_no_warning_when_activity_reaches_window_start(db: Connection) -> None:
    simple_account(db)
    make_inv_txn(db, "t1", "inv-1", "s-aaa", (WINDOW_START + timedelta(days=31)).isoformat(),
                 "buy", "buy", 1, 100)  # fmt: skip
    rebuild(db)
    assert meta(db)["warnings"] == []


def test_downloading_status_while_history_incomplete(db: Connection) -> None:
    simple_account(db)
    set_complete(db, "item-inv-1", False)
    with write_unit(db):
        db.execute("INSERT INTO investment_estimates VALUES ('inv-1', '2026-01-01', 5)")
    rebuild(db)
    assert est(db) == {}
    m = meta(db)
    assert m["status"] == "downloading"
    assert m["warnings"] == []
    assert (m["anchor_date"], m["excluded_count"]) == (None, 0)

    set_complete(db, "item-inv-1", True)
    rebuild(db)
    assert meta(db)["status"] == "ok"
    assert est(db)


def test_account_without_holdings_has_no_meta(db: Connection) -> None:
    setup_account(db)
    with write_unit(db):
        db.execute(
            "INSERT INTO investment_estimate_meta(account_id, status) VALUES ('inv-1', 'ok')"
        )
    rebuild(db)
    assert db.execute("SELECT count(*) FROM investment_estimate_meta").fetchone()[0] == 0
    assert est(db) == {}


def test_leading_non_positive_days_dropped_and_no_dates_from_anchor(db: Connection) -> None:
    setup_account(db, balance=1000)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 10, 100)
    flat_prices(db)
    make_inv_txn(db, "dep", "inv-1", None, d(-20), "cash", "deposit", None, -1000)
    make_inv_txn(db, "buy", "inv-1", "s-aaa", d(-10), "buy", "buy", 10, 1000)
    rebuild(db)
    values = est(db)
    assert min(values) == d(-20)
    assert values[d(-20)] == pytest.approx(1000)
    assert d(-21) not in values
    assert all(day < A.isoformat() for day in values)


def test_rebuild_replaces_previous_rows_and_bumps_data_version(db: Connection) -> None:
    simple_account(db)
    with write_unit(db):
        db.execute("INSERT INTO investment_estimates VALUES ('inv-1', '2020-01-01', 5)")
        db.execute("INSERT INTO investment_estimates VALUES ('inv-1', '2026-10-15', 5)")
    before = int(get_meta(db, "data_version") or 0)
    rebuild(db)
    assert "2020-01-01" not in est(db) and "2026-10-15" not in est(db)
    assert int(get_meta(db, "data_version") or 0) == before + 1
    first = est(db)
    rebuild(db)
    assert est(db) == first
    assert int(get_meta(db, "data_version") or 0) == before + 2


def test_account_ids_restricts_the_rebuild(db: Connection) -> None:
    simple_account(db)
    setup_account(db, "inv-2", balance=1000)
    hold(db, "inv-2", "s-aaa", 10, 100)
    rebuild(db, ["inv-2"])
    assert est(db, "inv-1") == {}
    assert est(db, "inv-2")
    rebuild(db)
    assert est(db, "inv-1")


def test_missing_prices_make_security_unpriceable(db: Connection) -> None:
    setup_account(db, balance=1500)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 10, 100)
    # Prices only cover the last 50 days: older days with shares have no usable close.
    put_prices(db, "AAA", A - timedelta(days=50), A, 100.0)
    rebuild(db)
    assert meta(db)["excluded_count"] == 1
    assert est(db)[d(-300)] == pytest.approx(500)


def test_failed_symbol_is_excluded(db: Connection) -> None:
    simple_account(db, balance=1500)
    set_fetch_state(db, "AAA", status="no_data", consecutive_failures=3)
    rebuild(db)
    m = meta(db)
    assert (m["excluded_count"], m["excluded_value"]) == (1, 1000)
    assert est(db)[d(-1)] == pytest.approx(500)


def test_brk_b_uses_dashed_symbol(db: Connection) -> None:
    setup_account(db, balance=1000)
    sec(db, "s-brk", "BRK.B")
    hold(db, "inv-1", "s-brk", 10, 100)
    flat_prices(db, "BRK-B")
    rebuild(db)
    assert meta(db)["excluded_count"] == 0
    assert est(db)[d(-1)] == pytest.approx(1000)


def test_hold_missing_balance_none_is_ignored(db: Connection) -> None:
    setup_account(db)
    make_balance_snapshot(db, "inv-1", A.isoformat(), None)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 10, 100)
    flat_prices(db)
    rebuild(db)
    assert est(db)[d(-1)] == pytest.approx(1000)


def test_symbols_needing_prices_matches_estimate_windows(db: Connection) -> None:
    from bizviz.prices import symbols_needing_prices

    simple_account(db)
    setup_account(db, "inv-2", complete=False)
    sec(db, "s-bbb", "BBB")
    hold(db, "inv-2", "s-bbb", 1, 10)
    assert set(symbols_needing_prices(db)) == {"AAA"}
