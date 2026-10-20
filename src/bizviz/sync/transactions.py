import json
import logging
from dataclasses import dataclass
from datetime import UTC, date
from typing import Any

from bizviz.analytics.merchant import merchant_key
from bizviz.context import AppContext
from bizviz.db.connection import Connection, bump_data_version, write_unit
from bizviz.plaid_types import PlaidError, PlaidNetworkError
from bizviz.sync import synclog
from bizviz.sync.accounts import upsert_accounts
from bizviz.sync.errors import (
    add_unavailable_step,
    apply_item_error,
    classify_plaid_error,
    error_code_of,
    log_plaid_error,
    require_plaid,
)

log = logging.getLogger(__name__)

MUTATION_ERROR = "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"
MAX_RESTARTS = 3
CARRY_OVER_WINDOW_DAYS = 7


@dataclass
class SyncOutcome:
    ok: bool
    kind: str | None = None
    error_code: str | None = None
    added: int = 0
    modified: int = 0
    removed: int = 0


@dataclass
class _Pages:
    added: list[dict[str, Any]]
    modified: list[dict[str, Any]]
    removed: list[str]
    accounts: dict[str, dict[str, Any]]
    next_cursor: str | None
    update_status: str | None


def ts(ctx: AppContext) -> str:
    return ctx.now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def eligible_for_transactions(item: Any) -> bool:
    if item is None or item["removed_at"] is not None:
        return False
    return "transactions" in json.loads(item["products"]) and "transactions" not in json.loads(
        item["unavailable_steps"]
    )


def _fetch_pages(ctx: AppContext, access_token: str, original_cursor: str | None) -> _Pages:
    plaid = require_plaid(ctx)
    restarts = 0
    while True:
        pages = _Pages([], [], [], {}, original_cursor, None)
        cursor = original_cursor
        try:
            while True:
                page = plaid.transactions_sync(access_token, cursor)
                pages.added.extend(page.get("added") or [])
                pages.modified.extend(page.get("modified") or [])
                pages.removed.extend(
                    r["transaction_id"] if isinstance(r, dict) else r
                    for r in page.get("removed") or []
                )
                for acct in page.get("accounts") or []:
                    pages.accounts[acct["account_id"]] = acct
                pages.next_cursor = page.get("next_cursor")
                pages.update_status = page.get("transactions_update_status")
                if not page.get("has_more"):
                    return pages
                cursor = page["next_cursor"]
        except PlaidError as exc:
            if exc.error_code != MUTATION_ERROR or restarts >= MAX_RESTARTS:
                raise
            restarts += 1
            log.info("sync restart after mutation during pagination restarts=%d", restarts)


def _txn_row(txn: dict[str, Any], now: str) -> dict[str, Any]:
    pfc = txn.get("personal_finance_category") or {}
    return {
        "transaction_id": txn["transaction_id"],
        "account_id": txn["account_id"],
        "date": txn["date"],
        "authorized_date": txn.get("authorized_date"),
        "amount": round(float(txn["amount"]), 2),
        "currency": txn.get("iso_currency_code") or txn.get("unofficial_currency_code"),
        "name": txn.get("name"),
        "merchant_name": txn.get("merchant_name"),
        "merchant_entity_id": txn.get("merchant_entity_id"),
        "pfc_primary": pfc.get("primary"),
        "pfc_detailed": pfc.get("detailed"),
        "payment_channel": txn.get("payment_channel"),
        "pending": int(bool(txn.get("pending"))),
        "pending_transaction_id": txn.get("pending_transaction_id"),
        "updated_at": now,
    }


_UPSERT_SQL = (
    "INSERT INTO transactions(transaction_id, account_id, date, authorized_date, amount, "
    "currency, name, merchant_name, merchant_entity_id, pfc_primary, pfc_detailed, "
    "payment_channel, pending, pending_transaction_id, updated_at) VALUES "
    "(:transaction_id, :account_id, :date, :authorized_date, :amount, :currency, :name, "
    ":merchant_name, :merchant_entity_id, :pfc_primary, :pfc_detailed, :payment_channel, "
    ":pending, :pending_transaction_id, :updated_at) "
    "ON CONFLICT(transaction_id) DO UPDATE SET account_id = excluded.account_id, "
    "date = excluded.date, authorized_date = excluded.authorized_date, "
    "amount = excluded.amount, currency = excluded.currency, name = excluded.name, "
    "merchant_name = excluded.merchant_name, merchant_entity_id = excluded.merchant_entity_id, "
    "pfc_primary = excluded.pfc_primary, pfc_detailed = excluded.pfc_detailed, "
    "payment_channel = excluded.payment_channel, pending = excluded.pending, "
    "pending_transaction_id = excluded.pending_transaction_id, updated_at = excluded.updated_at"
)


