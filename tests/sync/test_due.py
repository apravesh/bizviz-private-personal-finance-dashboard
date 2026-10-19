from datetime import UTC, date, datetime, timedelta
from typing import Any

from bizviz.db.connection import Connection
from bizviz.sync import synclog
from bizviz.sync.due import (
    due_daily_items,
    due_transaction_items,
    items_needing_forced_sync,
)
from tests.factories import make_item

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
TODAY = date(2026, 10, 15)


def ago(**delta: float) -> str:
    return (NOW - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def add(db: Connection, item_id: str = "item-1", **kw: Any) -> str:
    kw.setdefault("historical_complete", 1)
    return make_item(db, item_id, **kw)


def log(db: Connection, job: str, ok: bool, code: str | None, transient: bool) -> None:
    synclog.finish(db, synclog.start(db, job, "item-1"), ok, code, transient)


class TestTransactions:
    def test_ok_item_due_after_four_hours(self, db: Connection) -> None:
        add(db, "fresh", last_success_at=ago(hours=3, minutes=59), last_attempt_at=ago(hours=4))
        add(db, "stale", last_success_at=ago(hours=4), last_attempt_at=ago(hours=4))
        add(db, "never")
        assert due_transaction_items(db, NOW, None) == ["never", "stale"]

    def test_fifteen_minutes_while_history_incomplete(self, db: Connection) -> None:
        add(db, "a", historical_complete=0, last_success_at=ago(minutes=14))
        add(db, "b", historical_complete=0, last_success_at=ago(minutes=15))
        assert due_transaction_items(db, NOW, None) == ["b"]

    def test_failed_items_wait_four_hours_since_last_attempt(self, db: Connection) -> None:
        for status in ("needs_attention", "error"):
            add(db, f"{status}-recent", status=status, last_attempt_at=ago(hours=3, minutes=59))
            add(db, f"{status}-old", status=status, last_attempt_at=ago(hours=4))
        add(db, "needs-never", status="needs_attention")
        assert due_transaction_items(db, NOW, None) == [
            "error-old",
            "needs-never",
            "needs_attention-old",
        ]

    def test_failed_item_with_old_success_still_waits_on_attempt(self, db: Connection) -> None:
        add(db, status="error", last_success_at=ago(days=3), last_attempt_at=ago(hours=1))
        assert due_transaction_items(db, NOW, None) == []

    def test_fifteen_minute_spacing_after_transient_failure(self, db: Connection) -> None:
        add(db, last_success_at=ago(hours=6), last_attempt_at=ago(minutes=14))
        log(db, "transactions", False, "INSTITUTION_DOWN", True)
        assert due_transaction_items(db, NOW, None) == []
        db.execute("UPDATE items SET last_attempt_at = ?", (ago(minutes=15),))
        assert due_transaction_items(db, NOW, None) == ["item-1"]

    def test_attempt_newer_than_success_requires_spacing_even_without_log(
        self, db: Connection
    ) -> None:
        add(db, last_success_at=ago(hours=6), last_attempt_at=ago(minutes=5))
        assert due_transaction_items(db, NOW, None) == []

    def test_transient_log_of_a_different_job_is_ignored(self, db: Connection) -> None:
        add(db, last_success_at=ago(hours=6), last_attempt_at=ago(hours=6))
        log(db, "daily", False, "INSTITUTION_DOWN", True)
        assert due_transaction_items(db, NOW, None) == ["item-1"]

    def test_removed_items_never_due_even_when_forced(self, db: Connection) -> None:
        add(db, removed_at=ago(days=1))
        assert due_transaction_items(db, NOW, "all") == []
        assert due_transaction_items(db, NOW, {"item-1"}) == []

    def test_requires_transactions_product_and_step_available(self, db: Connection) -> None:
        add(db, "inv", products=["investments"])
        add(db, "unknown", products=[])
        add(db, "unavail", unavailable_steps='["transactions"]')
        assert due_transaction_items(db, NOW, "all") == []

    def test_forced_ignores_intervals(self, db: Connection) -> None:
        add(db, "a", last_success_at=ago(minutes=1), last_attempt_at=ago(minutes=1))
        add(db, "b", status="error", last_attempt_at=ago(minutes=1))
        add(db, "c", last_success_at=ago(minutes=1))
        assert due_transaction_items(db, NOW, {"a", "b"}) == ["a", "b"]
        assert due_transaction_items(db, NOW, "all") == ["a", "b", "c"]
        assert due_transaction_items(db, NOW, set()) == []


class TestDaily:
    def test_due_when_not_done_today(self, db: Connection) -> None:
        add(db, "done", last_daily_at="2026-10-15")
        add(db, "yesterday", last_daily_at="2026-10-14")
        add(db, "never")
        assert due_daily_items(db, NOW, TODAY) == ["never", "yesterday"]

    def test_removed_never_due(self, db: Connection) -> None:
        add(db, removed_at=ago(days=1))
        assert due_daily_items(db, NOW, TODAY) == []

    def test_thirty_minute_retry_spacing(self, db: Connection) -> None:
        add(db, "a", last_daily_attempt_at=ago(minutes=29))
        add(db, "b", last_daily_attempt_at=ago(minutes=30))
        assert due_daily_items(db, NOW, TODAY) == ["b"]

    def test_fifteen_minute_spacing_after_transient_daily_failure(self, db: Connection) -> None:
        add(db, last_daily_attempt_at=ago(minutes=16))
        log(db, "daily", False, "INSTITUTION_DOWN", True)
        assert due_daily_items(db, NOW, TODAY) == ["item-1"]
        db.execute("UPDATE items SET last_daily_attempt_at = ?", (ago(minutes=14),))
        assert due_daily_items(db, NOW, TODAY) == []

    def test_hard_failure_keeps_thirty_minutes(self, db: Connection) -> None:
        add(db, last_daily_attempt_at=ago(minutes=16))
        log(db, "daily", False, "WHAT", False)
        assert due_daily_items(db, NOW, TODAY) == []

    def test_day_rollover_makes_item_due_again(self, db: Connection) -> None:
        add(db, last_daily_at="2026-10-15", last_daily_attempt_at=ago(hours=5))
        assert due_daily_items(db, NOW, TODAY) == []
        assert due_daily_items(db, NOW, date(2026, 10, 16)) == ["item-1"]


class TestForcedSyncSelection:
    def test_only_non_removed_items_with_unknown_products(self, db: Connection) -> None:
        add(db, "recovered", products=[])
        add(db, "known")
        add(db, "removed", products=[], removed_at=ago(days=1))
        assert items_needing_forced_sync(db, NOW) == ["recovered"]

    def test_spaced_fifteen_minutes_after_attempt(self, db: Connection) -> None:
        add(db, "recent", products=[], last_attempt_at=ago(minutes=14))
        add(db, "older", products=[], last_attempt_at=ago(minutes=15))
        assert items_needing_forced_sync(db, NOW) == ["older"]

    def test_default_clock_is_wall_time(self, db: Connection) -> None:
        add(db, products=[])
        assert items_needing_forced_sync(db) == ["item-1"]


def test_transient_log_blocks_even_if_success_is_newer_than_attempt(db: Connection) -> None:
    add(db, historical_complete=0, last_success_at=ago(minutes=20), last_attempt_at=ago(minutes=20))
    log(db, "transactions", False, "INSTITUTION_DOWN", True)
    assert due_transaction_items(db, NOW, None) == ["item-1"]
    db.execute("UPDATE items SET last_attempt_at = ?", (ago(minutes=5),))
    assert due_transaction_items(db, NOW, None) == []
