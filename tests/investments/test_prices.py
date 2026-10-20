import sys
from datetime import UTC, date, datetime, timedelta
from types import ModuleType, SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from bizviz.context import AppContext
from bizviz.db.connection import Connection
from bizviz.price_types import PriceSourceError
from bizviz.prices import (
    YahooPriceSource,
    is_cash_like,
    is_unpriceable_symbol,
    refresh_prices,
    symbols_needing_prices,
    yahoo_symbol,
)
from tests.factories import make_inv_txn, set_now
from tests.investments.helpers import (
    A,
    FakePriceSource,
    d,
    hold,
    sec,
    set_complete,
    set_fetch_state,
    setup_account,
)


def _security(ticker: str | None, type: str = "equity", cash_eq: int = 0) -> dict[str, Any]:
    return {"ticker_symbol": ticker, "type": type, "is_cash_equivalent": cash_eq}


def _state(conn: Connection, symbol: str) -> Any:
    return conn.execute(
        "SELECT * FROM price_fetch_state WHERE yahoo_symbol = ?", (symbol,)
    ).fetchone()


@pytest.fixture
def source(app_ctx: AppContext) -> FakePriceSource:
    src = FakePriceSource()
    app_ctx.price_source = src
    return src


# --- symbol mapping -------------------------------------------------------------------------


def test_symbol_mapping() -> None:
    assert yahoo_symbol(_security("BRK.B")) == "BRK-B"
    assert yahoo_symbol(_security("vti", "etf")) == "VTI"
    assert yahoo_symbol(_security("VFIAX", "mutual fund")) == "VFIAX"
    assert yahoo_symbol(_security("BTC", "cryptocurrency")) == "BTC-USD"


def test_cash_like_securities_are_never_priced() -> None:
    assert yahoo_symbol(_security("USD", "cash")) is None
    assert yahoo_symbol(_security("SPAXX", "mutual fund", cash_eq=1)) is None
    assert is_cash_like(_security("USD", "cash"))
    assert is_cash_like(_security("SPAXX", "mutual fund", cash_eq=1))
    assert not is_cash_like(_security("AAA"))


@pytest.mark.parametrize("type", ["derivative", "fixed income", "loan", "other", None])
def test_unpriceable_types(type: str | None) -> None:
    assert yahoo_symbol(_security("XYZ", type or "")) is None


def test_no_ticker_is_unpriceable() -> None:
    assert yahoo_symbol(_security(None)) is None
    assert yahoo_symbol(_security("  ")) is None


# --- symbols_needing_prices -----------------------------------------------------------------


def test_symbols_needing_prices_only_for_complete_items(db: Connection) -> None:
    setup_account(db, "inv-1")
    sec(db, "s-aaa", "AAA")
    sec(db, "s-bbb", "BBB")
    sec(db, "s-cash", "SPAXX", type="mutual fund", is_cash_equivalent=True)
    sec(db, "s-none", None)
    sec(db, "s-opt", "OPT", type="derivative")
    sec(db, "s-btc", "BTC", type="cryptocurrency")
    hold(db, "inv-1", "s-aaa", 1, 10)
    hold(db, "inv-1", "s-cash", 5, 1)
    hold(db, "inv-1", "s-none", 5, 1)
    make_inv_txn(db, "t1", "inv-1", "s-bbb", d(-5), "buy", "buy", 1, 10)
    make_inv_txn(db, "t2", "inv-1", "s-opt", d(-5), "buy", "buy", 1, 10)
    make_inv_txn(db, "t3", "inv-1", "s-btc", d(-5), "buy", "buy", 1, 10)
    make_inv_txn(db, "t4", "inv-1", None, d(-5), "fee", None, None, 1)

    setup_account(db, "inv-2", complete=False)
    sec(db, "s-ccc", "CCC")
    hold(db, "inv-2", "s-ccc", 1, 10)
    setup_account(db, "inv-3")  # no holdings at all
    make_inv_txn(db, "t5", "inv-3", "s-ccc", d(-5), "buy", "buy", 1, 10)

    needed = symbols_needing_prices(db)
    start = A - timedelta(days=730 + 7)
    assert needed == {"AAA": (start, A), "BBB": (start, A), "BTC-USD": (start, A)}


