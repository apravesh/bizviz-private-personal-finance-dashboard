import re
from datetime import date
from typing import Any

from bizviz.db.connection import Connection, now_iso, write_unit


def normalize_name(s: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d", "", (s or "").lower())).strip()


def _money(value: Any) -> float | None:
    return None if value is None else round(float(value), 2)


def _find_match(
    conn: Connection, item_id: str, institution_id: str, acct: dict[str, Any]
) -> tuple[str, str, bool] | None:
    """Return (account_id, item_id, item_removed) of the best same-institution match."""
    mask = acct.get("mask")
    subtype = acct.get("subtype")
    if mask is None or subtype is None:
        return None
    name = normalize_name(acct.get("name"))
    rows = conn.execute(
        "SELECT a.account_id, a.name, a.item_id, i.removed_at, a.duplicate_of_account "
        "FROM accounts a JOIN items i ON i.item_id = a.item_id "
        "WHERE a.item_id != ? AND i.institution_id = ? AND a.mask = ? AND a.subtype = ? "
        "ORDER BY a.account_id",
        (item_id, institution_id, mask, subtype),
    ).fetchall()
    matches = [r for r in rows if normalize_name(r["name"]) == name]
    if not matches:
        return None
    matches.sort(key=lambda r: (r["removed_at"] is not None, r["duplicate_of_account"] is not None))
    best = matches[0]
    return best["account_id"], best["item_id"], best["removed_at"] is not None


def upsert_accounts(
    conn: Connection,
    item_id: str,
    plaid_accounts: list[dict[str, Any]],
    *,
    mark_gone: bool = False,
    today: date | None = None,
) -> list[str]:
    """Insert/update accounts of one item; must run inside a write unit. Returns new account ids."""
    if mark_gone and today is None:
        raise ValueError("today is required when mark_gone is set")
    now = now_iso()
    new_ids: list[str] = []
    with write_unit(conn):
        item = conn.execute(
            "SELECT institution_id, removed_at FROM items WHERE item_id = ?", (item_id,)
        ).fetchone()
        if item is None:
            raise LookupError("unknown item")
        for acct in plaid_accounts:
            account_id = acct["account_id"]
            balances = acct.get("balances") or {}
            values = {
                "name": acct.get("name"),
                "official_name": acct.get("official_name"),
                "mask": acct.get("mask"),
                "type": acct.get("type"),
                "subtype": acct.get("subtype"),
                "current_balance": _money(balances.get("current")),
                "available_balance": _money(balances.get("available")),
                "currency": balances.get("iso_currency_code")
                or balances.get("unofficial_currency_code"),
                "balance_as_of": now,
            }
            exists = conn.execute(
                "SELECT 1 FROM accounts WHERE account_id = ?", (account_id,)
            ).fetchone()
            if exists:
                conn.execute(
                    "UPDATE accounts SET name = :name, official_name = :official_name, "
                    "mask = :mask, type = :type, subtype = :subtype, "
                    "current_balance = :current_balance, available_balance = :available_balance, "
                    "currency = :currency, balance_as_of = :balance_as_of, gone_at = NULL "
                    "WHERE account_id = :account_id",
                    {**values, "account_id": account_id},
                )
                continue
            conn.execute(
                "INSERT INTO accounts(account_id, item_id, name, official_name, mask, type, "
                "subtype, ownership, included, current_balance, available_balance, currency, "
                "balance_as_of, reviewed) VALUES (:account_id, :item_id, :name, :official_name, "
                ":mask, :type, :subtype, 'mine', 1, :current_balance, :available_balance, "
                ":currency, :balance_as_of, 0)",
                {**values, "account_id": account_id, "item_id": item_id},
            )
            new_ids.append(account_id)
            if item["institution_id"] is None:
                continue
            match = _find_match(conn, item_id, item["institution_id"], acct)
            if match is None:
                continue
            matched_id, matched_item, matched_removed = match
            if matched_removed:
                conn.execute(
                    "UPDATE accounts SET superseded_by = ? WHERE account_id = ?",
                    (account_id, matched_id),
                )
            else:
                conn.execute(
                    "UPDATE accounts SET included = 0, duplicate_of_account = ? "
                    "WHERE account_id = ?",
                    (matched_id, account_id),
                )
            conn.execute(
                "UPDATE accounts SET ownership = "
                "(SELECT ownership FROM accounts WHERE account_id = ?) WHERE account_id = ?",
                (matched_id, account_id),
            )
            conn.execute(
                "UPDATE items SET duplicate_of = ? WHERE item_id = ?", (matched_item, item_id)
            )
        if mark_gone and item["removed_at"] is None:
            assert today is not None
            seen = [a["account_id"] for a in plaid_accounts]
            marks = ", ".join("?" for _ in seen)
            conn.execute(
                "UPDATE accounts SET gone_at = ? WHERE item_id = ? AND gone_at IS NULL"
                + (f" AND account_id NOT IN ({marks})" if seen else ""),
                (today.isoformat(), item_id, *seen),
            )
    return new_ids


def snapshot_balances(
    conn: Connection,
    plaid_accounts: list[dict[str, Any]],
    *,
    local_date: date,
    investment_dates: dict[str, date],
) -> None:
    now = now_iso()
    with write_unit(conn):
        for acct in plaid_accounts:
            account_id = acct["account_id"]
            snap_date = (
                investment_dates.get(account_id, local_date)
                if acct.get("type") == "investment"
                else local_date
            )
            balances = acct.get("balances") or {}
            conn.execute(
                "INSERT OR REPLACE INTO balance_snapshots(account_id, date, current, available, "
                "captured_at) VALUES (?, ?, ?, ?, ?)",
                (
                    account_id,
                    snap_date.isoformat(),
                    _money(balances.get("current")),
                    _money(balances.get("available")),
                    now,
                ),
            )