def _copy_override(conn: Connection, source_id: str, target_id: str, now: str) -> None:
    conn.execute(
        "INSERT INTO tx_overrides(transaction_id, category_id, flow_class, recurring, updated_at) "
        "SELECT ?, category_id, flow_class, recurring, ? FROM tx_overrides "
        "WHERE transaction_id = ?",
        (target_id, now, source_id),
    )


def _has_override(conn: Connection, transaction_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM tx_overrides WHERE transaction_id = ?", (transaction_id,)
    ).fetchone()
    return row is not None


def _carry_over_by_pending_id(conn: Connection, added: list[dict[str, Any]], now: str) -> set[str]:
    carried_sources: set[str] = set()
    for txn in added:
        pending_id = txn.get("pending_transaction_id")
        new_id = txn["transaction_id"]
        if not pending_id or pending_id == new_id:
            continue
        if _has_override(conn, pending_id) and not _has_override(conn, new_id):
            _copy_override(conn, pending_id, new_id, now)
            carried_sources.add(pending_id)
    return carried_sources


def _carry_over_fallback(
    conn: Connection,
    removed_rows: list[Any],
    added_ids: list[str],
    carried_sources: set[str],
    now: str,
) -> None:
    if not removed_rows:
        return
    placeholders = ", ".join("?" for _ in added_ids)
    candidates = (
        conn.execute(
            "SELECT transaction_id, account_id, date, amount, merchant_name, name "
            f"FROM transactions WHERE pending = 0 AND transaction_id IN ({placeholders})",
            added_ids,
        ).fetchall()
        if added_ids
        else []
    )
    for removed in sorted(removed_rows, key=lambda r: r["transaction_id"]):
        rid = removed["transaction_id"]
        if rid in carried_sources or not removed["pending"] or not _has_override(conn, rid):
            continue
        r_date = date.fromisoformat(removed["date"])
        r_amount = round(removed["amount"], 2)
        r_key = merchant_key(removed["merchant_name"], removed["name"])
        r_named = r_key != "unknown" and bool(
            (removed["merchant_name"] or "").strip() or (removed["name"] or "").strip()
        )
        best: tuple[int, str] | None = None
        for cand in candidates:
            if cand["account_id"] != removed["account_id"]:
                continue
            diff = abs((date.fromisoformat(cand["date"]) - r_date).days)
            if diff > CARRY_OVER_WINDOW_DAYS:
                continue
            same_amount = round(cand["amount"], 2) == r_amount
            same_merchant = r_named and merchant_key(cand["merchant_name"], cand["name"]) == r_key
            if not (same_amount or same_merchant):
                continue
            if _has_override(conn, cand["transaction_id"]):
                continue
            rank = (diff, cand["transaction_id"])
            if best is None or rank < best:
                best = rank
        if best is not None:
            _copy_override(conn, rid, best[1], now)


