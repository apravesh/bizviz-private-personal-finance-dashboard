import json
import logging
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from bizviz.context import AppContext
from bizviz.db.connection import (
    Connection,
    bump_data_version,
    get_setting,
    set_setting,
    write_unit,
)
from bizviz.errors import InvalidInputError, NotFoundError
from bizviz.plaid_types import LinkMode, PlaidError, PlaidNetworkError
from bizviz.sync import synclog
from bizviz.sync.accounts import snapshot_balances, upsert_accounts
from bizviz.sync.errors import (
    classify_plaid_error,
    error_code_of,
    log_plaid_error,
    require_plaid,
)
from bizviz.sync.transactions import store_item_info, ts
from bizviz.tokens import db_index_entry, index_read, index_upsert, mirror_token

log = logging.getLogger(__name__)

SLOT_LIMIT = 10
CACHE_TTL = timedelta(hours=1)
ITEM_MODES = ("bank", "investment", "loan")


class SlotLimitError(Exception):
    """All Plaid Trial slots have been used; no new link may be started."""


@dataclass
class ExchangeResult:
    item_id: str
    duplicate: bool
    pending: bool


_cache: dict[str, tuple[str, str, datetime]] = {}
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _lock_for(public_token: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(public_token, threading.Lock())


def _cache_get(public_token: str, now: datetime) -> tuple[str, str] | None:
    with _guard:
        for key in [k for k, (_, _, exp) in _cache.items() if exp <= now]:
            del _cache[key]
        hit = _cache.get(public_token)
    return None if hit is None else (hit[0], hit[1])


def _cache_put(public_token: str, item_id: str, access_token: str, now: datetime) -> None:
    with _guard:
        _cache[public_token] = (item_id, access_token, now + CACHE_TTL)


def _get_item(conn: Connection, item_id: str) -> Any:
    row = conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
    if row is None:
        raise NotFoundError(item_id)
    return row


def create_link_token(ctx: AppContext, mode: LinkMode, item_id: str | None = None) -> str:
    plaid = require_plaid(ctx)
    conn = ctx.connect()
    try:
        if mode == "update":
            if item_id is None:
                raise NotFoundError("item_id")
            item = _get_item(conn, item_id)
            if item["removed_at"] is not None:
                raise NotFoundError(item_id)
            return plaid.create_link_token(mode=mode, access_token=item["access_token"])
        if int(get_setting(conn, "plaid_items_created_count") or 0) >= SLOT_LIMIT:
            raise SlotLimitError(f"all {SLOT_LIMIT} Plaid slots are used")
    finally:
        conn.close()
    return plaid.create_link_token(mode=mode)


def exchange_public_token(
    ctx: AppContext,
    public_token: str,
    institution_id: str | None,
    institution_name: str | None,
    mode: LinkMode,
) -> ExchangeResult:
    if mode not in ITEM_MODES:
        raise InvalidInputError("invalid_mode")
    plaid = require_plaid(ctx)
    with _lock_for(public_token):
        cached = _cache_get(public_token, ctx.now())
        if cached is not None:
            item_id, access_token = cached
        else:
            resp = plaid.exchange_public_token(public_token)
            item_id, access_token = str(resp["item_id"]), str(resp["access_token"])
            _cache_put(public_token, item_id, access_token, ctx.now())

        secrets = ctx.platform.secrets
        mirror_token(secrets, item_id, access_token)
        index_upsert(
            secrets,
            {
                "item_id": item_id,
                "institution_id": institution_id,
                "institution_name": institution_name,
                "link_mode": mode,
                "created_at": ts(ctx),
                "removed_at": None,
            },
        )
        try:
            return _finish_exchange(
                ctx, item_id, access_token, institution_id, institution_name, mode
            )
        except Exception as exc:
            log.error(
                "exchange follow-up failed item=%s error_type=%s", item_id, type(exc).__name__
            )
            return ExchangeResult(item_id, False, True)
        finally:
            ctx.request_sync(item_id)


def _finish_exchange(
    ctx: AppContext,
    item_id: str,
    access_token: str,
    institution_id: str | None,
    institution_name: str | None,
    mode: str,
) -> ExchangeResult:
    index_len = len(index_read(ctx.platform.secrets))
    conn = ctx.connect()
    try:
        log_id = synclog.start(conn, "link", item_id)
        try:
            result, code, transient = _exchange_steps(
                ctx, conn, item_id, access_token, institution_id, institution_name, mode, index_len
            )
        except Exception:
            with suppress(Exception):
                synclog.finish(conn, log_id, False, "INTERNAL_ERROR")
            raise
        synclog.finish(conn, log_id, code is None, code, transient=transient)
        return result
    finally:
        conn.close()


def _exchange_steps(
    ctx: AppContext,
    conn: Connection,
    item_id: str,
    access_token: str,
    institution_id: str | None,
    institution_name: str | None,
    mode: str,
    index_len: int,
) -> tuple[ExchangeResult, str | None, bool]:
    """Insert the item and fill products/accounts; returns (result, error_code, transient)."""
    plaid = require_plaid(ctx)
    with write_unit(conn):
        exists = conn.execute("SELECT 1 FROM items WHERE item_id = ?", (item_id,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO items(item_id, institution_id, institution_name, access_token, "
                "link_mode, status, products, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'ok', '[]', ?)",
                (item_id, institution_id, institution_name, access_token, mode, ts(ctx)),
            )
            current = int(get_setting(conn, "plaid_items_created_count") or 0)
            set_setting(conn, "plaid_items_created_count", str(max(current + 1, index_len)))
            bump_data_version(conn)
    item = _get_item(conn, item_id)
    if exists and item["products"] != "[]":
        return ExchangeResult(item_id, item["duplicate_of"] is not None, False), None, False
    try:
        item_resp = plaid.item_get(access_token)
        accounts = plaid.accounts_get(access_token).get("accounts") or []
    except (PlaidError, PlaidNetworkError) as exc:
        log_plaid_error("exchange follow-up", item_id, exc)
        transient = classify_plaid_error(exc) == "transient"
        return ExchangeResult(item_id, False, True), error_code_of(exc), transient
    store_item_info(ctx, conn, item_id, item_resp)
    with write_unit(conn):
        upsert_accounts(conn, item_id, accounts)
        snapshot_balances(conn, accounts, local_date=ctx.today(), investment_dates={})
        bump_data_version(conn)
    duplicate = _get_item(conn, item_id)["duplicate_of"] is not None
    return ExchangeResult(item_id, duplicate, False), None, False


def mark_repaired(ctx: AppContext, item_id: str) -> None:
    conn = ctx.connect()
    try:
        _get_item(conn, item_id)
        with write_unit(conn):
            conn.execute(
                "UPDATE items SET status = 'ok', error_code = NULL, unavailable_steps = ? "
                "WHERE item_id = ?",
                (json.dumps([]), item_id),
            )
            bump_data_version(conn)
    finally:
        conn.close()
    ctx.request_sync(item_id)


def disconnect_item(ctx: AppContext, item_id: str) -> None:
    plaid = require_plaid(ctx)
    conn = ctx.connect()
    try:
        item = _get_item(conn, item_id)
        if item["removed_at"] is not None:
            return
        try:
            plaid.item_remove(item["access_token"])
        except PlaidError as exc:
            if exc.error_code != "ITEM_NOT_FOUND":
                raise
        removed_at = ts(ctx)
        index_upsert(ctx.platform.secrets, {**db_index_entry(item), "removed_at": removed_at})
        with write_unit(conn):
            conn.execute("UPDATE items SET removed_at = ? WHERE item_id = ?", (removed_at, item_id))
            bump_data_version(conn)
    finally:
        conn.close()
