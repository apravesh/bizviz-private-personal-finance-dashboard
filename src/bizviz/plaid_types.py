from datetime import date
from typing import Any, Literal, Protocol

LinkMode = Literal["bank", "investment", "loan", "update"]


class PlaidError(Exception):
    """A Plaid API error. `str()` never includes the message body."""

    def __init__(
        self,
        error_type: str,
        error_code: str,
        *,
        request_id: str | None = None,
        http_status: int | None = None,
        display_message: str | None = None,
    ) -> None:
        super().__init__(f"{error_type}/{error_code}")
        self.error_type = error_type
        self.error_code = error_code
        self.request_id = request_id
        self.http_status = http_status
        self.display_message = display_message

    def __str__(self) -> str:
        return f"{self.error_type}/{self.error_code}"


class PlaidNetworkError(Exception):
    """Timeout or connection failure talking to Plaid."""


class PlaidClient(Protocol):
    """Thin wrapper over Plaid. Methods return the response body as a plain dict with Plaid's
    snake_case field names (dates as 'YYYY-MM-DD' strings, datetimes as ISO strings)."""

    def create_link_token(self, *, mode: LinkMode, access_token: str | None = None) -> str:
        """Return the link_token. `access_token` is required for mode 'update'."""
        ...

    def exchange_public_token(self, public_token: str) -> dict[str, Any]:
        """Return {access_token, item_id}."""
        ...

    def transactions_sync(self, access_token: str, cursor: str | None) -> dict[str, Any]:
        """ONE page: {added, modified, removed, next_cursor, has_more, accounts,
        transactions_update_status}."""
        ...

    def item_get(self, access_token: str) -> dict[str, Any]:
        """Return {item: {item_id, institution_id, products, billed_products,
        available_products, consented_products, error, consent_expiration_time}}."""
        ...

    def accounts_get(self, access_token: str) -> dict[str, Any]:
        """Return {accounts, item}."""
        ...

    def investments_holdings_get(self, access_token: str) -> dict[str, Any]:
        """Return {accounts, holdings, securities, item}."""
        ...

    def investments_transactions_get(
        self,
        access_token: str,
        start_date: date,
        end_date: date,
        offset: int,
        count: int = 500,
    ) -> dict[str, Any]:
        """Return {accounts, investment_transactions, securities,
        total_investment_transactions, item}."""
        ...

    def item_remove(self, access_token: str) -> None: ...
