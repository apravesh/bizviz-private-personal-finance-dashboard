"""Yahoo Finance price source and the raw-close price cache.

Privacy rule: log only counts, never ticker names.
"""

import logging
import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import partial
from typing import Any

from bizviz.context import AppContext
from bizviz.db.connection import Connection, write_unit
from bizviz.price_types import PriceSourceError

log = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.WARNING)

WINDOW_DAYS = 730
CARRY_DAYS = 7
RETRY_DELAYS = (2, 8, 30)
RETRY_AFTER_FAILURE = timedelta(hours=6)
FAILURES_BEFORE_UNPRICEABLE = 3
_YAHOO_SYMBOL_TYPES = ("equity", "etf", "mutual fund")


def is_cash_like(security: Any) -> bool:
    """Cash-equivalent securities (and type 'cash') are cash, not positions."""
    return bool(security["is_cash_equivalent"]) or (security["type"] or "").lower() == "cash"


def yahoo_symbol(security: Any) -> str | None:
    """Yahoo symbol for a securities row, or None when unpriceable (or cash-like)."""
    if is_cash_like(security):
        return None
    ticker = (security["ticker_symbol"] or "").strip()
    if not ticker:
        return None
    sec_type = (security["type"] or "").lower()
    if sec_type in _YAHOO_SYMBOL_TYPES:
        return ticker.replace(".", "-").upper()
    if sec_type == "cryptocurrency":
        return f"{ticker.upper()}-USD"
    return None


class YahooPriceSource:
    def download_closes(
        self, symbols: list[str], start: date, end_exclusive: date
    ) -> dict[str, list[tuple[date, float]]]:
        try:
            import yfinance

            frame = yfinance.download(
                symbols,
                start=start.isoformat(),
                end=end_exclusive.isoformat(),
                auto_adjust=False,
                actions=True,
                group_by="ticker",
                progress=False,
                threads=False,
            )
            return {symbol: _extract_closes(frame, symbol) for symbol in symbols}
        except Exception as exc:
            raise PriceSourceError(type(exc).__name__) from exc

    def splits(self, symbol: str) -> list[tuple[date, float]]:
        try:
            import yfinance

            series = yfinance.Ticker(symbol).splits
            out: list[tuple[date, float]] = []
            for ts, ratio in series.items():
                if ratio is not None and float(ratio) > 0:
                    out.append((ts.date(), float(ratio)))
            return out
        except Exception as exc:
            raise PriceSourceError(type(exc).__name__) from exc


def _extract_closes(frame: Any, symbol: str) -> list[tuple[date, float]]:
    if frame is None or len(frame) == 0:
        return []
    columns = frame.columns
    if columns.nlevels > 1:
        if symbol not in columns.get_level_values(0):
            return []
        sub = frame[symbol]
    else:
        sub = frame
    if "Close" not in sub.columns:
        return []
    out: list[tuple[date, float]] = []
    for ts, value in sub["Close"].items():
        if value is None or math.isnan(float(value)):
            continue
        out.append((ts.date(), float(value)))
    return out


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_unpriceable_symbol(conn: Connection, yahoo_symbol: str) -> bool:
    row = conn.execute(
        "SELECT status, consecutive_failures FROM price_fetch_state WHERE yahoo_symbol = ?",
        (yahoo_symbol,),
    ).fetchone()
    if row is None:
        return False
    return row["status"] in ("no_data", "error") and (
        row["consecutive_failures"] >= FAILURES_BEFORE_UNPRICEABLE
    )


def eligible_accounts(conn: Connection) -> list[str]:
    """Included investment accounts whose item has finished downloading its history."""
    rows = conn.execute(
        "SELECT a.account_id FROM accounts a JOIN items i ON i.item_id = a.item_id "
        "WHERE a.type = 'investment' AND a.included = 1 AND a.duplicate_of_account IS NULL "
        "AND i.investments_history_complete = 1 ORDER BY a.account_id"
    ).fetchall()
    return [r["account_id"] for r in rows]


