import copy
from collections import defaultdict, deque
from typing import Any

_Result = dict[str, Any] | str | None | BaseException


class FakePlaidClient:
    """Scripted PlaidClient: queue responses per method, then inspect `calls`.

    Each call is recorded as (method, args, kwargs) exactly as the caller passed them.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._queues: dict[str, deque[_Result]] = defaultdict(deque)
        self._defaults: dict[str, _Result] = {}

    def queue(self, method: str, result: _Result) -> None:
        self._queues[method].append(result)

    def set_default(self, method: str, result: _Result) -> None:
        self._defaults[method] = result

    def count(self, method: str) -> int:
        return sum(1 for call in self.calls if call[0] == method)

    def _call(self, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        self.calls.append((method, args, kwargs))
        queue = self._queues[method]
        if queue:
            result = queue.popleft()
        elif method in self._defaults:
            result = self._defaults[method]
        else:
            raise AssertionError(f"FakePlaidClient: unexpected call {method}")
        if isinstance(result, BaseException):
            raise result
        return copy.deepcopy(result)

    def create_link_token(self, *args: Any, **kwargs: Any) -> str:
        return str(self._call("create_link_token", args, kwargs))

    def exchange_public_token(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(self._call("exchange_public_token", args, kwargs))

    def transactions_sync(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(self._call("transactions_sync", args, kwargs))

    def item_get(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(self._call("item_get", args, kwargs))

    def accounts_get(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(self._call("accounts_get", args, kwargs))

    def investments_holdings_get(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(self._call("investments_holdings_get", args, kwargs))

    def investments_transactions_get(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(self._call("investments_transactions_get", args, kwargs))

    def item_remove(self, *args: Any, **kwargs: Any) -> None:
        self._call("item_remove", args, kwargs)
