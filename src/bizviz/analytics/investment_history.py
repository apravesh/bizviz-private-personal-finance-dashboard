"""Reconstruct up to 24 months of estimated daily investment-account values.

Walks backward from the anchor holdings snapshot, undoing transactions and splits, and
values positions at unadjusted (raw) closes.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from bizviz.db.connection import Connection, bump_data_version, write_unit
from bizviz.prices import (
    CARRY_DAYS,
    WINDOW_DAYS,
    eligible_accounts,
    is_cash_like,
    is_unpriceable_symbol,
    yahoo_symbol,
)

WARN_INCONSISTENT_ACTIVITY = (
    "Some reported activity is inconsistent; the estimate may be incomplete."
)
WARN_NO_ACTIVITY = (
    "No investment activity was reported; the estimate assumes holdings were unchanged."
)
SANITY_TOLERANCE = 0.25
SPLIT_MATCH_DAYS = 3
SPLIT_MATCH_TOLERANCE = 0.01
QTY_EPSILON = 1e-6
NEGATIVE_CASH_TOLERANCE = 0.005


@dataclass
class _Event:
    date: date
    kind: str  # 'txn' | 'split'
    txn: Any = None
    symbol: str = ""
    ratio: float = 1.0


_UNKNOWN_SECURITY = {"ticker_symbol": None, "type": None, "is_cash_equivalent": 0}


def rebuild_estimates(
    conn: Connection, today: date, local_tz: tzinfo, account_ids: list[str] | None = None
) -> None:
    wanted = None if account_ids is None else set(account_ids)
    targets = [a for a in eligible_accounts(conn) if wanted is None or a in wanted]
    # eligible_accounts skips items still downloading; those still need a 'downloading' meta row.
    for account_id in _downloading_accounts(conn, wanted):
        with write_unit(conn):
            _clear(conn, account_id)
            conn.execute(
                "INSERT OR REPLACE INTO investment_estimate_meta(account_id, anchor_date,"
                " window_start, window_end, excluded_count, excluded_value, warnings, status)"
                " VALUES (?, NULL, NULL, NULL, 0, 0, '[]', 'downloading')",
                (account_id,),
            )
    for account_id in targets:
        with write_unit(conn):
            _rebuild_account(conn, account_id, local_tz)
    bump_data_version(conn)


def _downloading_accounts(conn: Connection, wanted: set[str] | None) -> list[str]:
    rows = conn.execute(
        "SELECT a.account_id FROM accounts a JOIN items i ON i.item_id = a.item_id "
        "WHERE a.type = 'investment' AND a.included = 1 AND a.duplicate_of_account IS NULL "
        "AND i.investments_history_complete = 0 ORDER BY a.account_id"
    ).fetchall()
    ids = [r["account_id"] for r in rows]
    return [a for a in ids if wanted is None or a in wanted]


def _clear(conn: Connection, account_id: str) -> None:
    conn.execute("DELETE FROM investment_estimates WHERE account_id = ?", (account_id,))


def _local_date(captured_at: str, local_tz: tzinfo) -> date:
    parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(local_tz).date()


def _rebuild_account(conn: Connection, account_id: str, local_tz: tzinfo) -> None:
    anchor_row = conn.execute(
        "SELECT date, MIN(captured_at) AS captured_at FROM holdings_snapshots"
        " WHERE account_id = ? AND date = (SELECT MIN(date) FROM holdings_snapshots"
        " WHERE account_id = ?)",
        (account_id, account_id),
    ).fetchone()
    if anchor_row["date"] is None:
        _clear(conn, account_id)
        conn.execute("DELETE FROM investment_estimate_meta WHERE account_id = ?", (account_id,))
        return
    anchor = date.fromisoformat(anchor_row["date"])
    last_day = anchor - timedelta(days=1)
    window_start = anchor - timedelta(days=WINDOW_DAYS)
    fetch_day = max(_local_date(anchor_row["captured_at"], local_tz), anchor)

    holdings = conn.execute(
        "SELECT * FROM holdings_snapshots WHERE account_id = ? AND date = ?",
        (account_id, anchor.isoformat()),
    ).fetchall()
    txn_rows = conn.execute(
        "SELECT * FROM investment_transactions WHERE account_id = ? AND date >= ? AND date <= ?"
        " ORDER BY date DESC, investment_transaction_id",
        (account_id, window_start.isoformat(), fetch_day.isoformat()),
    ).fetchall()
    sec_ids = {h["security_id"] for h in holdings} | {
        t["security_id"] for t in txn_rows if t["security_id"] is not None
    }
    securities: dict[str, Any] = {}
    for sid in sec_ids:
        row = conn.execute("SELECT * FROM securities WHERE security_id = ?", (sid,)).fetchone()
        securities[sid] = row if row is not None else _UNKNOWN_SECURITY

    def cash_like(sid: str | None) -> bool:
        return sid is not None and is_cash_like(securities[sid])

    noncash_ids = [sid for sid in sorted(sec_ids) if not cash_like(sid)]
    symbol_of = {sid: yahoo_symbol(securities[sid]) for sid in noncash_ids}
    symbols = sorted({s for s in symbol_of.values() if s is not None})

    anchor_qty: dict[str, float] = {}
    anchor_price: dict[str, float | None] = {}
    anchor_value: dict[str, float] = {}
    cash_holdings_value = 0.0
    for h in holdings:
        value = _holding_value(h)
        if cash_like(h["security_id"]):
            cash_holdings_value += value
        else:
            sid = h["security_id"]
            anchor_qty[sid] = anchor_qty.get(sid, 0.0) + h["quantity"]
            anchor_price[sid] = h["institution_price"]
            anchor_value[sid] = anchor_value.get(sid, 0.0) + value
    balance = conn.execute(
        "SELECT current FROM balance_snapshots WHERE account_id = ? AND date = ?",
        (account_id, anchor.isoformat()),
    ).fetchone()
    if balance is not None and balance["current"] is not None:
        anchor_cash = balance["current"] - sum(anchor_value.values())
    else:
        anchor_cash = cash_holdings_value

    splits_by_symbol: dict[str, list[tuple[date, float]]] = {s: [] for s in symbols}
    for symbol in symbols:
        for r in conn.execute(
            "SELECT date, ratio FROM price_splits"
            " WHERE yahoo_symbol = ? AND date > ? AND date <= ?",
            (symbol, window_start.isoformat(), fetch_day.isoformat()),
        ):
            splits_by_symbol[symbol].append((date.fromisoformat(r["date"]), r["ratio"]))

    events = _build_events(txn_rows, splits_by_symbol)
    days = [last_day - timedelta(days=i) for i in range((last_day - window_start).days + 1)]

    # PASS 1: quantities only.
    quantities, ignored, inconsistent = _walk_quantities(
        days, events, noncash_ids, anchor_qty, symbol_of, splits_by_symbol, cash_like
    )

    prices = _load_prices(conn, symbols, window_start, anchor)

    def close_on(symbol: str, day: date) -> float | None:
        series = prices.get(symbol, {})
        for back in range(CARRY_DAYS + 1):
            value = series.get(day - timedelta(days=back))
            if value is not None:
                return value
        return None

    # Priceability.
    priceable: set[str] = set()
    excluded: list[str] = []
    sanity_failed = 0
    for sid in noncash_ids:
        held_in_window = any(abs(quantities[d].get(sid, 0.0)) > 1e-9 for d in days)
        held_at_anchor = abs(anchor_qty.get(sid, 0.0)) > 1e-9
        if not held_in_window and not held_at_anchor:
            continue
        sym = symbol_of[sid]
        ok = sym is not None and not is_unpriceable_symbol(conn, sym)
        if ok and sym is not None:
            ok = all(close_on(sym, d) is not None for d in days if quantities[d].get(sid, 0.0) > 0)
        if ok and sym is not None and held_at_anchor:
            price = anchor_price.get(sid)
            if price:
                # Plaid's price is as of T; splits since A are not in the close at A yet.
                for split_date, ratio in splits_by_symbol[sym]:
                    if anchor < split_date <= fetch_day:
                        price *= ratio
            close = close_on(sym, anchor)
            if price and close is not None and abs(close - price) / abs(price) > SANITY_TOLERANCE:
                ok = False
                sanity_failed += 1
        if ok:
            priceable.add(sid)
        else:
            excluded.append(sid)

    # PASS 2: cash and values.
    cash_by_day = _walk_cash(days, events, anchor_cash, ignored, priceable, cash_like)

    values: list[tuple[date, float]] = []
    for d in sorted(days):
        total = cash_by_day[d]
        for sid in priceable:
            qty = quantities[d].get(sid, 0.0)
            if qty > 0:
                sym = symbol_of[sid]
                assert sym is not None
                close = close_on(sym, d)
                if close is not None:
                    total += qty * close
        values.append((d, total))

    kept = list(values)
    while kept and kept[0][1] <= 0:
        kept.pop(0)

    warnings: list[str] = []
    first_txn = conn.execute(
        "SELECT MIN(date) AS d FROM investment_transactions WHERE account_id = ?", (account_id,)
    ).fetchone()["d"]
    if first_txn is None:
        warnings.append(WARN_NO_ACTIVITY)
    elif date.fromisoformat(first_txn) > window_start + timedelta(days=31):
        warnings.append(
            f"Investment activity was only reported from {first_txn};"
            " earlier estimates assume no activity before then."
        )
    if sanity_failed:
        warnings.append(
            f"Price data for {sanity_failed} holdings looked inconsistent and was excluded."
        )
    if inconsistent or any(cash_by_day[d] < -NEGATIVE_CASH_TOLERANCE for d, _ in kept):
        warnings.append(WARN_INCONSISTENT_ACTIVITY)

    excluded_value = sum(anchor_value.get(sid, 0.0) for sid in excluded)
    _clear(conn, account_id)
    conn.executemany(
        "INSERT INTO investment_estimates(account_id, date, value) VALUES (?, ?, ?)",
        [(account_id, d.isoformat(), round(v, 2)) for d, v in kept],
    )
    conn.execute(
        "INSERT OR REPLACE INTO investment_estimate_meta(account_id, anchor_date, window_start,"
        " window_end, excluded_count, excluded_value, warnings, status)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'ok')",
        (
            account_id,
            anchor.isoformat(),
            window_start.isoformat(),
            last_day.isoformat(),
            len(excluded),
            excluded_value,
            json.dumps(list(dict.fromkeys(warnings))),
        ),
    )


def _holding_value(h: Any) -> float:
    if h["institution_value"] is not None:
        return float(h["institution_value"])
    if h["institution_price"] is not None:
        return float(h["quantity"] * h["institution_price"])
    return 0.0


def _build_events(
    txn_rows: list[Any], splits_by_symbol: dict[str, list[tuple[date, float]]]
) -> list[_Event]:
    events = [_Event(date.fromisoformat(t["date"]), "txn", txn=t) for t in txn_rows]
    splits = [
        _Event(d, "split", symbol=symbol, ratio=ratio)
        for symbol, items in splits_by_symbol.items()
        for d, ratio in items
    ]
    # Newest first; within a date, transactions are undone before that date's split.
    # Stable sort keeps transactions in (date desc, id) order.
    return sorted(events + splits, key=lambda e: (e.date, e.kind == "txn"), reverse=True)


def _walk[R](
    days: list[date], events: list[_Event], apply: Callable[[_Event], None], snap: Callable[[], R]
) -> dict[date, R]:
    out: dict[date, R] = {}
    i = 0
    for d in days:
        while i < len(events) and events[i].date > d:
            apply(events[i])
            i += 1
        out[d] = snap()
    return out


def _walk_quantities(
    days: list[date],
    events: list[_Event],
    noncash_ids: list[str],
    anchor_qty: dict[str, float],
    symbol_of: dict[str, str | None],
    splits_by_symbol: dict[str, list[tuple[date, float]]],
    cash_like: Callable[[str | None], bool],
) -> tuple[dict[date, dict[str, float]], set[str], bool]:
    qty = {sid: anchor_qty.get(sid, 0.0) for sid in noncash_ids}
    ignored: set[str] = set()
    undone_splits: set[tuple[str, date]] = set()
    inconsistent = False

    def clamp(sid: str) -> None:
        nonlocal inconsistent
        if qty[sid] < -QTY_EPSILON:
            qty[sid] = 0.0
            inconsistent = True

    def apply(ev: _Event) -> None:
        if ev.kind == "split":
            undone_splits.add((ev.symbol, ev.date))
            for sid in noncash_ids:
                if symbol_of[sid] == ev.symbol:
                    qty[sid] /= ev.ratio
            return
        t = ev.txn
        sid = t["security_id"]
        if sid is None or cash_like(sid):
            return
        if _is_split_row(t, symbol_of[sid], qty[sid], splits_by_symbol, undone_splits):
            ignored.add(t["investment_transaction_id"])
            return
        if t["quantity"] is not None:
            qty[sid] -= t["quantity"]
            clamp(sid)

    quantities = _walk(days, events, apply, lambda: dict(qty))
    return quantities, ignored, inconsistent


def _is_split_row(
    t: Any,
    symbol: str | None,
    running_qty: float,
    splits_by_symbol: dict[str, list[tuple[date, float]]],
    undone_splits: set[tuple[str, date]],
) -> bool:
    subtype = (t["subtype"] or "").lower()
    if subtype in ("split", "stock distribution"):
        return True
    kind = (t["type"] or "").lower()
    if not (kind in ("transfer", "adjustment") or subtype == "adjustment"):
        return False
    if symbol is None or t["quantity"] is None:
        return False
    txn_date = date.fromisoformat(t["date"])
    for split_date, ratio in splits_by_symbol.get(symbol, []):
        if abs((split_date - txn_date).days) > SPLIT_MATCH_DAYS:
            continue
        post_split = running_qty * ratio if (symbol, split_date) in undone_splits else running_qty
        expected = post_split * (ratio - 1) / ratio
        if abs(t["quantity"] - expected) <= SPLIT_MATCH_TOLERANCE * abs(expected):
            return True
    return False


def _walk_cash(
    days: list[date],
    events: list[_Event],
    anchor_cash: float,
    ignored: set[str],
    priceable: set[str],
    cash_like: Callable[[str | None], bool],
) -> dict[date, float]:
    cash = anchor_cash

    def apply(ev: _Event) -> None:
        nonlocal cash
        if ev.kind != "txn":
            return
        t = ev.txn
        if t["investment_transaction_id"] in ignored:
            return
        sid = t["security_id"]
        kind = (t["type"] or "").lower()
        if sid is not None:
            if cash_like(sid):
                if kind in ("buy", "sell"):
                    return
            else:
                if kind in ("buy", "sell") and sid not in priceable:
                    return
                if kind == "transfer":
                    return
        cash += t["amount"] or 0.0

    return _walk(days, events, apply, lambda: cash)


def _load_prices(
    conn: Connection, symbols: list[str], window_start: date, anchor: date
) -> dict[str, dict[date, float]]:
    out: dict[str, dict[date, float]] = {}
    low = (window_start - timedelta(days=CARRY_DAYS)).isoformat()
    for symbol in symbols:
        out[symbol] = {
            date.fromisoformat(r["date"]): r["raw_close"]
            for r in conn.execute(
                "SELECT date, raw_close FROM prices WHERE yahoo_symbol = ? AND date >= ?"
                " AND date <= ?",
                (symbol, low, anchor.isoformat()),
            )
        }
    return out
