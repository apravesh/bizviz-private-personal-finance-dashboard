import logging
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from bizviz import certs
from bizviz.certs import leaf_needs_renewal
from bizviz.config import replace_config
from bizviz.context import AppContext
from bizviz.db.connection import Connection
from bizviz.plaid_fake import FakePlaidClient
from bizviz.sync.daily import DailyOutcome
from bizviz.sync.scheduler import Scheduler
from tests.conftest import FIXED_NOW
from tests.factories import make_item, set_now
from tests.investments.helpers import FakePriceSource, hold, sec, setup_account
from tests.sync.helpers import item_get, plaid_account, sync_page

TODAY = date(2026, 10, 15)


class Spies:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def record(self, name: str, result: Any = None) -> Callable[..., Any]:
        def spy(*args: Any, **kwargs: Any) -> Any:
            self.events.append((name, args[1] if name in ("sync", "forced", "daily") else args))
            return result(*args, **kwargs) if callable(result) else result

        return spy

    def names(self) -> list[str]:
        return [e[0] for e in self.events]

    def of(self, name: str) -> list[Any]:
        return [e[1] for e in self.events if e[0] == name]


@pytest.fixture
def spies(monkeypatch: pytest.MonkeyPatch) -> Spies:
    s = Spies()
    for attr, name, result in [
        ("sync_item_transactions", "sync", None),
        ("forced_sync", "forced", None),
        ("rebuild_classification", "classify", 1),
        ("run_daily_job", "daily", lambda ctx, item_id: DailyOutcome()),
        ("rebuild_estimates", "estimates", None),
        ("recover_tokens", "recover", []),
    ]:
        monkeypatch.setattr(f"bizviz.sync.scheduler.{attr}", s.record(name, result))
    return s


@pytest.fixture
def restarts() -> list[str]:
    return []


@pytest.fixture
def sched(ctx: AppContext, restarts: list[str]) -> Scheduler:
    certs.issue_leaf(ctx.cfg, ctx.platform.secrets, FIXED_NOW)
    return Scheduler(ctx, on_restart=lambda: restarts.append("restart"))


def advance(ctx: AppContext, delta: timedelta) -> datetime:
    now = ctx.now() + delta
    set_now(ctx, now)
    return now


def sync_log(db: Connection, job: str) -> list[Any]:
    return db.execute("SELECT * FROM sync_log WHERE job = ? ORDER BY id", (job,)).fetchall()


def test_due_transaction_syncs_run_before_one_classification_rebuild(
    sched: Scheduler, db: Connection, spies: Spies
) -> None:
    make_item(db, "item-1")
    make_item(db, "item-2", institution_name="Two")
    make_item(db, "item-3", products=["investments"])
    sched.tick()
    names = spies.names()
    assert spies.of("sync") == ["item-1", "item-2"]
    assert names.index("classify") > max(i for i, n in enumerate(names) if n == "sync")
    assert names.count("classify") == 2  # one after syncs, one after the daily jobs


def test_nothing_due_means_no_sync_and_no_rebuild(
    sched: Scheduler, db: Connection, ctx: AppContext, spies: Spies
) -> None:
    make_item(
        db,
        "item-1",
        last_success_at="2026-10-15T14:00:00Z",
        last_attempt_at="2026-10-15T14:00:00Z",
        historical_complete=1,
        last_daily_at="2026-10-15",
    )
    sched.tick()
    assert spies.names() == ["recover"]


