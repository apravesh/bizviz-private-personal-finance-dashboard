from datetime import date, timedelta
from typing import Any

from bizviz.db.connection import Connection, write_unit
from bizviz.price_types import PriceSourceError
from tests.factories import (
    make_account,
    make_balance_snapshot,
    make_holding_snapshot,
    make_item,
    make_security,
)

A = date(2026, 10, 15)


def d(offset: int, anchor: date = A) -> str:
    return (anchor + timedelta(days=offset)).isoformat()


class FakePriceSource:
    """Dict-backed PriceSource that records calls and can raise PriceSourceError."""

    def __init__(self) -> None:
        self.closes: dict[str, list[tuple[date, float]]] = {}
        self.split_map: dict[str, list[tuple[date, float]]] = {}
        self.download_calls: list[tuple[list[str], date, date]] = []
        self.split_calls: list[str] = []
        self.download_failures = 0  # raise on the next N download calls
        self.split_failures = 0

    def download_closes(
        self, symbols: list[str], start: date, end_exclusive: date
    ) -> dict[str, list[tuple[date, float]]]:
        self.download_calls.append((list(symbols), start, end_exclusive))
        if self.download_failures > 0:
            self.download_failures -= 1
            raise PriceSourceError("boom")
        return {
            s: [(day, v) for day, v in self.closes.get(s, []) if start <= day < end_exclusive]
            for s in symbols
        }

    def splits(self, symbol: str) -> list[tuple[date, float]]:
        self.split_calls.append(symbol)
        if self.split_failures > 0:
            self.split_failures -= 1
            raise PriceSourceError("boom")
        return list(self.split_map.get(symbol, []))


def setup_account(
    conn: Connection,
    account_id: str = "inv-1",
    *,
    anchor: date = A,
    complete: bool = True,
    item_id: str | None = None,
    captured_at: str | None = None,
    balance: float | None = None,
) -> str:
    """Investment account (and its item) with no holdings yet."""
    item_id = item_id or f"item-{account_id}"
    make_item(conn, item_id, link_mode="investment", products=["investments"])
    make_account(conn, account_id, item_id, type="investment", subtype="brokerage")
    set_complete(conn, item_id, complete)
    if balance is not None:
        make_balance_snapshot(conn, account_id, anchor.isoformat(), balance)
    return account_id


def set_complete(conn: Connection, item_id: str, complete: bool) -> None:
    with write_unit(conn):
        conn.execute(
            "UPDATE items SET investments_history_complete = ? WHERE item_id = ?",
            (int(complete), item_id),
        )


def hold(
    conn: Connection,
    account_id: str,
    security_id: str,
    quantity: float,
    price: float | None,
    value: float | None = None,
    *,
    anchor: date = A,
    captured_at: str | None = None,
) -> None:
    make_holding_snapshot(
        conn,
        account_id,
        security_id,
        anchor.isoformat(),
        quantity,
        price,
        quantity * price if value is None and price is not None else value,
        captured_at=captured_at,
    )


def sec(conn: Connection, security_id: str, ticker: str | None, **kw: Any) -> str:
    return make_security(conn, security_id, ticker, **kw)


def put_prices(
    conn: Connection,
    symbol: str,
    start: date,
    end: date,
    price: Any = 100.0,
    *,
    weekdays_only: bool = False,
) -> None:
    """Raw closes for [start, end]; `price` is a constant or a function of the date."""
    with write_unit(conn):
        day = start
        while day <= end:
            if not (weekdays_only and day.weekday() >= 5):
                value = price(day) if callable(price) else price
                conn.execute(
                    "INSERT OR REPLACE INTO prices(yahoo_symbol, date, raw_close) VALUES (?, ?, ?)",
                    (symbol, day.isoformat(), value),
                )
            day += timedelta(days=1)


def put_split(conn: Connection, symbol: str, day: date, ratio: float) -> None:
    with write_unit(conn):
        conn.execute(
            "INSERT OR REPLACE INTO price_splits(yahoo_symbol, date, ratio) VALUES (?, ?, ?)",
            (symbol, day.isoformat(), ratio),
        )


def set_fetch_state(conn: Connection, symbol: str, **cols: Any) -> None:
    row = {"yahoo_symbol": symbol, **cols}
    with write_unit(conn):
        conn.execute(
            f"INSERT OR REPLACE INTO price_fetch_state({', '.join(row)})"
            f" VALUES ({', '.join('?' for _ in row)})",
            tuple(row.values()),
        )
