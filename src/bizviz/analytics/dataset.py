import json
from datetime import date
from typing import Any

from bizviz.analytics.classify import compute_quality
from bizviz.analytics.networth import compute_net_worth, eligible_accounts
from bizviz.analytics.transfers import INV_CASH_MOVEMENT_SQL
from bizviz.api.schema import (
    AccountOut,
    CategoryOut,
    DataPayload,
    EstimateMetaOut,
    HoldingOut,
    InvestmentOut,
    ItemStatus,
    OverrideOut,
    RecurringGroupOut,
    RecurringOut,
    RuleOut,
    ScopeClass,
    TransactionOut,
)
from bizviz.db.connection import Connection, get_meta, now_iso, read_unit

_ACCOUNT_SQL = (
    "SELECT a.account_id, a.item_id, i.institution_name, a.name, a.mask, a.type, a.subtype,"
    " a.ownership, a.included, a.reviewed, a.current_balance, a.available_balance, a.currency,"
    " a.balance_as_of, a.gone_at, i.removed_at, a.duplicate_of_account, a.superseded_by"
    " FROM accounts a LEFT JOIN items i ON i.item_id = a.item_id"
)


def _account_out(r: Any) -> AccountOut:
    return {
        "account_id": r["account_id"],
        "item_id": r["item_id"],
        "institution_name": r["institution_name"],
        "name": r["name"],
        "mask": r["mask"],
        "type": r["type"],
        "subtype": r["subtype"],
        "ownership": r["ownership"],
        "included": bool(r["included"]),
        "reviewed": bool(r["reviewed"]),
        "current_balance": r["current_balance"],
        "available_balance": r["available_balance"],
        "currency": r["currency"],
        "balance_as_of": r["balance_as_of"],
        "gone_at": r["gone_at"],
        "removed": r["removed_at"] is not None,
        "duplicate_of_account": r["duplicate_of_account"],
        "superseded_by": r["superseded_by"],
    }


def fetch_account(conn: Connection, account_id: str) -> AccountOut | None:
    r = conn.execute(_ACCOUNT_SQL + " WHERE a.account_id = ?", (account_id,)).fetchone()
    return None if r is None else _account_out(r)


def rule_out(r: Any) -> RuleOut:
    return {
        "rule_id": r["rule_id"],
        "merchant_key": r["merchant_key"],
        "category_id": r["category_id"],
        "flow_class": r["flow_class"],
        "recurring": None if r["recurring"] is None else bool(r["recurring"]),
    }


def category_out(r: Any) -> CategoryOut:
    return {
        "category_id": r["category_id"],
        "name": r["name"],
        "kind": r["kind"],
        "applies_to": r["applies_to"],
        "hidden": bool(r["hidden"]),
    }


def _items(conn: Connection) -> list[ItemStatus]:
    return [
        {
            "item_id": r["item_id"],
            "institution_id": r["institution_id"],
            "institution_name": r["institution_name"],
            "status": r["status"],
            "error_code": r["error_code"],
            "consent_expires_at": r["consent_expires_at"],
            "last_success_at": r["last_success_at"],
            "removed": r["removed_at"] is not None,
        }
        for r in conn.execute(
            "SELECT item_id, institution_id, institution_name, status, error_code,"
            " consent_expires_at, last_success_at, removed_at FROM items"
            " ORDER BY created_at, item_id"
        )
    ]


def _newest_data_at(conn: Connection) -> str | None:
    row = conn.execute(
        "SELECT MAX(v) FROM (SELECT MAX(last_success_at) AS v FROM items"
        " UNION ALL SELECT MAX(captured_at) FROM balance_snapshots)"
    ).fetchone()
    return None if row[0] is None else str(row[0])


def _status(conn: Connection) -> dict[str, Any]:
    return {
        "data_version": int(get_meta(conn, "data_version") or 0),
        "newest_data_at": _newest_data_at(conn),
        "items": _items(conn),
    }


def compute_status(conn: Connection) -> dict[str, Any]:
    """{data_version, newest_data_at, items}, read consistently."""
    if conn.in_transaction:
        return _status(conn)
    with read_unit(conn):
        return _status(conn)


def _scope(flow: str | None, category_id: str | None, recurring: int | None) -> ScopeClass | None:
    if flow is None or category_id is None:
        return None
    return {"flow": flow, "category_id": category_id, "recurring": bool(recurring)}


def _transactions(conn: Connection) -> list[TransactionOut]:
    out: list[TransactionOut] = []
    for r in conn.execute(
        "SELECT t.transaction_id, t.account_id, t.date, t.amount, t.name, t.pending,"
        " t.pfc_detailed, c.merchant_key, c.merchant_display, c.mine_flow, c.mine_category_id,"
        " c.mine_recurring,"
        " c.household_flow, c.household_category_id, c.household_recurring,"
        " o.transaction_id AS ov_id, o.category_id AS ov_category, o.flow_class AS ov_flow,"
        " o.recurring AS ov_recurring"
        " FROM tx_classification c JOIN transactions t ON t.transaction_id = c.transaction_id"
        " JOIN accounts a ON a.account_id = t.account_id"
        " LEFT JOIN tx_overrides o ON o.transaction_id = t.transaction_id"
        " WHERE a.type IN ('depository', 'credit') AND a.included = 1"
        " AND a.duplicate_of_account IS NULL ORDER BY t.date DESC, t.transaction_id"
    ):
        override: OverrideOut | None = None
        if r["ov_id"] is not None:
            override = {
                "category_id": r["ov_category"],
                "flow_class": r["ov_flow"],
                "recurring": None if r["ov_recurring"] is None else bool(r["ov_recurring"]),
            }
        out.append(
            {
                "id": r["transaction_id"],
                "account_id": r["account_id"],
                "date": r["date"],
                "amount": r["amount"],
                "name": r["name"],
                "merchant_key": r["merchant_key"],
                "merchant_display": r["merchant_display"],
                "pending": bool(r["pending"]),
                "pfc_detailed": r["pfc_detailed"],
                "mine": _scope(r["mine_flow"], r["mine_category_id"], r["mine_recurring"]),
                "household": _scope(
                    r["household_flow"], r["household_category_id"], r["household_recurring"]
                ),
                "override": override,
            }
        )
    return out


