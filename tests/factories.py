"""Row factories for tests. Each inserts inside its own write_unit and returns the new id."""

import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from bizviz.context import AppContext
from bizviz.db.connection import Connection, write_unit

_CREATED_AT = "2026-01-01T00:00:00Z"


def _insert(conn: Connection, table: str, row: dict[str, Any]) -> None:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with write_unit(conn):
        conn.execute(f"INSERT INTO {table}({cols}) VALUES ({marks})", tuple(row.values()))


def set_now(ctx: AppContext, dt: datetime) -> None:
    """Move the context's fixed clock (from the app_ctx fixture) to `dt` (tz-aware)."""
    ctx.clock_holder.now = dt  # type: ignore[attr-defined]


def make_item(
    conn: Connection,
    item_id: str = "item-1",
    institution_id: str | None = "ins_1",
    institution_name: str | None = "Bank One",
    link_mode: str = "bank",
    products: Iterable[str] = ("transactions",),
    **kw: Any,
) -> str:
    row: dict[str, Any] = {
        "item_id": item_id,
        "institution_id": institution_id,
        "institution_name": institution_name,
        "access_token": f"access-{item_id}",
        "link_mode": link_mode,
        "products": json.dumps(list(products)),
        "created_at": _CREATED_AT,
    }
    row.update(kw)
    _insert(conn, "items", row)
    return item_id


def make_account(
    conn: Connection,
    account_id: str = "acc-1",
    item_id: str = "item-1",
    type: str = "depository",
    subtype: str | None = "checking",
    name: str | None = "Checking",
    mask: str | None = "0001",
    ownership: str = "mine",
    **kw: Any,
) -> str:
    row: dict[str, Any] = {
        "account_id": account_id,
        "item_id": item_id,
        "name": name,
        "mask": mask,
        "type": type,
        "subtype": subtype,
        "ownership": ownership,
    }
    row.update(kw)
    _insert(conn, "accounts", row)
    return account_id


def make_txn(
    conn: Connection,
    transaction_id: str,
    account_id: str,
    date: str,
    amount: float,
    *,
    name: str = "",
    merchant_name: str | None = None,
    pfc_primary: str | None = None,
    pfc_detailed: str | None = None,
    pending: bool = False,
    pending_transaction_id: str | None = None,
) -> str:
    _insert(
        conn,
        "transactions",
        {
            "transaction_id": transaction_id,
            "account_id": account_id,
            "date": date,
            "amount": amount,
            "name": name,
            "merchant_name": merchant_name,
            "pfc_primary": pfc_primary,
            "pfc_detailed": pfc_detailed,
            "pending": int(pending),
            "pending_transaction_id": pending_transaction_id,
            "updated_at": _CREATED_AT,
        },
    )
    return transaction_id


def make_override(
    conn: Connection,
    transaction_id: str,
    *,
    category_id: str | None = None,
    flow_class: str | None = None,
    recurring: bool | None = None,
) -> str:
    _insert(
        conn,
        "tx_overrides",
        {
            "transaction_id": transaction_id,
            "category_id": category_id,
            "flow_class": flow_class,
            "recurring": None if recurring is None else int(recurring),
            "updated_at": _CREATED_AT,
        },
    )
    return transaction_id


def make_rule(
    conn: Connection,
    merchant_key: str,
    *,
    category_id: str | None = None,
    flow_class: str | None = None,
    recurring: bool | None = None,
) -> int:
    with write_unit(conn):
        cur = conn.execute(
            "INSERT INTO merchant_rules(merchant_key, category_id, flow_class, recurring,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                merchant_key,
                category_id,
                flow_class,
                None if recurring is None else int(recurring),
                _CREATED_AT,
                _CREATED_AT,
            ),
        )
        rule_id = cur.lastrowid
    assert rule_id is not None
    return int(rule_id)


def make_balance_snapshot(
    conn: Connection,
    account_id: str,
    date: str,
    current: float | None,
    available: float | None = None,
) -> str:
    _insert(
        conn,
        "balance_snapshots",
        {
            "account_id": account_id,
            "date": date,
            "current": current,
            "available": available,
            "captured_at": f"{date}T12:00:00Z",
        },
    )
    return account_id


def make_security(
    conn: Connection,
    security_id: str,
    ticker_symbol: str | None,
    type: str = "equity",
    is_cash_equivalent: bool = False,
    name: str | None = None,
) -> str:
    _insert(
        conn,
        "securities",
        {
            "security_id": security_id,
            "ticker_symbol": ticker_symbol,
            "name": name,
            "type": type,
            "is_cash_equivalent": int(is_cash_equivalent),
            "updated_at": _CREATED_AT,
        },
    )
    return security_id


def make_holding_snapshot(
    conn: Connection,
    account_id: str,
    security_id: str,
    date: str,
    quantity: float,
    institution_price: float | None,
    institution_value: float | None = None,
    captured_at: str | None = None,
) -> str:
    _insert(
        conn,
        "holdings_snapshots",
        {
            "account_id": account_id,
            "security_id": security_id,
            "date": date,
            "quantity": quantity,
            "institution_price": institution_price,
            "institution_value": institution_value,
            "captured_at": captured_at or f"{date}T16:00:00Z",
        },
    )
    return security_id


def make_inv_txn(
    conn: Connection,
    investment_transaction_id: str,
    account_id: str,
    security_id: str | None,
    date: str,
    type: str,
    subtype: str | None,
    quantity: float | None,
    amount: float | None,
) -> str:
    _insert(
        conn,
        "investment_transactions",
        {
            "investment_transaction_id": investment_transaction_id,
            "account_id": account_id,
            "security_id": security_id,
            "date": date,
            "type": type,
            "subtype": subtype,
            "quantity": quantity,
            "amount": amount,
        },
    )
    return investment_transaction_id
