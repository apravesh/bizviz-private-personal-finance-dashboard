import logging
from datetime import date, timedelta
from typing import Any

from bizviz.context import AppContext
from bizviz.db.connection import Connection, bump_data_version, write_unit
from bizviz.sync.accounts import upsert_accounts
from bizviz.sync.errors import require_plaid
from bizviz.sync.transactions import ts

log = logging.getLogger(__name__)

PAGE_SIZE = 500
FULL_HISTORY_DAYS = 730
INCREMENTAL_DAYS = 30


def _round(value: Any) -> float | None:
    return None if value is None else round(float(value), 2)


def upsert_securities(conn: Connection, securities: list[dict[str, Any]], now: str) -> None:
    with write_unit(conn):
        for sec in securities:
            conn.execute(
                "INSERT INTO securities(security_id, ticker_symbol, name, type, "
                "is_cash_equivalent, close_price, close_price_as_of, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(security_id) DO UPDATE SET ticker_symbol = excluded.ticker_symbol, "
                "name = excluded.name, type = excluded.type, "
                "is_cash_equivalent = excluded.is_cash_equivalent, "
                "close_price = excluded.close_price, "
                "close_price_as_of = excluded.close_price_as_of, updated_at = excluded.updated_at",
                (
                    sec["security_id"],
                    sec.get("ticker_symbol"),
                    sec.get("name"),
                    sec.get("type"),
                    int(bool(sec.get("is_cash_equivalent"))),
                    sec.get("close_price"),
                    sec.get("close_price_as_of"),
                    now,
                ),
            )


def _snapshot_date(holdings: list[dict[str, Any]], today: date) -> date:
    as_of = [h["institution_price_as_of"] for h in holdings if h.get("institution_price_as_of")]
    if not as_of:
        return today
    return min(today, date.fromisoformat(max(as_of)[:10]))


def capture_holdings(
    ctx: AppContext, item_id: str, conn: Connection | None = None
) -> dict[str, date]:
    """Fetch current holdings and store them; returns {investment account_id: snapshot date}."""
    own = conn is None
    conn = conn or ctx.connect()
    try:
        token = conn.execute(
            "SELECT access_token FROM items WHERE item_id = ?", (item_id,)
        ).fetchone()["access_token"]
        resp = require_plaid(ctx).investments_holdings_get(token)
        today = ctx.today()
        now = ts(ctx)
        by_account: dict[str, list[dict[str, Any]]] = {}
        for h in resp.get("holdings") or []:
            by_account.setdefault(h["account_id"], []).append(h)
        accounts = resp.get("accounts") or []
        investment_ids = {a["account_id"] for a in accounts if a.get("type") == "investment"}
        investment_ids |= set(by_account)
        dates: dict[str, date] = {}
        with write_unit(conn):
            upsert_securities(conn, resp.get("securities") or [], now)
            upsert_accounts(conn, item_id, accounts)
            for account_id in sorted(investment_ids):
                holdings = by_account.get(account_id, [])
                snap = _snapshot_date(holdings, today)
                dates[account_id] = snap
                conn.execute(
                    "DELETE FROM holdings_snapshots WHERE account_id = ? AND date = ?",
                    (account_id, snap.isoformat()),
                )
                for h in holdings:
                    conn.execute(
                        "INSERT OR REPLACE INTO holdings_snapshots(account_id, security_id, date, "
                        "quantity, institution_price, institution_price_as_of, institution_value, "
                        "cost_basis, captured_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            account_id,
                            h["security_id"],
                            snap.isoformat(),
                            h["quantity"],
                            h.get("institution_price"),
                            h.get("institution_price_as_of"),
                            _round(h.get("institution_value")),
                            _round(h.get("cost_basis")),
                            now,
                        ),
                    )
            conn.execute(
                "UPDATE items SET last_holdings_at = ? WHERE item_id = ?",
                (today.isoformat(), item_id),
            )
            bump_data_version(conn)
        log.info("holdings captured item=%s accounts=%d", item_id, len(dates))
        return dates
    finally:
        if own:
            conn.close()


def fetch_investment_transactions(
    ctx: AppContext, item_id: str, conn: Connection | None = None
) -> None:
    own = conn is None
    conn = conn or ctx.connect()
    try:
        item = conn.execute(
            "SELECT access_token, investments_history_complete FROM items WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        today = ctx.today()
        full = not item["investments_history_complete"]
        start = today - timedelta(days=FULL_HISTORY_DAYS if full else INCREMENTAL_DAYS)
        plaid = require_plaid(ctx)
        transactions: list[dict[str, Any]] = []
        securities: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = plaid.investments_transactions_get(
                item["access_token"], start, today, offset=offset, count=PAGE_SIZE
            )
            batch = page.get("investment_transactions") or []
            transactions.extend(batch)
            securities.extend(page.get("securities") or [])
            offset += len(batch)
            if not batch or offset >= int(page.get("total_investment_transactions") or 0):
                break
        now = ts(ctx)
        with write_unit(conn):
            upsert_securities(conn, securities, now)
            for t in transactions:
                conn.execute(
                    "INSERT INTO investment_transactions(investment_transaction_id, account_id, "
                    "security_id, date, name, type, subtype, quantity, amount, price, fees) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(investment_transaction_id) DO UPDATE SET "
                    "account_id = excluded.account_id, security_id = excluded.security_id, "
                    "date = excluded.date, name = excluded.name, type = excluded.type, "
                    "subtype = excluded.subtype, quantity = excluded.quantity, "
                    "amount = excluded.amount, price = excluded.price, fees = excluded.fees",
                    (
                        t["investment_transaction_id"],
                        t["account_id"],
                        t.get("security_id"),
                        t["date"],
                        t.get("name"),
                        t.get("type"),
                        t.get("subtype"),
                        t.get("quantity"),
                        _round(t.get("amount")),
                        t.get("price"),
                        _round(t.get("fees")),
                    ),
                )
            conn.execute(
                "UPDATE items SET last_inv_tx_at = ?, "
                "investments_history_complete = max(investments_history_complete, ?) "
                "WHERE item_id = ?",
                (today.isoformat(), int(full), item_id),
            )
            bump_data_version(conn)
        log.info(
            "investment transactions fetched item=%s count=%d full=%s",
            item_id,
            len(transactions),
            full,
        )
    finally:
        if own:
            conn.close()