def test_symbols_needing_prices_ranges_merge_per_symbol(db: Connection) -> None:
    sec(db, "s-aaa", "AAA")
    setup_account(db, "inv-1", anchor=date(2026, 10, 15))
    hold(db, "inv-1", "s-aaa", 1, 10, anchor=date(2026, 10, 15))
    setup_account(db, "inv-2", anchor=date(2026, 10, 20))
    hold(db, "inv-2", "s-aaa", 1, 10, anchor=date(2026, 10, 20))
    needed = symbols_needing_prices(db)
    assert needed["AAA"] == (
        date(2026, 10, 15) - timedelta(days=737),
        date(2026, 10, 20),
    )


def test_symbols_needing_prices_subtracts_cached_range(db: Connection) -> None:
    setup_account(db)
    for ticker in ("COV", "OLD", "NEW"):
        sec(db, f"s-{ticker}", ticker)
        hold(db, "inv-1", f"s-{ticker}", 1, 10)
    start = A - timedelta(days=737)
    set_fetch_state(
        db, "COV", status="ok", consecutive_failures=0,
        first_date=start.isoformat(), last_date=A.isoformat(),
    )  # fmt: skip
    set_fetch_state(
        db, "OLD", status="ok", consecutive_failures=0,
        first_date=start.isoformat(), last_date=d(-5),
    )  # fmt: skip
    set_fetch_state(
        db, "NEW", status="ok", consecutive_failures=0,
        first_date=d(-100), last_date=A.isoformat(),
    )  # fmt: skip
    needed = symbols_needing_prices(db)
    assert "COV" not in needed
    assert needed["OLD"] == (A - timedelta(days=4), A)
    assert needed["NEW"] == (start, A - timedelta(days=101))


def test_failed_symbols_are_skipped_for_six_hours(db: Connection) -> None:
    setup_account(db)
    sec(db, "s-zzz", "ZZZ")
    hold(db, "inv-1", "s-zzz", 1, 10)
    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    set_fetch_state(
        db, "ZZZ", status="no_data", consecutive_failures=1,
        last_attempt_at="2026-10-15T08:00:00Z",
    )  # fmt: skip
    assert symbols_needing_prices(db, now) == {}
    assert "ZZZ" in symbols_needing_prices(db, now + timedelta(hours=3))


def test_is_unpriceable_symbol_needs_three_failures(db: Connection) -> None:
    assert not is_unpriceable_symbol(db, "NOPE")
    set_fetch_state(db, "ZZZ", status="no_data", consecutive_failures=2)
    assert not is_unpriceable_symbol(db, "ZZZ")
    set_fetch_state(db, "ZZZ", status="no_data", consecutive_failures=3)
    assert is_unpriceable_symbol(db, "ZZZ")
    set_fetch_state(db, "ZZZ", status="error", consecutive_failures=4)
    assert is_unpriceable_symbol(db, "ZZZ")
    set_fetch_state(db, "ZZZ", status="ok", consecutive_failures=0)
    assert not is_unpriceable_symbol(db, "ZZZ")


# --- refresh_prices -------------------------------------------------------------------------


def test_refresh_is_noop_without_price_source(app_ctx: AppContext) -> None:
    assert app_ctx.price_source is None
    out = refresh_prices(app_ctx)
    assert (out.fetched, out.failed, out.stopped_early) == (0, 0, False)


