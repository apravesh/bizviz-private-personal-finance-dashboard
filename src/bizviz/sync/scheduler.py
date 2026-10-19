"""Background scheduler: one thread, one tick per minute, one step at a time.

Privacy rule: log only IDs, counts and error types.
"""

import logging
import os
import signal
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta

from bizviz.analytics.classify import rebuild_classification
from bizviz.analytics.investment_history import rebuild_estimates
from bizviz.backup import daily_backup_if_needed
from bizviz.certs import issue_leaf, leaf_needs_renewal
from bizviz.context import AppContext
from bizviz.prices import refresh_prices, symbols_needing_prices
from bizviz.sync import synclog
from bizviz.sync.daily import forced_sync, run_daily_job
from bizviz.sync.due import (
    due_daily_items,
    due_transaction_items,
    items_needing_forced_sync,
)
from bizviz.sync.transactions import sync_item_transactions
from bizviz.tokens import recover_tokens

log = logging.getLogger(__name__)

TOKEN_RECOVERY_INTERVAL = timedelta(minutes=10)
BACKUP_RETRY_INTERVAL = timedelta(minutes=10)


def default_restart() -> None:
    # launchd's KeepAlive restarts the service; sys.exit from a thread would not stop the process.
    os.kill(os.getpid(), signal.SIGTERM)