def _persist_pages(ctx: AppContext, conn: Connection, item_id: str, pages: _Pages) -> None:
    now = ts(ctx)
    with write_unit(conn):
        upsert_accounts(conn, item_id, list(pages.accounts.values()))
        known = {r[0] for r in conn.execute("SELECT account_id FROM accounts").fetchall()}
        removed_rows: list[Any] = []
        if pages.removed:
            marks = ", ".join("?" for _ in pages.removed)
            removed_rows = conn.execute(
                "SELECT transaction_id, account_id, date, amount, merchant_name, name, pending "
                f"FROM transactions WHERE transaction_id IN ({marks})",
                pages.removed,
            ).fetchall()
        added_ids: list[str] = []
        for txn in pages.added:
            if txn["account_id"] not in known:
                log.warning(
                    "skipping transaction for unknown account txn=%s", txn["transaction_id"]
                )
                continue
            conn.execute(_UPSERT_SQL, _txn_row(txn, now))
            added_ids.append(txn["transaction_id"])
        for txn in pages.modified:
            if txn["account_id"] not in known:
                log.warning(
                    "skipping transaction for unknown account txn=%s", txn["transaction_id"]
                )
                continue
            conn.execute(_UPSERT_SQL, _txn_row(txn, now))
        for tid in pages.removed:
            conn.execute("DELETE FROM transactions WHERE transaction_id = ?", (tid,))
        added_set = set(added_ids)
        carried = _carry_over_by_pending_id(
            conn, [t for t in pages.added if t["transaction_id"] in added_set], now
        )
        _carry_over_fallback(conn, removed_rows, added_ids, carried, now)
        historical = 1 if pages.update_status == "HISTORICAL_UPDATE_COMPLETE" else 0
        conn.execute(
            "UPDATE items SET transactions_cursor = ?, last_success_at = ?, "
            "historical_complete = max(historical_complete, ?) WHERE item_id = ?",
            (pages.next_cursor, now, historical, item_id),
        )
        bump_data_version(conn)


def store_item_info(
    ctx: AppContext, conn: Connection, item_id: str, item_resp: dict[str, Any]
) -> None:
    """Persist products/consent and map a reported item error to status."""
    item = item_resp.get("item") or {}
    products = sorted(set(item.get("products") or []) | set(item.get("billed_products") or []))
    error = item.get("error")
    with write_unit(conn):
        conn.execute(
            "UPDATE items SET products = ?, consent_expires_at = ? WHERE item_id = ?",
            (json.dumps(products), item.get("consent_expiration_time"), item_id),
        )
        if error:
            kind = classify_plaid_error(
                PlaidError(str(error.get("error_type") or ""), str(error.get("error_code") or ""))
            )
            apply_item_error(conn, item_id, kind, str(error.get("error_code") or "UNKNOWN"))
        else:
            row = conn.execute(
                "SELECT status, error_code FROM items WHERE item_id = ?", (item_id,)
            ).fetchone()
            if row is not None and (row["status"] != "ok" or row["error_code"] is not None):
                conn.execute(
                    "UPDATE items SET status = 'ok', error_code = NULL WHERE item_id = ?",
                    (item_id,),
                )
                bump_data_version(conn)


def record_failure(
    conn: Connection, item_id: str, step: str, exc: PlaidError | PlaidNetworkError
) -> str:
    """Apply the item error mapping for `exc`; returns the error kind."""
    kind = classify_plaid_error(exc)
    code = error_code_of(exc)
    if kind == "unavailable":
        add_unavailable_step(conn, item_id, step)
    else:
        apply_item_error(conn, item_id, kind, code)
    return kind


def sync_item_transactions(ctx: AppContext, item_id: str) -> SyncOutcome:
    conn = ctx.connect()
    try:
        return _sync(ctx, conn, item_id)
    finally:
        conn.close()


def _sync(ctx: AppContext, conn: Connection, item_id: str) -> SyncOutcome:
    item = conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
    if not eligible_for_transactions(item):
        return SyncOutcome(ok=True)
    plaid = require_plaid(ctx)
    log_id = synclog.start(conn, "transactions", item_id)
    with write_unit(conn):
        conn.execute(
            "UPDATE items SET last_attempt_at = ? WHERE item_id = ?",
            (ts(ctx), item_id),
        )
    counts = (0, 0, 0)
    try:
        pages = _fetch_pages(ctx, item["access_token"], item["transactions_cursor"])
        counts = (len(pages.added), len(pages.modified), len(pages.removed))
        _persist_pages(ctx, conn, item_id, pages)
        store_item_info(ctx, conn, item_id, plaid.item_get(item["access_token"]))
    except (PlaidError, PlaidNetworkError) as exc:
        log_plaid_error("transactions sync", item_id, exc)
        kind = record_failure(conn, item_id, "transactions", exc)
        code = error_code_of(exc)
        synclog.finish(conn, log_id, False, code, transient=kind == "transient")
        return SyncOutcome(False, kind, code, *counts)
    except Exception:
        synclog.finish(conn, log_id, False, "INTERNAL_ERROR")
        raise
    synclog.finish(conn, log_id, True, None)
    log.info("transactions synced item=%s added=%d modified=%d removed=%d", item_id, *counts)
    return SyncOutcome(True, None, None, *counts)
