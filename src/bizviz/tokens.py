"""Keychain mirror of Plaid access tokens, the item index, and token recovery.

Every Plaid Trial slot is permanent, so a token must never be lost: it is mirrored to the
Keychain before the database row exists, and `recover_tokens` rebuilds rows from the mirror.
"""

import json
import logging
import threading
from datetime import UTC
from typing import Any

from bizviz.config import KC_ITEM_INDEX, KC_TOKEN_PREFIX
from bizviz.context import AppContext
from bizviz.db.connection import (
    Connection,
    bump_data_version,
    get_setting,
    now_iso,
    set_setting,
    write_unit,
)
from bizviz.platform.base import SecretStore
from bizviz.sync import synclog

log = logging.getLogger(__name__)

INDEX_BACKUP_KEY = KC_ITEM_INDEX + ".corrupt"
LINK_MODES = ("bank", "investment", "loan")

_INDEX_LOCK = threading.Lock()

# Institution names recovered by recover_tokens in this process.
RECOVERED_THIS_RUN: list[str] = []


def _read_unlocked(secrets: SecretStore) -> list[dict[str, Any]]:
    raw = secrets.get(KC_ITEM_INDEX)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, list):
        log.error("plaid item index is corrupt; keeping a copy and starting a new index")
        secrets.set(INDEX_BACKUP_KEY, raw)
        return []
    return [e for e in data if isinstance(e, dict) and e.get("item_id")]


def _merge_entry(existing: dict[str, Any] | None, entry: dict[str, Any]) -> dict[str, Any]:
    if existing is None:
        return dict(entry)
    merged = dict(existing)
    for key, value in entry.items():
        if value is not None and not (key == "created_at" and merged.get(key)):
            merged[key] = value
    if existing.get("removed_at") is not None:
        merged["removed_at"] = existing["removed_at"]
    return merged


def _merge_into(index: list[dict[str, Any]], entries: list[dict[str, Any]]) -> None:
    by_id = {e["item_id"]: i for i, e in enumerate(index)}
    for entry in entries:
        pos = by_id.get(entry["item_id"])
        if pos is None:
            by_id[entry["item_id"]] = len(index)
            index.append(dict(entry))
        else:
            index[pos] = _merge_entry(index[pos], entry)


def _write(secrets: SecretStore, index: list[dict[str, Any]]) -> None:
    secrets.set(KC_ITEM_INDEX, json.dumps(index))


def mirror_token(secrets: SecretStore, item_id: str, access_token: str) -> None:
    secrets.set(KC_TOKEN_PREFIX + item_id, access_token)


def index_read(secrets: SecretStore) -> list[dict[str, Any]]:
    with _INDEX_LOCK:
        return _read_unlocked(secrets)


def index_merge(secrets: SecretStore, entries: list[dict[str, Any]]) -> None:
    """Union `entries` into the index by item_id; a non-null removed_at always wins."""
    with _INDEX_LOCK:
        index = _read_unlocked(secrets)
        _merge_into(index, entries)
        _write(secrets, index)


def index_upsert(secrets: SecretStore, entry: dict[str, Any]) -> None:
    index_merge(secrets, [entry])


def db_index_entry(row: Any) -> dict[str, Any]:
    return {
        "item_id": row["item_id"],
        "institution_id": row["institution_id"],
        "institution_name": row["institution_name"],
        "link_mode": row["link_mode"],
        "created_at": row["created_at"],
        "removed_at": row["removed_at"],
    }


def mirror_all_from_db(ctx: AppContext) -> None:
    secrets = ctx.platform.secrets
    conn = ctx.connect()
    try:
        rows = conn.execute("SELECT * FROM items ORDER BY item_id").fetchall()
    finally:
        conn.close()
    for row in rows:
        mirror_token(secrets, row["item_id"], row["access_token"])
    index_merge(secrets, [db_index_entry(r) for r in rows])


def recovered_items(conn: Connection) -> list[dict[str, Any]]:
    raw = get_setting(conn, "recovered_items")
    if not raw:
        return []
    data = json.loads(raw)
    return data if isinstance(data, list) else []


def _bump_created_count(conn: Connection, index_len: int) -> None:
    current = int(get_setting(conn, "plaid_items_created_count") or 0)
    if index_len > current:
        set_setting(conn, "plaid_items_created_count", str(index_len))


def recover_tokens(ctx: AppContext) -> list[str]:
    """Re-insert indexed items missing from the DB; returns recovered institution names."""
    secrets = ctx.platform.secrets
    index = index_read(secrets)
    conn = ctx.connect()
    recovered: list[dict[str, Any]] = []
    try:
        known = {r[0] for r in conn.execute("SELECT item_id FROM items").fetchall()}
        missing = []
        for entry in index:
            if entry["item_id"] in known:
                continue
            token = secrets.get(KC_TOKEN_PREFIX + entry["item_id"])
            if token:
                missing.append((entry, token))
        current = int(get_setting(conn, "plaid_items_created_count") or 0)
        if not missing and len(index) <= current:
            return []
        now = now_iso()
        recovered_at = ctx.now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with write_unit(conn):
            for entry, token in missing:
                link_mode = entry.get("link_mode")
                conn.execute(
                    "INSERT INTO items(item_id, institution_id, institution_name, access_token, "
                    "link_mode, status, products, created_at, removed_at) "
                    "VALUES (?, ?, ?, ?, ?, 'ok', '[]', ?, ?)",
                    (
                        entry["item_id"],
                        entry.get("institution_id"),
                        entry.get("institution_name"),
                        token,
                        link_mode if link_mode in LINK_MODES else "bank",
                        entry.get("created_at") or now,
                        entry.get("removed_at"),
                    ),
                )
                recovered.append(entry)
                synclog.finish(conn, synclog.start(conn, "recovery", entry["item_id"]), True, None)
            if recovered:
                names = recovered_items(conn)
                names.extend(
                    {
                        "item_id": e["item_id"],
                        "institution_name": e.get("institution_name"),
                        "recovered_at": recovered_at,
                    }
                    for e in recovered
                )
                set_setting(conn, "recovered_items", json.dumps(names))
            _bump_created_count(conn, len(index))
            bump_data_version(conn)
    finally:
        conn.close()
    for entry in recovered:
        log.info("recovered item from keychain mirror item=%s", entry["item_id"])
        if entry.get("removed_at") is None:
            ctx.request_sync(entry["item_id"])
    names_out = [str(e.get("institution_name") or "") for e in recovered]
    RECOVERED_THIS_RUN.extend(names_out)
    return names_out


def apply_index_removed_at(ctx: AppContext) -> None:
    """Copy removed_at from the Keychain index onto DB items still marked active."""
    removed = {
        e["item_id"]: e["removed_at"]
        for e in index_read(ctx.platform.secrets)
        if e.get("removed_at")
    }
    if not removed:
        return
    conn = ctx.connect()
    try:
        with write_unit(conn):
            changed = 0
            for item_id, removed_at in removed.items():
                cur = conn.execute(
                    "UPDATE items SET removed_at = ? WHERE item_id = ? AND removed_at IS NULL",
                    (removed_at, item_id),
                )
                changed += cur.rowcount
            if changed:
                bump_data_version(conn)
    finally:
        conn.close()
