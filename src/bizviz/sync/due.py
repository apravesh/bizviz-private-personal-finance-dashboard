import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from bizviz.db.connection import Connection

OK_INTERVAL = timedelta(hours=4)
BACKFILL_INTERVAL = timedelta(minutes=15)
FAILED_INTERVAL = timedelta(hours=4)
TRANSIENT_RETRY = timedelta(minutes=15)
DAILY_RETRY = timedelta(minutes=30)


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _last_log(conn: Connection, item_id: str, job: str) -> Any:
    return conn.execute(
        "SELECT ok, transient FROM sync_log WHERE item_id = ? AND job = ? ORDER BY id DESC LIMIT 1",
        (item_id, job),
    ).fetchone()


def _last_failed_transiently(conn: Connection, item_id: str, job: str) -> bool:
    row = _last_log(conn, item_id, job)
    return row is not None and row["ok"] == 0 and bool(row["transient"])


def items_needing_forced_sync(conn: Connection, now: datetime | None = None) -> list[str]:
    """Non-removed items whose products are unknown ('[]'), spaced 15 min after an attempt."""
    now = now or datetime.now(UTC)
    rows = conn.execute(
        "SELECT item_id, last_attempt_at FROM items "
        "WHERE removed_at IS NULL AND products = '[]' ORDER BY item_id"
    ).fetchall()
    due = []
    for row in rows:
        attempt = parse_ts(row["last_attempt_at"])
        if attempt is None or now - attempt >= TRANSIENT_RETRY:
            due.append(row["item_id"])
    return due


def due_transaction_items(
    conn: Connection, now: datetime, forced: set[str] | Literal["all"] | None
) -> list[str]:
    rows = conn.execute("SELECT * FROM items WHERE removed_at IS NULL ORDER BY item_id").fetchall()
    due: list[str] = []
    for row in rows:
        item_id = row["item_id"]
        if "transactions" not in json.loads(row["products"]):
            continue
        if "transactions" in json.loads(row["unavailable_steps"]):
            continue
        if forced == "all" or (forced is not None and item_id in forced):
            due.append(item_id)
            continue
        attempt = parse_ts(row["last_attempt_at"])
        success = parse_ts(row["last_success_at"])
        if row["status"] == "ok":
            interval = OK_INTERVAL if row["historical_complete"] else BACKFILL_INTERVAL
            if success is not None and now - success < interval:
                continue
            failed_last = attempt is not None and (success is None or attempt > success)
            if (
                failed_last or _last_failed_transiently(conn, item_id, "transactions")
            ) and attempt is not None:
                if now - attempt < TRANSIENT_RETRY:
                    continue
            due.append(item_id)
        elif attempt is None or now - attempt >= FAILED_INTERVAL:
            due.append(item_id)
    return due


def due_daily_items(conn: Connection, now: datetime, today: date) -> list[str]:
    rows = conn.execute(
        "SELECT item_id, last_daily_at, last_daily_attempt_at FROM items "
        "WHERE removed_at IS NULL ORDER BY item_id"
    ).fetchall()
    due: list[str] = []
    for row in rows:
        item_id = row["item_id"]
        if row["last_daily_at"] == today.isoformat():
            continue
        attempt = parse_ts(row["last_daily_attempt_at"])
        if attempt is not None:
            retry = (
                TRANSIENT_RETRY if _last_failed_transiently(conn, item_id, "daily") else DAILY_RETRY
            )
            if now - attempt < retry:
                continue
        due.append(item_id)
    return due