def _recurring(conn: Connection) -> RecurringOut:
    by_scope: dict[str, list[RecurringGroupOut]] = {"mine": [], "household": []}
    for r in conn.execute(
        "SELECT scope, merchant_key, merchant_display, cadence, typical_amount, last_date,"
        " count, active FROM recurring_groups"
        " ORDER BY active DESC, last_date DESC, merchant_key"
    ):
        by_scope[r["scope"]].append(
            {
                "merchant_key": r["merchant_key"],
                "merchant_display": r["merchant_display"],
                "cadence": r["cadence"],
                "typical_amount": r["typical_amount"],
                "last_date": r["last_date"],
                "count": r["count"],
                "active": bool(r["active"]),
            }
        )
    return {"mine": by_scope["mine"], "household": by_scope["household"]}


def _investments(conn: Connection) -> list[InvestmentOut]:
    out: list[InvestmentOut] = []
    for acc in eligible_accounts(conn):
        if acc.type != "investment":
            continue
        aid = acc.account_id
        anchor = conn.execute(
            "SELECT MIN(date) FROM holdings_snapshots WHERE account_id = ?", (aid,)
        ).fetchone()[0]
        actual = [
            (r["date"], r["current"])
            for r in conn.execute(
                "SELECT date, current FROM balance_snapshots WHERE account_id = ?"
                " AND current IS NOT NULL AND date >= ? AND date <= ? ORDER BY date",
                (aid, anchor or "", acc.cutoff or "9999-12-31"),
            )
        ]
        estimated = [
            (r["date"], r["value"])
            for r in conn.execute(
                "SELECT date, value FROM investment_estimates WHERE account_id = ? ORDER BY date",
                (aid,),
            )
        ]
        meta_row = conn.execute(
            "SELECT * FROM investment_estimate_meta WHERE account_id = ?", (aid,)
        ).fetchone()
        estimate_meta: EstimateMetaOut | None = None
        if meta_row is not None:
            estimate_meta = {
                "anchor_date": meta_row["anchor_date"],
                "window_start": meta_row["window_start"],
                "window_end": meta_row["window_end"],
                "excluded_count": meta_row["excluded_count"],
                "excluded_value": meta_row["excluded_value"],
                "warnings": [str(w) for w in json.loads(meta_row["warnings"] or "[]")],
                "status": meta_row["status"],
            }
        holdings: list[HoldingOut] = [
            {
                "security_name": r["name"],
                "ticker": r["ticker_symbol"],
                "quantity": r["quantity"],
                "price": r["institution_price"],
                "value": r["institution_value"],
                "price_as_of": r["institution_price_as_of"],
            }
            for r in conn.execute(
                "SELECT s.name, s.ticker_symbol, h.quantity, h.institution_price,"
                " h.institution_value, h.institution_price_as_of FROM holdings_snapshots h"
                " LEFT JOIN securities s ON s.security_id = h.security_id"
                " WHERE h.account_id = ? AND h.date = (SELECT MAX(date) FROM holdings_snapshots"
                " WHERE account_id = ?) ORDER BY h.institution_value IS NULL,"
                " h.institution_value DESC, s.ticker_symbol, h.security_id",
                (aid, aid),
            )
        ]
        # Net money moved in (+) or out (-) per day; Plaid reports cash credited as negative.
        flows = [
            (r[0], round(-r[1], 2))
            for r in conn.execute(
                "SELECT it.date, SUM(it.amount) FROM investment_transactions it"
                " LEFT JOIN securities s ON s.security_id = it.security_id"
                f" WHERE it.account_id = ? AND {INV_CASH_MOVEMENT_SQL}"
                " GROUP BY it.date ORDER BY it.date",
                (aid,),
            )
        ]
        out.append(
            {
                "account_id": aid,
                "ownership": acc.ownership,
                "actual": actual,
                "estimated": estimated,
                "estimate_meta": estimate_meta,
                "holdings": holdings,
                "flows": flows,
            }
        )
    return out


def build_dataset(conn: Connection, today: date) -> DataPayload:
    """The full /api/data payload, read inside one read transaction."""
    with read_unit(conn):
        status = _status(conn)
        return {
            "data_version": status["data_version"],
            "generated_at": now_iso(),
            "status": {"newest_data_at": status["newest_data_at"], "items": status["items"]},
            "accounts": [
                _account_out(r)
                for r in conn.execute(
                    _ACCOUNT_SQL + " ORDER BY i.institution_name, a.name, a.account_id"
                )
            ],
            "categories": [
                category_out(r)
                for r in conn.execute(
                    "SELECT category_id, name, kind, applies_to, hidden FROM categories"
                    " ORDER BY rowid"
                )
            ],
            "rules": [
                rule_out(r)
                for r in conn.execute(
                    "SELECT rule_id, merchant_key, category_id, flow_class, recurring"
                    " FROM merchant_rules ORDER BY rule_id"
                )
            ],
            "transactions": _transactions(conn),
            "recurring": _recurring(conn),
            "investments": _investments(conn),
            "net_worth": compute_net_worth(conn, today),
            "quality": compute_quality(conn),
        }
