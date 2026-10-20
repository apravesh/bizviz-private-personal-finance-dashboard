import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from bizviz.context import AppContext
from bizviz.db.connection import Connection, bump_data_version, write_unit
from bizviz.errors import NotFoundError
from bizviz.plaid_types import PlaidError, PlaidNetworkError
from bizviz.sync import synclog
from bizviz.sync.accounts import snapshot_balances, upsert_accounts
from bizviz.sync.errors import (
    apply_item_error,
    classify_plaid_error,
    error_code_of,
    log_plaid_error,
    require_plaid,
)
from bizviz.sync.investments import capture_holdings, fetch_investment_transactions
from bizviz.sync.transactions import (
    record_failure,
    store_item_info,
    sync_item_transactions,
    ts,
)

log = logging.getLogger(__name__)

STEP_OK_STATES = frozenset({"ok", "skipped", "unavailable"})


@dataclass
class DailyOutcome:
    steps: dict[str, str] = field(default_factory=dict)
    investment_account_ids: list[str] = field(default_factory=list)
    complete: bool = False


def _load_item(conn: Connection, item_id: str) -> dict[str, object] | None:
    row = conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
    return None if row is None else dict(row)


def _refresh_balances(
    ctx: AppContext, conn: Connection, item_id: str, investment_dates: dict[str, date]
) -> None:
    token = conn.execute("SELECT access_token FROM items WHERE item_id = ?", (item_id,)).fetchone()[
        "access_token"
    ]
    accounts = require_plaid(ctx).accounts_get(token).get("accounts") or []
    today = ctx.today()
    with write_unit(conn):
        upsert_accounts(conn, item_id, accounts, mark_gone=True, today=today)
        snapshot_balances(conn, accounts, local_date=today, investment_dates=investment_dates)
        bump_data_version(conn)


def _investment_account_ids(conn: Connection, item_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT account_id FROM accounts WHERE item_id = ? AND type = 'investment' "
        "ORDER BY account_id",
        (item_id,),
    ).fetchall()
    return [r[0] for r in rows]


def run_daily_job(ctx: AppContext, item_id: str) -> DailyOutcome:
    conn = ctx.connect()
    try:
        return _run_daily(ctx, conn, item_id)
    finally:
        conn.close()


def _run_daily(ctx: AppContext, conn: Connection, item_id: str) -> DailyOutcome:
    item = _load_item(conn, item_id)
    if item is None or item["removed_at"] is not None:
        return DailyOutcome()
    today = ctx.today()
    log_id = synclog.start(conn, "daily", item_id)
    with write_unit(conn):
        conn.execute(
            "UPDATE items SET last_daily_attempt_at = ? WHERE item_id = ?", (ts(ctx), item_id)
        )

    steps: dict[str, str] = {}
    failures: list[tuple[str, bool]] = []
    investment_dates: dict[str, date] = {}

    def run_step(step: str, unavailable_name: str | None, fn: Callable[[], None]) -> None:
        try:
            fn()
            steps[step] = "ok"
        except (PlaidError, PlaidNetworkError) as exc:
            log_plaid_error(f"daily {step}", item_id, exc)
            code = error_code_of(exc)
            kind = classify_plaid_error(exc)
            if kind == "unavailable" and unavailable_name is not None:
                record_failure(conn, item_id, unavailable_name, exc)
                steps[step] = "unavailable"
                return
            if kind == "unavailable":
                kind = "error"
                apply_item_error(conn, item_id, "error", code)
            else:
                record_failure(conn, item_id, step, exc)
            steps[step] = "transient" if kind == "transient" else "failed"
            failures.append((code, kind == "transient"))
        except Exception as exc:
            log.error("daily %s crashed item=%s error_type=%s", step, item_id, type(exc).__name__)
            steps[step] = "failed"
            failures.append(("INTERNAL_ERROR", False))

    def has_investments() -> bool:
        current = _load_item(conn, item_id)
        assert current is not None
        return "investments" in json.loads(str(current["products"])) and bool(
            _investment_account_ids(conn, item_id)
        )

    def step_applies(step_name: str, done_column: str) -> bool:
        current = _load_item(conn, item_id)
        assert current is not None
        return (
            has_investments()
            and step_name not in json.loads(str(current["unavailable_steps"]))
            and current[done_column] != today.isoformat()
        )

    def balances() -> None:
        run_step("balances", None, lambda: _refresh_balances(ctx, conn, item_id, investment_dates))

    def holdings() -> None:
        if not step_applies("holdings", "last_holdings_at"):
            steps["holdings"] = "skipped"
            return
        run_step(
            "holdings",
            "holdings",
            lambda: investment_dates.update(capture_holdings(ctx, item_id, conn)),
        )

    def investment_transactions() -> None:
        if not step_applies("investment_transactions", "last_inv_tx_at"):
            steps["investment_transactions"] = "skipped"
            return
        run_step(
            "investment_transactions",
            "investment_transactions",
            lambda: fetch_investment_transactions(ctx, item_id, conn),
        )

    has_accounts = conn.execute(
        "SELECT 1 FROM accounts WHERE item_id = ? LIMIT 1", (item_id,)
    ).fetchone()
    if has_accounts:
        holdings()
        balances()
    else:
        balances()
        holdings()
    investment_transactions()

    complete = all(state in STEP_OK_STATES for state in steps.values())
    if complete:
        with write_unit(conn):
            conn.execute(
                "UPDATE items SET last_daily_at = ? WHERE item_id = ?",
                (today.isoformat(), item_id),
            )
        synclog.finish(conn, log_id, True, None)
    else:
        code = failures[0][0] if failures else None
        transient = bool(failures) and all(t for _, t in failures)
        synclog.finish(conn, log_id, False, code, transient=transient)
    log.info("daily job item=%s complete=%s steps=%s", item_id, complete, json.dumps(steps))
    return DailyOutcome(steps, _investment_account_ids(conn, item_id), complete)


def forced_sync(ctx: AppContext, item_id: str) -> None:
    """item_get + accounts_get + balance snapshot, then transactions sync when eligible."""
    conn = ctx.connect()
    try:
        item = _load_item(conn, item_id)
        if item is None:
            raise NotFoundError(item_id)
        if item["removed_at"] is not None:
            return
        plaid = require_plaid(ctx)
        log_id = synclog.start(conn, "transactions", item_id)
        with write_unit(conn):
            conn.execute(
                "UPDATE items SET last_attempt_at = ? WHERE item_id = ?", (ts(ctx), item_id)
            )
        token = str(item["access_token"])
        today = ctx.today()
        try:
            store_item_info(ctx, conn, item_id, plaid.item_get(token))
            accounts = plaid.accounts_get(token).get("accounts") or []
            with write_unit(conn):
                upsert_accounts(conn, item_id, accounts, mark_gone=True, today=today)
                snapshot_balances(conn, accounts, local_date=today, investment_dates={})
                bump_data_version(conn)
        except (PlaidError, PlaidNetworkError) as exc:
            log_plaid_error("forced sync", item_id, exc)
            kind = classify_plaid_error(exc)
            code = error_code_of(exc)
            if kind == "unavailable":
                kind = "error"
                apply_item_error(conn, item_id, "error", code)
            else:
                record_failure(conn, item_id, "transactions", exc)
            synclog.finish(conn, log_id, False, code, transient=kind == "transient")
            return
        except Exception:
            synclog.finish(conn, log_id, False, "INTERNAL_ERROR")
            raise
        synclog.finish(conn, log_id, True, None)
    finally:
        conn.close()
    sync_item_transactions(ctx, item_id)