class Scheduler:
    def __init__(
        self,
        ctx: AppContext,
        *,
        on_restart: Callable[[], None] = default_restart,
        tick_seconds: float = 60,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.ctx = ctx
        self._on_restart = on_restart
        self._tick_seconds = tick_seconds
        self._now = now or ctx.now
        self._sleep = sleep
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._forced: set[str] = set()
        self._force_all = False
        self._ticking = False
        self._last_recovery: datetime | None = None
        self._last_backup_failure: datetime | None = None
        self._hostname_warned = False

    def request_sync(self, item_id: str | None) -> None:
        with self._lock:
            if item_id is None:
                self._force_all = True
            else:
                self._forced.add(item_id)
        self._wake.set()

    @property
    def is_busy(self) -> bool:
        with self._lock:
            return self._ticking or self._force_all or bool(self._forced)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="bizviz-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=30)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self._tick_seconds)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.tick()
            except Exception as exc:
                log.error("scheduler tick failed error_type=%s", type(exc).__name__)

    def tick(self) -> None:
        with self._lock:
            self._ticking = True
        try:
            now = self._now()
            today = now.astimezone(self.ctx.tz()).date()
            if self.ctx.plaid is not None:
                self._step("sync", self._sync_step, now, today)
                self._step("daily", self._daily_step, now, today)
                self._step("recovery", self._recovery_step, now, today)
            self._step("prices", self._prices_step, now, today)
            self._step("backup", self._backup_step, now, today)
            self._step("cert", self._cert_step, now, today)
            self._step("hostname", self._hostname_step, now, today)
        finally:
            with self._lock:
                self._ticking = False

    def _step(
        self, name: str, fn: Callable[[datetime, date], None], now: datetime, today: date
    ) -> None:
        try:
            fn(now, today)
        except Exception as exc:
            log.error("scheduler step %s failed error_type=%s", name, type(exc).__name__)

    def _record(self, job: str, ok: bool, error_code: str | None = None) -> None:
        conn = self.ctx.connect()
        try:
            synclog.finish(conn, synclog.start(conn, job, None), ok, error_code)
        finally:
            conn.close()

    def _take_forced(self) -> tuple[set[str], bool]:
        with self._lock:
            forced, force_all = self._forced, self._force_all
            self._forced, self._force_all = set(), False
        return forced, force_all

    def _rebuild_classification(self, today: date) -> None:
        conn = self.ctx.connect()
        try:
            rebuild_classification(conn, today)
        finally:
            conn.close()

    def _rebuild_estimates(self, today: date, account_ids: list[str] | None) -> None:
        conn = self.ctx.connect()
        try:
            log_id = synclog.start(conn, "estimates", None)
            try:
                rebuild_estimates(conn, today, self.ctx.tz(), account_ids)
            except Exception as exc:
                synclog.finish(conn, log_id, False, type(exc).__name__)
                raise
            synclog.finish(conn, log_id, True, None)
        finally:
            conn.close()

    def _sync_step(self, now: datetime, today: date) -> None:
        ctx = self.ctx
        forced, force_all = self._take_forced()
        conn = ctx.connect()
        try:
            if force_all:
                rows = conn.execute("SELECT item_id FROM items WHERE removed_at IS NULL")
                forced |= {r[0] for r in rows.fetchall()}
            forced |= set(items_needing_forced_sync(conn, now))
            ran = False
            synced: set[str] = set()
            for item_id in sorted(forced):
                try:
                    forced_sync(ctx, item_id)
                    synced.add(item_id)
                    ran = True
                except Exception as exc:
                    log.error(
                        "forced sync failed item=%s error_type=%s", item_id, type(exc).__name__
                    )
            for item_id in due_transaction_items(conn, now, forced):
                if item_id in synced:
                    continue
                try:
                    sync_item_transactions(ctx, item_id)
                    ran = True
                except Exception as exc:
                    log.error("sync failed item=%s error_type=%s", item_id, type(exc).__name__)
            if ran:
                rebuild_classification(conn, today)
        finally:
            conn.close()

    def _daily_step(self, now: datetime, today: date) -> None:
        ctx = self.ctx
        conn = ctx.connect()
        try:
            due = due_daily_items(conn, now, today)
        finally:
            conn.close()
        investment_ids: list[str] = []
        ran = False
        for item_id in due:
            try:
                outcome = run_daily_job(ctx, item_id)
            except Exception as exc:
                log.error("daily job failed item=%s error_type=%s", item_id, type(exc).__name__)
                continue
            ran = True
            investment_ids.extend(
                a for a in outcome.investment_account_ids if a not in investment_ids
            )
        if investment_ids:
            self._rebuild_estimates(today, investment_ids)
        if ran:
            self._rebuild_classification(today)

    def _recovery_step(self, now: datetime, today: date) -> None:
        last = self._last_recovery
        if last is not None and now - last < TOKEN_RECOVERY_INTERVAL:
            return
        self._last_recovery = now
        recover_tokens(self.ctx)

    def _prices_step(self, now: datetime, today: date) -> None:
        ctx = self.ctx
        if ctx.price_source is None:
            return
        conn = ctx.connect()
        try:
            if not symbols_needing_prices(conn, now):
                return
        finally:
            conn.close()
        try:
            outcome = refresh_prices(ctx, sleep=self._sleep)
        except Exception as exc:
            self._record("prices", False, type(exc).__name__)
            raise
        self._record(
            "prices", outcome.failed == 0, "PRICE_FETCH_FAILED" if outcome.failed else None
        )
        if outcome.fetched:
            self._rebuild_estimates(today, None)

    def _backup_step(self, now: datetime, today: date) -> None:
        last_failure = self._last_backup_failure
        if last_failure is not None and now - last_failure < BACKUP_RETRY_INTERVAL:
            return
        try:
            dest = daily_backup_if_needed(self.ctx, today)
        except Exception as exc:
            self._last_backup_failure = now
            self._record("backup", False, type(exc).__name__)
            raise
        self._last_backup_failure = None
        if dest is not None:
            self._record("backup", True)

    def _cert_step(self, now: datetime, today: date) -> None:
        ctx = self.ctx
        if not leaf_needs_renewal(ctx.cfg, now):
            return
        try:
            issue_leaf(ctx.cfg, ctx.platform.secrets, now)
        except Exception as exc:
            self._record("cert", False, type(exc).__name__)
            raise
        self._record("cert", True)
        log.info("leaf certificate renewed; restarting to load it")
        self._on_restart()

    def _hostname_step(self, now: datetime, today: date) -> None:
        cfg = self.ctx.cfg
        if self._hostname_warned or not cfg.hostname:
            return
        current = self.ctx.platform.system.local_hostname()
        if current and current != cfg.hostname:
            self._hostname_warned = True
            log.warning("local hostname differs from the pinned hostname; not reissuing")
