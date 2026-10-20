import json
from datetime import date, datetime
from typing import Any

import plaid
import pytest
import urllib3.exceptions

from bizviz.config import KC_PLAID_CLIENT_ID, KC_PLAID_SECRET
from bizviz.context import AppContext
from bizviz.plaid_client import (
    DAYS_REQUESTED,
    REQUEST_TIMEOUT_SECONDS,
    RealPlaidClient,
    build_plaid_client,
    normalize,
    translate_api_exception,
)
from bizviz.plaid_types import PlaidError, PlaidNetworkError


class Resp:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        return self.data


class Recorder:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def __call__(self, request: Any, **kwargs: Any) -> Any:
        self.calls.append((request, kwargs))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


@pytest.fixture
def client() -> RealPlaidClient:
    return RealPlaidClient("sandbox", "cid", "sec")


def patch(client: RealPlaidClient, name: str, result: Any) -> Recorder:
    rec = Recorder(result)
    setattr(client._api, name, rec)
    return rec


@pytest.mark.parametrize(
    ("mode", "products", "optional"),
    [
        ("bank", ["transactions"], ["investments", "liabilities"]),
        ("investment", ["investments"], ["transactions", "liabilities"]),
        ("loan", ["liabilities"], ["transactions"]),
    ],
)
def test_create_link_token_products_per_mode(
    client: RealPlaidClient, mode: Any, products: list[str], optional: list[str]
) -> None:
    rec = patch(client, "link_token_create", Resp({"link_token": "link-123"}))
    assert client.create_link_token(mode=mode) == "link-123"
    ((request, kwargs),) = rec.calls
    assert [p.value for p in request.products] == products
    assert [p.value for p in request.optional_products] == optional
    assert request.transactions.days_requested == DAYS_REQUESTED == 730
    assert request.client_name == "BizViz"
    assert [c.value for c in request.country_codes] == ["US"]
    assert kwargs == {"_request_timeout": REQUEST_TIMEOUT_SECONDS}
    assert REQUEST_TIMEOUT_SECONDS == 60
    assert "access_token" not in request._data_store


def test_create_link_token_update_mode(client: RealPlaidClient) -> None:
    rec = patch(client, "link_token_create", Resp({"link_token": "upd"}))
    assert client.create_link_token(mode="update", access_token="access-1") == "upd"
    ((request, _),) = rec.calls
    assert request.access_token == "access-1"
    assert request.update.account_selection_enabled is True
    store = request._data_store
    assert "products" not in store
    assert "optional_products" not in store
    assert "transactions" not in store


def test_create_link_token_update_requires_access_token(client: RealPlaidClient) -> None:
    with pytest.raises(ValueError):
        client.create_link_token(mode="update")


def test_exchange_returns_only_item_and_token(client: RealPlaidClient) -> None:
    rec = patch(
        client,
        "item_public_token_exchange",
        Resp({"access_token": "a", "item_id": "i", "request_id": "r"}),
    )
    assert client.exchange_public_token("pub") == {"access_token": "a", "item_id": "i"}
    assert rec.calls[0][0].public_token == "pub"


def test_transactions_sync_cursor_handling(client: RealPlaidClient) -> None:
    rec = patch(client, "transactions_sync", Resp({"added": [], "next_cursor": "c"}))
    client.transactions_sync("tok", None)
    client.transactions_sync("tok", "cur-1")
    first, second = (c[0] for c in rec.calls)
    assert first.access_token == "tok"
    assert first.count == 500
    assert "cursor" not in first._data_store
    assert second.cursor == "cur-1"


def test_investments_transactions_request(client: RealPlaidClient) -> None:
    rec = patch(client, "investments_transactions_get", Resp({"total": 0}))
    client.investments_transactions_get("tok", date(2026, 1, 1), date(2026, 10, 1), 500)
    ((request, _),) = rec.calls
    assert request.start_date == date(2026, 1, 1)
    assert request.end_date == date(2026, 10, 1)
    assert (request.options.count, request.options.offset) == (500, 500)


