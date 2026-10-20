import json
import logging
from collections.abc import Callable
from datetime import date, datetime
from typing import Any, Literal

import plaid
import urllib3.exceptions
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
from plaid.model.investments_transactions_get_request import InvestmentsTransactionsGetRequest
from plaid.model.investments_transactions_get_request_options import (
    InvestmentsTransactionsGetRequestOptions,
)
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_update import LinkTokenCreateRequestUpdate
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.link_token_transactions import LinkTokenTransactions
from plaid.model.products import Products
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from bizviz.config import KC_PLAID_CLIENT_ID, KC_PLAID_SECRET, AppConfig
from bizviz.plaid_types import LinkMode, PlaidError, PlaidNetworkError
from bizviz.platform.base import SecretStore

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 60
DAYS_REQUESTED = 730

_MODE_PRODUCTS: dict[str, tuple[list[str], list[str]]] = {
    "bank": (["transactions"], ["investments", "liabilities"]),
    "investment": (["investments"], ["transactions", "liabilities"]),
    "loan": (["liabilities"], ["transactions"]),
}


def normalize(value: Any) -> Any:
    """Recursively convert plaid-python output to JSON-shaped data (ISO date strings)."""
    if isinstance(value, dict):
        return {str(k): normalize(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [normalize(v) for v in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if hasattr(value, "to_dict"):
        return normalize(value.to_dict())
    if not isinstance(value, str | int | float | bool | None) and hasattr(value, "value"):
        return normalize(value.value)
    return value


def translate_api_exception(exc: plaid.ApiException) -> PlaidError:
    body: Any = None
    raw = getattr(exc, "body", None)
    if raw:
        try:
            body = json.loads(raw)
        except (ValueError, TypeError):
            body = None
    status = getattr(exc, "status", None)
    if isinstance(body, dict) and body.get("error_code"):
        return PlaidError(
            str(body.get("error_type") or "API_ERROR"),
            str(body["error_code"]),
            request_id=body.get("request_id"),
            http_status=status,
            display_message=body.get("display_message"),
        )
    if isinstance(status, int) and status >= 500:
        return PlaidError("API_ERROR", "INTERNAL_SERVER_ERROR", http_status=status, request_id=None)
    return PlaidError("INVALID_REQUEST", "UNKNOWN", http_status=status)


class RealPlaidClient:
    def __init__(self, env: Literal["production", "sandbox"], client_id: str, secret: str) -> None:
        host = plaid.Environment.Production if env == "production" else plaid.Environment.Sandbox
        configuration = plaid.Configuration(
            host=host, api_key={"clientId": client_id, "secret": secret}
        )
        self._api = plaid_api.PlaidApi(plaid.ApiClient(configuration))

    def _call(self, method: Callable[..., Any], request: Any) -> dict[str, Any]:
        try:
            response = method(request, _request_timeout=REQUEST_TIMEOUT_SECONDS)
        except plaid.ApiException as exc:
            raise translate_api_exception(exc) from None
        except (urllib3.exceptions.HTTPError, OSError):
            raise PlaidNetworkError("network error talking to Plaid") from None
        result = normalize(response.to_dict())
        assert isinstance(result, dict)
        return result

    def create_link_token(self, *, mode: LinkMode, access_token: str | None = None) -> str:
        fields: dict[str, Any] = {
            "client_name": "BizViz",
            "language": "en",
            "country_codes": [CountryCode("US")],
            "user": LinkTokenCreateRequestUser(client_user_id="local-user"),
        }
        if mode == "update":
            if not access_token:
                raise ValueError("access_token is required for update mode")
            fields["access_token"] = access_token
            fields["update"] = LinkTokenCreateRequestUpdate(account_selection_enabled=True)
        else:
            products, optional = _MODE_PRODUCTS[mode]
            fields["products"] = [Products(p) for p in products]
            fields["optional_products"] = [Products(p) for p in optional]
            fields["transactions"] = LinkTokenTransactions(days_requested=DAYS_REQUESTED)
        result = self._call(self._api.link_token_create, LinkTokenCreateRequest(**fields))
        return str(result["link_token"])

    def exchange_public_token(self, public_token: str) -> dict[str, Any]:
        request = ItemPublicTokenExchangeRequest(public_token=public_token)
        result = self._call(self._api.item_public_token_exchange, request)
        return {"access_token": result["access_token"], "item_id": result["item_id"]}

    def transactions_sync(self, access_token: str, cursor: str | None) -> dict[str, Any]:
        fields: dict[str, Any] = {"access_token": access_token, "count": 500}
        if cursor:
            fields["cursor"] = cursor
        return self._call(self._api.transactions_sync, TransactionsSyncRequest(**fields))

    def item_get(self, access_token: str) -> dict[str, Any]:
        return self._call(self._api.item_get, ItemGetRequest(access_token=access_token))

    def accounts_get(self, access_token: str) -> dict[str, Any]:
        return self._call(self._api.accounts_get, AccountsGetRequest(access_token=access_token))

    def investments_holdings_get(self, access_token: str) -> dict[str, Any]:
        request = InvestmentsHoldingsGetRequest(access_token=access_token)
        return self._call(self._api.investments_holdings_get, request)

    def investments_transactions_get(
        self,
        access_token: str,
        start_date: date,
        end_date: date,
        offset: int,
        count: int = 500,
    ) -> dict[str, Any]:
        request = InvestmentsTransactionsGetRequest(
            access_token=access_token,
            start_date=start_date,
            end_date=end_date,
            options=InvestmentsTransactionsGetRequestOptions(count=count, offset=offset),
        )
        return self._call(self._api.investments_transactions_get, request)

    def item_remove(self, access_token: str) -> None:
        self._call(self._api.item_remove, ItemRemoveRequest(access_token=access_token))


def build_plaid_client(cfg: AppConfig, secrets: SecretStore) -> RealPlaidClient | None:
    client_id = secrets.get(KC_PLAID_CLIENT_ID)
    secret = secrets.get(KC_PLAID_SECRET)
    if not client_id or not secret:
        return None
    return RealPlaidClient(cfg.plaid_env, client_id, secret)