def test_daily_jobs_then_estimates_for_returned_investment_accounts(
    sched: Scheduler, db: Connection, monkeypatch: pytest.MonkeyPatch, spies: Spies
) -> None:
    make_item(db, "item-a", products=["investments"])
    make_item(db, "item-b", products=["investments"], institution_name="B")
    make_item(
        db, "item-c", products=["investments"], institution_name="C", last_daily_at="2026-10-15"
    )
    outcomes = {
        "item-a": DailyOutcome(investment_account_ids=["inv-a"]),
        "item-b": DailyOutcome(investment_account_ids=["inv-b", "inv-a"]),
    }
    monkeypatch.setattr(
        "bizviz.sync.scheduler.run_daily_job", spies.record("daily", lambda c, i: outcomes[i])
    )
    sched.tick()
    assert spies.of("daily") == ["item-a", "item-b"]
    names = spies.names()
    assert names.count("estimates") == 1
    estimates_args = spies.of("estimates")[0]
    assert estimates_args[1] == TODAY and list(estimates_args[3]) == ["inv-a", "inv-b"]
    assert names.index("estimates") > names.index("daily")
    assert names[-1] == "classify" or names.index("classify") > names.index("estimates")
    assert [r["ok"] for r in sync_log(db, "estimates")] == [1]


def test_daily_without_investments_skips_estimates_but_rebuilds_classification(
    sched: Scheduler, db: Connection, spies: Spies
) -> None:
    make_item(db, "item-a", products=["investments"])
    sched.tick()
    assert "estimates" not in spies.names()
    assert "classify" in spies.names()


def test_token_recovery_runs_every_ten_minutes(
    sched: Scheduler, ctx: AppContext, spies: Spies
) -> None:
    sched.tick()
    assert len(spies.of("recover")) == 1
    advance(ctx, timedelta(minutes=5))
    sched.tick()
    assert len(spies.of("recover")) == 1
    advance(ctx, timedelta(minutes=5))
    sched.tick()
    assert len(spies.of("recover")) == 2


def test_prices_fetched_when_needed_then_estimates_rebuilt(
    sched: Scheduler, ctx: AppContext, db: Connection
) -> None:
    source = FakePriceSource()
    start = TODAY - timedelta(days=760)
    source.closes["VTI"] = [(start + timedelta(days=i), 100.0 + i) for i in range(770)]
    ctx.price_source = source
    ctx.plaid = None
    setup_account(db, "inv-1", complete=True)
    sec(db, "sec-1", "VTI", type="etf")
    hold(db, "inv-1", "sec-1", 10, 100.0)

    sched.tick()
    assert len(source.download_calls) == 1
    assert source.download_calls[0][0] == ["VTI"]
    assert [r["ok"] for r in sync_log(db, "prices")] == [1]
    assert [r["ok"] for r in sync_log(db, "estimates")] == [1]
    assert db.execute("SELECT count(*) FROM prices").fetchone()[0] > 0

    sched.tick()
    assert len(source.download_calls) == 1
    assert len(sync_log(db, "prices")) == 1


def test_price_failure_is_logged_and_not_retried_every_tick(
    sched: Scheduler, ctx: AppContext, db: Connection
) -> None:
    source = FakePriceSource()
    source.download_failures = 99
    ctx.price_source = source
    ctx.plaid = None
    setup_account(db, "inv-1", complete=True)
    sec(db, "sec-1", "VTI", type="etf")
    hold(db, "inv-1", "sec-1", 10, 100.0)
    slept: list[float] = []
    sched._sleep = slept.append
    sched.tick()
    rows = sync_log(db, "prices")
    assert [(r["ok"], r["error_code"]) for r in rows] == [(0, "PRICE_FETCH_FAILED")]
    assert slept == [2, 8, 30]
    sched.tick()
    assert len(sync_log(db, "prices")) == 1


def test_no_price_source_skips_prices(sched: Scheduler, ctx: AppContext, db: Connection) -> None:
    ctx.price_source = None
    ctx.plaid = None
    setup_account(db, "inv-1", complete=True)
    sec(db, "sec-1", "VTI", type="etf")
    hold(db, "inv-1", "sec-1", 10, 100.0)
    sched.tick()
    assert sync_log(db, "prices") == []