def test_raw_close_conversion_for_split(
    app_ctx: AppContext, db: Connection, source: FakePriceSource
) -> None:
    anchor = date(2024, 6, 14)
    setup_account(db, anchor=anchor)
    sec(db, "s-nvda", "NVDA")
    hold(db, "inv-1", "s-nvda", 1, 120.0, anchor=anchor)
    source.closes["NVDA"] = [
        (date(2024, 6, 7), 120.888),
        (date(2024, 6, 11), 121.79),
    ]
    source.split_map["NVDA"] = [(date(2024, 6, 10), 10.0)]

    out = refresh_prices(app_ctx, sleep=lambda s: None)

    assert out.fetched == 1
    rows = dict(
        db.execute("SELECT date, raw_close FROM prices WHERE yahoo_symbol='NVDA'").fetchall()
    )
    assert rows["2024-06-07"] == pytest.approx(1208.88)
    assert rows["2024-06-11"] == pytest.approx(121.79)
    split = db.execute("SELECT date, ratio FROM price_splits WHERE yahoo_symbol='NVDA'").fetchall()
    assert [tuple(r) for r in split] == [("2024-06-10", 10.0)]


def test_fetch_groups_by_range_and_uses_exclusive_end(
    app_ctx: AppContext, db: Connection, source: FakePriceSource
) -> None:
    setup_account(db, "inv-1", anchor=A)
    setup_account(db, "inv-2", anchor=date(2026, 10, 1))
    for ticker in ("AAA", "BBB", "CCC"):
        sec(db, f"s-{ticker}", ticker)
    hold(db, "inv-1", "s-AAA", 1, 10)
    hold(db, "inv-1", "s-BBB", 1, 10)
    hold(db, "inv-2", "s-CCC", 1, 10, anchor=date(2026, 10, 1))
    for ticker in ("AAA", "BBB", "CCC"):
        source.closes[ticker] = [(date(2026, 9, 30), 5.0)]
        source.split_map[ticker] = [(date(2025, 1, 2), 2.0)]

    out = refresh_prices(app_ctx, sleep=lambda s: None)

    assert out.fetched == 3 and out.failed == 0
    assert len(source.download_calls) == 2
    by_symbols = {tuple(sorted(c[0])): c for c in source.download_calls}
    start1 = A - timedelta(days=737)
    assert by_symbols[("AAA", "BBB")][1:] == (start1, A + timedelta(days=1))
    start2 = date(2026, 10, 1) - timedelta(days=737)
    assert by_symbols[("CCC",)][1:] == (start2, date(2026, 10, 2))
    assert sorted(source.split_calls) == ["AAA", "BBB", "CCC"]
    stored = db.execute("SELECT DISTINCT yahoo_symbol FROM price_splits").fetchall()
    assert sorted(r[0] for r in stored) == ["AAA", "BBB", "CCC"]

    # Everything is cached now, so a second run fetches nothing.
    source.download_calls.clear()
    refresh_prices(app_ctx, sleep=lambda s: None)
    assert source.download_calls == []
    state = _state(db, "AAA")
    assert (state["status"], state["consecutive_failures"]) == ("ok", 0)
    assert state["first_date"] == start1.isoformat()
    # last_date is the requested end even though the last trading day was earlier
    assert state["last_date"] == A.isoformat()


def test_incremental_fetch_requests_only_uncovered_range(
    app_ctx: AppContext, db: Connection, source: FakePriceSource
) -> None:
    setup_account(db)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 1, 10)
    start = A - timedelta(days=737)
    set_fetch_state(
        db, "AAA", status="ok", consecutive_failures=0,
        first_date=start.isoformat(), last_date=d(-5),
    )  # fmt: skip
    source.closes["AAA"] = []  # a weekend-only range has no rows; that is still a success
    out = refresh_prices(app_ctx, sleep=lambda s: None)
    assert source.download_calls == [(["AAA"], A - timedelta(days=4), A + timedelta(days=1))]
    assert out.fetched == 1
    state = _state(db, "AAA")
    assert (state["status"], state["last_date"]) == ("ok", A.isoformat())
    assert state["first_date"] == start.isoformat()


