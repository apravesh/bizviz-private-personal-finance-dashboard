import copy
import json
from pathlib import Path
from typing import Any

from bizviz.db.connection import Connection

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def item_get(
    products: list[str] | None = None,
    billed: list[str] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resp = load("item_get")
    resp["item"]["products"] = products if products is not None else ["transactions"]
    if billed is not None:
        resp["item"]["billed_products"] = billed
    resp["item"]["error"] = error
    return resp


def plaid_account(
    account_id: str,
    *,
    name: str = "Checking",
    mask: str | None = "0001",
    type: str = "depository",
    subtype: str | None = "checking",
    current: float | None = 100.0,
    available: float | None = 90.0,
) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "name": name,
        "official_name": None,
        "mask": mask,
        "type": type,
        "subtype": subtype,
        "balances": {"current": current, "available": available, "iso_currency_code": "USD"},
    }


def plaid_txn(
    transaction_id: str,
    account_id: str = "acc-1",
    date: str = "2026-10-10",
    amount: float = 10.0,
    *,
    name: str = "Shop",
    merchant_name: str | None = None,
    pending: bool = False,
    pending_transaction_id: str | None = None,
) -> dict[str, Any]:
    return {
        "transaction_id": transaction_id,
        "account_id": account_id,
        "date": date,
        "authorized_date": None,
        "amount": amount,
        "iso_currency_code": "USD",
        "name": name,
        "merchant_name": merchant_name,
        "pending": pending,
        "pending_transaction_id": pending_transaction_id,
    }


def sync_page(
    added: list[dict[str, Any]] | None = None,
    modified: list[dict[str, Any]] | None = None,
    removed: list[str] | None = None,
    accounts: list[dict[str, Any]] | None = None,
    *,
    cursor: str = "c1",
    has_more: bool = False,
    status: str = "HISTORICAL_UPDATE_COMPLETE",
) -> dict[str, Any]:
    return {
        "added": added or [],
        "modified": modified or [],
        "removed": [{"transaction_id": r} for r in removed or []],
        "accounts": accounts or [],
        "next_cursor": cursor,
        "has_more": has_more,
        "transactions_update_status": status,
    }


def item_row(conn: Connection, item_id: str = "item-1") -> Any:
    return conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()


def data_version(conn: Connection) -> int:
    return int(conn.execute("SELECT value FROM meta WHERE key = 'data_version'").fetchone()[0])


def deep(obj: Any) -> Any:
    return copy.deepcopy(obj)