def _required_ranges(conn: Connection) -> dict[str, tuple[date, date]]:
    required: dict[str, tuple[date, date]] = {}
    for account_id in eligible_accounts(conn):
        row = conn.execute(
            "SELECT MIN(date) AS d FROM holdings_snapshots WHERE account_id = ?", (account_id,)
        ).fetchone()
        if row["d"] is None:
            continue
        anchor = date.fromisoformat(row["d"])
        window_start = anchor - timedelta(days=WINDOW_DAYS)
        rng = (window_start - timedelta(days=CARRY_DAYS), anchor)
        secs = conn.execute(
            "SELECT s.* FROM securities s WHERE s.security_id IN ("
            " SELECT security_id FROM holdings_snapshots WHERE account_id = ? AND date = ?"
            " UNION SELECT security_id FROM investment_transactions"
            "  WHERE account_id = ? AND date >= ? AND security_id IS NOT NULL)",
            (account_id, row["d"], account_id, window_start.isoformat()),
        ).fetchall()
        for sec in secs:
            symbol = yahoo_symbol(sec)
            if symbol is None:
                continue
            prev = required.get(symbol)
            required[symbol] = rng if prev is None else (min(prev[0], rng[0]), max(prev[1], rng[1]))
    return required


def symbols_needing_prices(
    conn: Connection, now: datetime | None = None
) -> dict[str, tuple[date, date]]:
    """Symbol -> uncached date range still to fetch, for symbols used by some estimate window."""
    now = now or datetime.now(UTC)
    needed: dict[str, tuple[date, date]] = {}
    for symbol, (start, end) in _required_ranges(conn).items():
        state = conn.execute(
            "SELECT * FROM price_fetch_state WHERE yahoo_symbol = ?", (symbol,)
        ).fetchone()
        if state is not None:
            if state["status"] in ("no_data", "error") and state["last_attempt_at"]:
                if now - _parse_ts(state["last_attempt_at"]) < RETRY_AFTER_FAILURE:
                    continue
            if state["first_date"] and state["last_date"]:
                cached_start = date.fromisoformat(state["first_date"])
                cached_end = date.fromisoformat(state["last_date"])
                if start >= cached_start and end <= cached_end:
                    continue
                if end > cached_end:
                    start = start if start < cached_start else cached_end + timedelta(days=1)
                else:
                    end = cached_start - timedelta(days=1)
        needed[symbol] = (start, end)
    return needed


@dataclass
class PriceOutcome:
    fetched: int = 0
    failed: int = 0
    stopped_early: bool = False


def _with_retries[T](call: Callable[[], T], sleep: Callable[[float], None]) -> T:
    attempt = 0
    while True:
        try:
            return call()
        except PriceSourceError:
            if attempt >= len(RETRY_DELAYS):
                raise
            sleep(RETRY_DELAYS[attempt])
            attempt += 1


def _raw_closes(
    closes: list[tuple[date, float]], splits: list[tuple[date, float]], fetch_date: date
) -> list[tuple[date, float]]:
    out = []
    for d, adjusted in closes:
        factor = 1.0
        for split_date, ratio in splits:
            if d < split_date <= fetch_date:
                factor *= ratio
        out.append((d, adjusted * factor))
    return out


def _record_failure(
    conn: Connection, symbols: Iterable[str], status: str, attempted_at: str
) -> None:
    with write_unit(conn):
        for symbol in symbols:
            conn.execute(
                "INSERT INTO price_fetch_state(yahoo_symbol, last_attempt_at, status,"
                " consecutive_failures) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(yahoo_symbol) DO UPDATE SET"
                " last_attempt_at = excluded.last_attempt_at,"
                " status = excluded.status,"
                " consecutive_failures = consecutive_failures + 1",
                (symbol, attempted_at, status),
            )