def test_daily_backup_once_per_local_date(
    sched: Scheduler, ctx: AppContext, db: Connection
) -> None:
    ctx.plaid = None
    sched.tick()
    sched.tick()
    files = sorted(p.name for p in ctx.cfg.backups_dir.glob("bizviz-2026-*.db"))
    assert files == ["bizviz-2026-10-15.db"]
    assert [r["ok"] for r in sync_log(db, "backup")] == [1]
    # 15:00Z is 11:00 in New York; +10h is 21:00 local, still the same date.
    advance(ctx, timedelta(hours=10))
    sched.tick()
    assert len(list(ctx.cfg.backups_dir.glob("bizviz-2026-*.db"))) == 1
    advance(ctx, timedelta(hours=10))
    sched.tick()
    assert sorted(p.name for p in ctx.cfg.backups_dir.glob("bizviz-2026-*.db")) == [
        "bizviz-2026-10-15.db",
        "bizviz-2026-10-16.db",
    ]
    assert len(sync_log(db, "backup")) == 2


def test_backup_failure_is_logged_and_retried_after_ten_minutes(
    sched: Scheduler, ctx: AppContext, db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx.plaid = None
    calls: list[int] = []

    def boom(*a: Any, **k: Any) -> None:
        calls.append(1)
        raise OSError("disk full")

    monkeypatch.setattr("bizviz.sync.scheduler.daily_backup_if_needed", boom)
    sched.tick()
    sched.tick()
    assert len(calls) == 1
    assert [(r["ok"], r["error_code"]) for r in sync_log(db, "backup")] == [(0, "OSError")]
    advance(ctx, timedelta(minutes=11))
    sched.tick()
    assert len(calls) == 2


def test_leaf_renewal_within_thirty_days_triggers_restart(
    ctx: AppContext, db: Connection, restarts: list[str]
) -> None:
    ctx.plaid = None
    certs.issue_leaf(ctx.cfg, ctx.platform.secrets, FIXED_NOW - timedelta(days=340))
    assert leaf_needs_renewal(ctx.cfg, FIXED_NOW)
    sched = Scheduler(ctx, on_restart=lambda: restarts.append("restart"))
    sched.tick()
    assert restarts == ["restart"]
    assert not leaf_needs_renewal(ctx.cfg, FIXED_NOW)
    assert [r["ok"] for r in sync_log(db, "cert")] == [1]
    sched.tick()
    assert restarts == ["restart"]


def test_healthy_leaf_is_not_reissued(
    sched: Scheduler, ctx: AppContext, restarts: list[str]
) -> None:
    ctx.plaid = None
    before = certs.leaf_path(ctx.cfg).read_bytes()
    sched.tick()
    assert certs.leaf_path(ctx.cfg).read_bytes() == before
    assert restarts == []


def test_hostname_mismatch_never_reissues_and_warns_once(
    ctx: AppContext, restarts: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    ctx.plaid = None
    ctx.cfg = replace_config(ctx.cfg, hostname="pinned.local")
    certs.issue_leaf(ctx.cfg, ctx.platform.secrets, FIXED_NOW)
    before = certs.leaf_path(ctx.cfg).read_bytes()
    sched = Scheduler(ctx, on_restart=lambda: restarts.append("restart"))
    with caplog.at_level(logging.WARNING, logger="bizviz.sync.scheduler"):
        sched.tick()
        sched.tick()
    assert certs.leaf_path(ctx.cfg).read_bytes() == before
    assert restarts == []
    assert sum("differs from the pinned hostname" in r.message for r in caplog.records) == 1


def test_request_sync_item_forces_only_that_item_on_next_tick(
    sched: Scheduler, db: Connection, spies: Spies
) -> None:
    recent = dict(
        last_success_at="2026-10-15T14:59:00Z",
        last_attempt_at="2026-10-15T14:59:00Z",
        historical_complete=1,
        last_daily_at="2026-10-15",
    )
    make_item(db, "item-1", **recent)
    make_item(db, "item-2", institution_name="Two", **recent)
    sched.tick()
    assert spies.of("forced") == [] and spies.of("sync") == []
    sched.request_sync("item-2")
    assert sched.is_busy
    sched.tick()
    assert spies.of("forced") == ["item-2"]
    assert not sched.is_busy
    sched.tick()
    assert spies.of("forced") == ["item-2"]


def test_request_sync_none_forces_every_non_removed_item(
    sched: Scheduler, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    recent = dict(
        last_success_at="2026-10-15T14:59:00Z",
        last_attempt_at="2026-10-15T14:59:00Z",
        historical_complete=1,
        last_daily_at="2026-10-15",
    )
    make_item(db, "item-1", **recent)
    make_item(db, "item-2", institution_name="Two", **recent)
    make_item(db, "item-3", institution_name="Gone", removed_at="2026-10-01T00:00:00Z", **recent)
    fake_plaid.set_default("item_get", item_get(products=["transactions"]))
    fake_plaid.set_default("accounts_get", {"accounts": [plaid_account("acc-1")]})
    fake_plaid.set_default("transactions_sync", sync_page(cursor="cur"))
    sched.request_sync(None)
    sched.tick()
    tokens = {c[1][0] for c in fake_plaid.calls}
    assert tokens == {"access-item-1", "access-item-2"}
    forced = [c[1][0] for c in fake_plaid.calls if c[0] == "accounts_get"]
    assert forced == ["access-item-1", "access-item-2"]
    syncs = [c for c in fake_plaid.calls if c[0] == "transactions_sync"]
    assert {c[1][0] for c in syncs} == {"access-item-1", "access-item-2"}
    assert len(syncs) == 2  # the due-items pass does not sync forced items again


def test_items_with_unknown_products_are_force_synced_with_spacing(
    sched: Scheduler, ctx: AppContext, db: Connection, spies: Spies
) -> None:
    make_item(db, "item-1", products=[], last_daily_at="2026-10-15")
    sched.tick()
    assert spies.of("forced") == ["item-1"]
    with_attempt = "2026-10-15T14:55:00Z"
    db.execute("UPDATE items SET last_attempt_at = ? WHERE item_id = 'item-1'", (with_attempt,))
    sched.tick()
    assert spies.of("forced") == ["item-1"]
    advance(ctx, timedelta(minutes=11))
    sched.tick()
    assert spies.of("forced") == ["item-1", "item-1"]


def test_without_plaid_no_plaid_steps_run(
    sched: Scheduler, ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient, spies: Spies
) -> None:
    ctx.plaid = None
    make_item(db, "item-1")
    make_item(db, "item-2", products=[])
    sched.request_sync(None)
    sched.tick()
    assert spies.names() == []
    assert fake_plaid.calls == []
    assert len(sync_log(db, "backup")) == 1


def test_forcing_an_unknown_item_does_not_break_the_tick(
    sched: Scheduler, ctx: AppContext, db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    sched.request_sync("ghost")
    sched.tick()
    assert len(sync_log(db, "backup")) == 1


def test_a_failing_step_does_not_stop_later_steps(
    sched: Scheduler, db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_item(db, "item-1")

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("sync exploded")

    monkeypatch.setattr("bizviz.sync.scheduler.sync_item_transactions", boom)
    monkeypatch.setattr("bizviz.sync.scheduler.due_daily_items", boom)
    sched.tick()
    assert len(sync_log(db, "backup")) == 1


def test_loop_wakes_on_request_sync_and_survives_tick_errors(ctx: AppContext) -> None:
    ctx.plaid = None
    sched = Scheduler(ctx, tick_seconds=60, on_restart=lambda: None)
    ticked = threading.Event()
    calls: list[int] = []

    def tick() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("first tick fails")
        ticked.set()

    sched.tick = tick  # type: ignore[method-assign]
    sched.start()
    try:
        sched.request_sync(None)
        assert _wait_for(lambda: len(calls) >= 1)
        sched.request_sync("x")
        assert ticked.wait(5)
    finally:
        sched.stop()
    assert len(calls) == 2


def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = threading.Event()
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return True
        deadline.wait(0.01)
    return False