@pytest.mark.parametrize(
    ("method", "api_name", "args"),
    [
        ("item_get", "item_get", ("tok",)),
        ("accounts_get", "accounts_get", ("tok",)),
        ("investments_holdings_get", "investments_holdings_get", ("tok",)),
        ("item_remove", "item_remove", ("tok",)),
    ],
)
def test_simple_calls_pass_access_token(
    client: RealPlaidClient, method: str, api_name: str, args: tuple[str, ...]
) -> None:
    rec = patch(client, api_name, Resp({"request_id": "r"}))
    getattr(client, method)(*args)
    assert rec.calls[0][0].access_token == "tok"
    assert rec.calls[0][1] == {"_request_timeout": 60}


def test_responses_are_normalized_to_json_shapes(client: RealPlaidClient) -> None:
    patch(
        client,
        "accounts_get",
        Resp({"accounts": [{"d": date(2026, 10, 1), "t": datetime(2026, 10, 1, 12, 0, 5)}]}),
    )
    assert client.accounts_get("tok") == {
        "accounts": [{"d": "2026-10-01", "t": "2026-10-01T12:00:05"}]
    }


def test_normalize_handles_enums_models_and_nesting() -> None:
    class Holder:
        value = "wrapped"

    class Model:
        def to_dict(self) -> dict[str, Any]:
            return {"day": date(2026, 2, 3)}

    assert normalize({"a": [date(2026, 1, 2), (1, 2)], "m": Model(), "h": Holder()}) == {
        "a": ["2026-01-02", [1, 2]],
        "m": {"day": "2026-02-03"},
        "h": "wrapped",
    }
    assert normalize({"x": None, "y": 1.5, "z": True, "s": "s"}) == {
        "x": None,
        "y": 1.5,
        "z": True,
        "s": "s",
    }


def api_exc(status: int, body: Any) -> plaid.ApiException:
    exc = plaid.ApiException(status=status, reason="r")
    exc.body = body if isinstance(body, str) or body is None else json.dumps(body)
    return exc


def test_api_exception_with_plaid_body_becomes_plaid_error(client: RealPlaidClient) -> None:
    body = {
        "error_type": "ITEM_ERROR",
        "error_code": "ITEM_LOGIN_REQUIRED",
        "error_message": "secret detail",
        "display_message": "Please log in",
        "request_id": "req-9",
    }
    patch(client, "item_get", api_exc(400, body))
    with pytest.raises(PlaidError) as info:
        client.item_get("tok")
    err = info.value
    assert (err.error_type, err.error_code) == ("ITEM_ERROR", "ITEM_LOGIN_REQUIRED")
    assert (err.request_id, err.http_status, err.display_message) == ("req-9", 400, "Please log in")
    assert "secret detail" not in str(err)
    assert err.__cause__ is None


def test_api_exception_without_body_5xx_and_4xx() -> None:
    err = translate_api_exception(api_exc(503, None))
    assert (err.error_type, err.error_code, err.http_status) == (
        "API_ERROR",
        "INTERNAL_SERVER_ERROR",
        503,
    )
    err = translate_api_exception(api_exc(400, "<html>not json</html>"))
    assert (err.error_type, err.error_code) == ("INVALID_REQUEST", "UNKNOWN")


@pytest.mark.parametrize(
    "exc",
    [
        urllib3.exceptions.MaxRetryError(None, "https://x"),  # type: ignore[arg-type]
        urllib3.exceptions.ReadTimeoutError(None, "https://x", "slow"),  # type: ignore[arg-type]
        ConnectionResetError("reset"),
        TimeoutError("t"),
    ],
)
def test_network_errors_become_plaid_network_error(client: RealPlaidClient, exc: Exception) -> None:
    patch(client, "accounts_get", exc)
    with pytest.raises(PlaidNetworkError) as info:
        client.accounts_get("tok")
    assert info.value.__cause__ is None


def test_build_plaid_client_requires_both_credentials(app_ctx: AppContext) -> None:
    secrets = app_ctx.platform.secrets
    assert build_plaid_client(app_ctx.cfg, secrets) is None
    secrets.set(KC_PLAID_CLIENT_ID, "cid")
    assert build_plaid_client(app_ctx.cfg, secrets) is None
    secrets.set(KC_PLAID_SECRET, "sec")
    assert isinstance(build_plaid_client(app_ctx.cfg, secrets), RealPlaidClient)