def test_no_data_then_unpriceable_then_recovery(
    app_ctx: AppContext, db: Connection, source: FakePriceSource
) -> None:
    setup_account(db)
    sec(db, "s-zzz", "ZZZ")
    hold(db, "inv-1", "s-zzz", 1, 10)

    out = refresh_prices(app_ctx, sleep=lambda s: None)
    assert (out.fetched, out.failed) == (0, 1)
    state = _state(db, "ZZZ")
    assert (state["status"], state["consecutive_failures"]) == ("no_data", 1)
    assert db.execute("SELECT count(*) FROM prices").fetchone()[0] == 0

    # not retried within 6 hours
    set_now(app_ctx, datetime(2026, 10, 15, 20, 0, tzinfo=UTC))
    refresh_prices(app_ctx, sleep=lambda s: None)
    assert len(source.download_calls) == 1

    set_now(app_ctx, datetime(2026, 10, 15, 22, 0, tzinfo=UTC))
    refresh_prices(app_ctx, sleep=lambda s: None)
    assert len(source.download_calls) == 2
    assert not is_unpriceable_symbol(db, "ZZZ")
    set_now(app_ctx, datetime(2026, 10, 16, 6, 0, tzinfo=UTC))
    refresh_prices(app_ctx, sleep=lambda s: None)
    assert _state(db, "ZZZ")["consecutive_failures"] == 3
    assert is_unpriceable_symbol(db, "ZZZ")

    # a later success resets the counter
    source.closes["ZZZ"] = [(date(2026, 10, 14), 9.0)]
    set_now(app_ctx, datetime(2026, 10, 16, 15, 0, tzinfo=UTC))
    out = refresh_prices(app_ctx, sleep=lambda s: None)
    assert out.fetched == 1
    state = _state(db, "ZZZ")
    assert (state["status"], state["consecutive_failures"]) == ("ok", 0)
    assert not is_unpriceable_symbol(db, "ZZZ")


def test_source_errors_are_retried_with_backoff(
    app_ctx: AppContext, db: Connection, source: FakePriceSource
) -> None:
    setup_account(db)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 1, 10)
    source.closes["AAA"] = [(date(2026, 10, 14), 9.0)]
    source.download_failures = 2
    sleeps: list[float] = []
    out = refresh_prices(app_ctx, sleep=sleeps.append)
    assert sleeps == [2, 8]
    assert len(source.download_calls) == 3
    assert out.fetched == 1


def test_split_errors_are_retried(
    app_ctx: AppContext, db: Connection, source: FakePriceSource
) -> None:
    setup_account(db)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 1, 10)
    source.closes["AAA"] = [(date(2026, 10, 14), 9.0)]
    source.split_failures = 1
    sleeps: list[float] = []
    out = refresh_prices(app_ctx, sleep=sleeps.append)
    assert sleeps == [2]
    assert out.fetched == 1


def test_exhausted_retries_mark_error(
    app_ctx: AppContext, db: Connection, source: FakePriceSource
) -> None:
    setup_account(db)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 1, 10)
    source.download_failures = 100
    sleeps: list[float] = []
    out = refresh_prices(app_ctx, sleep=sleeps.append)
    assert sleeps == [2, 8, 30]
    assert len(source.download_calls) == 4
    assert (out.fetched, out.failed) == (0, 1)
    state = _state(db, "AAA")
    assert (state["status"], state["consecutive_failures"]) == ("error", 1)


def test_job_stops_after_budget(
    app_ctx: AppContext, db: Connection, source: FakePriceSource
) -> None:
    for i in range(4):
        acc = f"inv-{i}"
        anchor = A - timedelta(days=i)
        setup_account(db, acc, anchor=anchor)
        sec(db, f"s-{i}", f"T{i}")
        hold(db, acc, f"s-{i}", 1, 10, anchor=anchor)
    source.download_failures = 100

    def sleep(seconds: float) -> None:
        set_now(app_ctx, app_ctx.now() + timedelta(seconds=seconds))

    out = refresh_prices(app_ctx, sleep=sleep, budget_seconds=60)
    assert out.stopped_early
    # each failing group burns 40 s of backoff: groups start at 0 and 40; the one at 80 is skipped
    assert out.failed == 2
    assert len(source.download_calls) == 8


def test_symbols_not_needed_are_not_fetched(
    app_ctx: AppContext, db: Connection, source: FakePriceSource
) -> None:
    setup_account(db)
    sec(db, "s-aaa", "AAA")
    hold(db, "inv-1", "s-aaa", 1, 10)
    set_complete(db, "item-inv-1", False)
    refresh_prices(app_ctx, sleep=lambda s: None)
    assert source.download_calls == []