def refresh_prices(
    ctx: AppContext,
    *,
    sleep: Callable[[float], None] = time.sleep,
    budget_seconds: float = 120,
) -> PriceOutcome:
    outcome = PriceOutcome()
    source = ctx.price_source
    if source is None:
        return outcome
    started = ctx.now()
    fetch_date = ctx.today()
    conn = ctx.connect()
    try:
        needed = symbols_needing_prices(conn, started)
        groups: dict[tuple[date, date], list[str]] = {}
        for symbol, rng in sorted(needed.items()):
            groups.setdefault(rng, []).append(symbol)
        for (start, end), symbols in groups.items():
            if (ctx.now() - started).total_seconds() > budget_seconds:
                outcome.stopped_early = True
                break
            _fetch_group(ctx, conn, source, symbols, start, end, fetch_date, sleep, outcome)
        log.info(
            "price refresh: %d symbols fetched, %d failed, stopped_early=%s",
            outcome.fetched,
            outcome.failed,
            outcome.stopped_early,
        )
    finally:
        conn.close()
    return outcome


def _fetch_group(
    ctx: AppContext,
    conn: Connection,
    source: Any,
    symbols: list[str],
    start: date,
    end: date,
    fetch_date: date,
    sleep: Callable[[float], None],
    outcome: PriceOutcome,
) -> None:
    # Network calls stay outside write units.
    try:
        closes = _with_retries(
            lambda: source.download_closes(symbols, start, end + timedelta(days=1)), sleep
        )
        splits: dict[str, list[tuple[date, float]]] = {}
        for symbol in symbols:
            if closes.get(symbol):
                splits[symbol] = _with_retries(partial(source.splits, symbol), sleep)
    except PriceSourceError:
        _record_failure(conn, symbols, "error", _iso(ctx.now()))
        outcome.failed += len(symbols)
        return

    attempted_at = _iso(ctx.now())
    with write_unit(conn):
        for symbol in symbols:
            rows = closes.get(symbol) or []
            state = conn.execute(
                "SELECT first_date, last_date FROM price_fetch_state WHERE yahoo_symbol = ?",
                (symbol,),
            ).fetchone()
            has_cache = state is not None and state["first_date"] is not None
            if not rows and not has_cache:
                _record_failure(conn, [symbol], "no_data", attempted_at)
                outcome.failed += 1
                continue
            for split_date, ratio in splits.get(symbol, []):
                conn.execute(
                    "INSERT OR REPLACE INTO price_splits(yahoo_symbol, date, ratio)"
                    " VALUES (?, ?, ?)",
                    (symbol, split_date.isoformat(), ratio),
                )
            for d, raw in _raw_closes(rows, splits.get(symbol, []), fetch_date):
                conn.execute(
                    "INSERT OR REPLACE INTO prices(yahoo_symbol, date, raw_close) VALUES (?, ?, ?)",
                    (symbol, d.isoformat(), raw),
                )
            # Coverage is the requested range, so weekends never re-trigger a fetch.
            first = start if not has_cache else min(start, date.fromisoformat(state["first_date"]))
            last = end if not has_cache else max(end, date.fromisoformat(state["last_date"]))
            conn.execute(
                "INSERT INTO price_fetch_state(yahoo_symbol, last_attempt_at, last_success_at,"
                " status, consecutive_failures, first_date, last_date)"
                " VALUES (?, ?, ?, 'ok', 0, ?, ?) "
                "ON CONFLICT(yahoo_symbol) DO UPDATE SET"
                " last_attempt_at = excluded.last_attempt_at,"
                " last_success_at = excluded.last_success_at, status = 'ok',"
                " consecutive_failures = 0, first_date = excluded.first_date,"
                " last_date = excluded.last_date",
                (symbol, attempted_at, attempted_at, first.isoformat(), last.isoformat()),
            )
            outcome.fetched += 1
