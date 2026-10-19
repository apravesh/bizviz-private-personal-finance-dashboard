import json
import logging
from typing import Literal

from bizviz.context import AppContext
from bizviz.db.connection import Connection, bump_data_version, write_unit
from bizviz.plaid_types import PlaidClient, PlaidError, PlaidNetworkError

log = logging.getLogger(__name__)

ErrorKind = Literal["needs_attention", "transient", "unavailable", "error"]

NEEDS_ATTENTION = frozenset(
    {
        "ITEM_LOGIN_REQUIRED",
        "INVALID_CREDENTIALS",
        "INVALID_MFA",
        "ITEM_LOCKED",
        "USER_SETUP_REQUIRED",
        "INSUFFICIENT_CREDENTIALS",
        "ACCESS_NOT_GRANTED",
        "NO_ACCOUNTS",
    }
)
TRANSIENT = frozenset(
    {
        "INSTITUTION_DOWN",
        "INSTITUTION_NOT_RESPONDING",
        "INSTITUTION_NOT_AVAILABLE",
        "RATE_LIMIT_EXCEEDED",
        "INTERNAL_SERVER_ERROR",
        "PLANNED_MAINTENANCE",
        "PRODUCT_NOT_READY",
        "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION",
    }
)
TRANSIENT_TYPES = frozenset({"RATE_LIMIT_EXCEEDED", "API_ERROR"})
UNAVAILABLE = frozenset(
    {
        "PRODUCTS_NOT_SUPPORTED",
        "PRODUCT_NOT_ENABLED",
        "NO_INVESTMENT_ACCOUNTS",
        "NO_INVESTMENT_AUTH_ACCESS",
        "INVALID_PRODUCT",
    }
)

NETWORK_ERROR_CODE = "NETWORK_ERROR"


def classify_plaid_error(err: PlaidError | PlaidNetworkError) -> ErrorKind:
    if isinstance(err, PlaidNetworkError):
        return "transient"
    code = err.error_code
    if code in NEEDS_ATTENTION:
        return "needs_attention"
    if code in TRANSIENT or err.error_type in TRANSIENT_TYPES:
        return "transient"
    if code in UNAVAILABLE:
        return "unavailable"
    return "error"


def error_code_of(err: PlaidError | PlaidNetworkError) -> str:
    return NETWORK_ERROR_CODE if isinstance(err, PlaidNetworkError) else err.error_code


def log_plaid_error(where: str, item_id: str, err: PlaidError | PlaidNetworkError) -> None:
    if isinstance(err, PlaidNetworkError):
        log.warning("%s failed item=%s error_code=%s", where, item_id, NETWORK_ERROR_CODE)
    else:
        log.warning(
            "%s failed item=%s error_type=%s error_code=%s request_id=%s",
            where,
            item_id,
            err.error_type,
            err.error_code,
            err.request_id,
        )


def require_plaid(ctx: AppContext) -> PlaidClient:
    if ctx.plaid is None:
        raise RuntimeError("Plaid client is not configured")
    return ctx.plaid


def apply_item_error(conn: Connection, item_id: str, kind: ErrorKind, error_code: str) -> None:
    """Persist an item-level error. Transient and unavailable kinds leave the item untouched."""
    if kind not in ("needs_attention", "error"):
        return
    with write_unit(conn):
        row = conn.execute(
            "SELECT status, error_code FROM items WHERE item_id = ?", (item_id,)
        ).fetchone()
        if row is None or (row["status"] == kind and row["error_code"] == error_code):
            return
        conn.execute(
            "UPDATE items SET status = ?, error_code = ? WHERE item_id = ?",
            (kind, error_code, item_id),
        )
        bump_data_version(conn)


def load_unavailable_steps(conn: Connection, item_id: str) -> list[str]:
    row = conn.execute(
        "SELECT unavailable_steps FROM items WHERE item_id = ?", (item_id,)
    ).fetchone()
    return [] if row is None else list(json.loads(row["unavailable_steps"]))


def add_unavailable_step(conn: Connection, item_id: str, step: str) -> None:
    with write_unit(conn):
        steps = load_unavailable_steps(conn, item_id)
        if step in steps:
            return
        steps.append(step)
        conn.execute(
            "UPDATE items SET unavailable_steps = ? WHERE item_id = ?",
            (json.dumps(steps), item_id),
        )
        bump_data_version(conn)