# --- YahooPriceSource (yfinance is faked; the real library is never imported) ---------------


@pytest.fixture
def fake_yf(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(frame=None, raise_exc=None, kwargs=None, splits=None)
    module = ModuleType("yfinance")

    def download(symbols: Any, **kwargs: Any) -> Any:
        state.kwargs = {"symbols": symbols, **kwargs}
        if state.raise_exc:
            raise state.raise_exc
        return state.frame

    class Ticker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def splits(self) -> Any:
            if state.raise_exc:
                raise state.raise_exc
            return state.splits

    module.download = download  # type: ignore[attr-defined]
    module.Ticker = Ticker  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yfinance", module)
    return state


def test_yahoo_download_multi_symbol(fake_yf: SimpleNamespace) -> None:
    idx = pd.to_datetime(["2024-06-06", "2024-06-07"])
    cols = pd.MultiIndex.from_product([["AAA", "BBB"], ["Close", "Volume"]])
    frame = pd.DataFrame(
        [[1.5, 10, 2.5, 20], [float("nan"), 11, 2.75, 21]], index=idx, columns=cols
    )
    fake_yf.frame = frame
    out = YahooPriceSource().download_closes(
        ["AAA", "BBB", "CCC"], date(2024, 6, 1), date(2024, 6, 8)
    )
    assert out == {
        "AAA": [(date(2024, 6, 6), 1.5)],
        "BBB": [(date(2024, 6, 6), 2.5), (date(2024, 6, 7), 2.75)],
        "CCC": [],
    }
    assert fake_yf.kwargs == {
        "symbols": ["AAA", "BBB", "CCC"],
        "start": "2024-06-01",
        "end": "2024-06-08",
        "auto_adjust": False,
        "actions": True,
        "group_by": "ticker",
        "progress": False,
        "threads": False,
    }


def test_yahoo_download_single_symbol_flat_and_grouped(fake_yf: SimpleNamespace) -> None:
    idx = pd.to_datetime(["2024-06-06"])
    fake_yf.frame = pd.DataFrame({"Close": [3.0], "Volume": [1]}, index=idx)
    src = YahooPriceSource()
    assert src.download_closes(["AAA"], date(2024, 6, 1), date(2024, 6, 8)) == {
        "AAA": [(date(2024, 6, 6), 3.0)]
    }
    fake_yf.frame = pd.DataFrame(
        [[3.0, 1]], index=idx, columns=pd.MultiIndex.from_product([["AAA"], ["Close", "Volume"]])
    )
    assert src.download_closes(["AAA"], date(2024, 6, 1), date(2024, 6, 8)) == {
        "AAA": [(date(2024, 6, 6), 3.0)]
    }


def test_yahoo_download_empty_frame(fake_yf: SimpleNamespace) -> None:
    fake_yf.frame = pd.DataFrame()
    assert YahooPriceSource().download_closes(["AAA"], date(2024, 6, 1), date(2024, 6, 8)) == {
        "AAA": []
    }


def test_yahoo_splits(fake_yf: SimpleNamespace) -> None:
    fake_yf.splits = pd.Series(
        [10.0, 2.0],
        index=pd.to_datetime(["2024-06-10", "2020-08-31"]).tz_localize("America/New_York"),
    )
    assert YahooPriceSource().splits("NVDA") == [
        (date(2024, 6, 10), 10.0),
        (date(2020, 8, 31), 2.0),
    ]


def test_yahoo_errors_become_price_source_error(fake_yf: SimpleNamespace) -> None:
    fake_yf.raise_exc = RuntimeError("YFRateLimitError: Too Many Requests")
    src = YahooPriceSource()
    with pytest.raises(PriceSourceError):
        src.download_closes(["AAA"], date(2024, 6, 1), date(2024, 6, 8))
    with pytest.raises(PriceSourceError):
        src.splits("AAA")
