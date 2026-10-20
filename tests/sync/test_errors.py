import json

import pytest

from bizviz.db.connection import Connection
from bizviz.plaid_types import PlaidError, PlaidNetworkError
from bizviz.sync.errors import (
    add_unavailable_step,
    apply_item_error,
    classify_plaid_error,
    load_unavailable_steps,
)
from tests.factories import make_item
from tests.sync.helpers import data_version, item_row


@pytest.mark.parametrize(
    ("error_type", "code", "kind"),
    [
        ("ITEM_ERROR", "ITEM_LOGIN_REQUIRED", "needs_attention"),
        ("ITEM_ERROR", "INVALID_CREDENTIALS", "needs_attention"),
        ("ITEM_ERROR", "INVALID_MFA", "needs_attention"),
        ("ITEM_ERROR", "ITEM_LOCKED", "needs_attention"),
        ("ITEM_ERROR", "USER_SETUP_REQUIRED", "needs_attention"),
        ("ITEM_ERROR", "INSUFFICIENT_CREDENTIALS", "needs_attention"),
        ("ITEM_ERROR", "ACCESS_NOT_GRANTED", "needs_attention"),
        ("ITEM_ERROR", "NO_ACCOUNTS", "needs_attention"),
        ("ITEM_ERROR", "INSTITUTION_DOWN", "transient"),
        ("ITEM_ERROR", "INSTITUTION_NOT_RESPONDING", "transient"),
        ("ITEM_ERROR", "INSTITUTION_NOT_AVAILABLE", "transient"),
        ("ITEM_ERROR", "PLANNED_MAINTENANCE", "transient"),
        ("INVESTMENTS_ERROR", "PRODUCT_NOT_READY", "transient"),
        ("RATE_LIMIT_EXCEEDED", "TRANSACTIONS_LIMIT", "transient"),
        ("API_ERROR", "INTERNAL_SERVER_ERROR", "transient"),
        ("API_ERROR", "SOMETHING_ELSE", "transient"),
        ("TRANSACTIONS_ERROR", "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION", "transient"),
        ("ITEM_ERROR", "PRODUCTS_NOT_SUPPORTED", "unavailable"),
        ("ITEM_ERROR", "PRODUCT_NOT_ENABLED", "unavailable"),
        ("INVESTMENTS_ERROR", "NO_INVESTMENT_ACCOUNTS", "unavailable"),
        ("INVESTMENTS_ERROR", "NO_INVESTMENT_AUTH_ACCESS", "unavailable"),
        ("INVALID_REQUEST", "INVALID_PRODUCT", "unavailable"),
        ("ITEM_ERROR", "SOMETHING_NEW", "error"),
        ("INVALID_INPUT", "INVALID_FIELD", "error"),
    ],
)
def test_classification(error_type: str, code: str, kind: str) -> None:
    assert classify_plaid_error(PlaidError(error_type, code)) == kind


def test_network_error_is_transient() -> None:
    assert classify_plaid_error(PlaidNetworkError("timeout")) == "transient"


def test_apply_item_error_sets_status_for_needs_attention_and_error(db: Connection) -> None:
    make_item(db, "a")
    make_item(db, "b")
    before = data_version(db)
    apply_item_error(db, "a", "needs_attention", "ITEM_LOGIN_REQUIRED")
    apply_item_error(db, "b", "error", "SOMETHING_NEW")
    assert item_row(db, "a")["status"] == "needs_attention"
    assert item_row(db, "a")["error_code"] == "ITEM_LOGIN_REQUIRED"
    assert item_row(db, "b")["status"] == "error"
    assert data_version(db) == before + 2


def test_apply_item_error_is_idempotent_and_ignores_other_kinds(db: Connection) -> None:
    make_item(db)
    apply_item_error(db, "item-1", "needs_attention", "ITEM_LOGIN_REQUIRED")
    version = data_version(db)
    apply_item_error(db, "item-1", "needs_attention", "ITEM_LOGIN_REQUIRED")
    apply_item_error(db, "item-1", "transient", "INSTITUTION_DOWN")
    apply_item_error(db, "item-1", "unavailable", "PRODUCTS_NOT_SUPPORTED")
    assert data_version(db) == version
    assert item_row(db)["status"] == "needs_attention"
    assert item_row(db)["error_code"] == "ITEM_LOGIN_REQUIRED"


def test_add_unavailable_step_appends_once(db: Connection) -> None:
    make_item(db)
    add_unavailable_step(db, "item-1", "holdings")
    add_unavailable_step(db, "item-1", "holdings")
    add_unavailable_step(db, "item-1", "investment_transactions")
    assert load_unavailable_steps(db, "item-1") == ["holdings", "investment_transactions"]
    assert json.loads(item_row(db)["unavailable_steps"]) == [
        "holdings",
        "investment_transactions",
    ]
