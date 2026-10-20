from datetime import date

import pytest

from bizviz.plaid_fake import FakePlaidClient
from bizviz.plaid_types import PlaidError


def test_fifo_per_method() -> None:
    fake = FakePlaidClient()
    fake.queue("item_get", {"n": 1})
    fake.queue("item_get", {"n": 2})
    fake.queue("accounts_get", {"n": 3})
    assert fake.item_get("t")["n"] == 1
    assert fake.accounts_get("t")["n"] == 3
    assert fake.item_get("t")["n"] == 2
    assert fake.count("item_get") == 2


def test_queued_exception_is_raised() -> None:
    fake = FakePlaidClient()
    fake.queue("transactions_sync", PlaidError("ITEM_ERROR", "ITEM_LOGIN_REQUIRED"))
    with pytest.raises(PlaidError) as info:
        fake.transactions_sync("tok", None)
    assert str(info.value) == "ITEM_ERROR/ITEM_LOGIN_REQUIRED"
    assert fake.count("transactions_sync") == 1


def test_calls_record_positional_and_keyword_arguments() -> None:
    fake = FakePlaidClient()
    fake.set_default("create_link_token", "link-abc")
    fake.set_default("investments_transactions_get", {"investment_transactions": []})
    fake.set_default("item_remove", None)
    assert fake.create_link_token(mode="update", access_token="tok") == "link-abc"
    fake.investments_transactions_get("tok", date(2026, 1, 1), date(2026, 2, 1), 0, count=100)
    fake.item_remove("tok")
    assert fake.calls[0] == ("create_link_token", (), {"mode": "update", "access_token": "tok"})
    assert fake.calls[1] == (
        "investments_transactions_get",
        ("tok", date(2026, 1, 1), date(2026, 2, 1), 0),
        {"count": 100},
    )
    assert fake.calls[2] == ("item_remove", ("tok",), {})


def test_default_used_when_queue_empty_and_queue_takes_precedence() -> None:
    fake = FakePlaidClient()
    fake.set_default("item_get", {"d": 1})
    fake.queue("item_get", {"q": 1})
    assert fake.item_get("t") == {"q": 1}
    assert fake.item_get("t") == {"d": 1}
    assert fake.item_get("t") == {"d": 1}


def test_results_are_deep_copied() -> None:
    fake = FakePlaidClient()
    fake.set_default("accounts_get", {"accounts": [{"id": "a"}]})
    first = fake.accounts_get("t")
    first["accounts"].append({"id": "b"})
    assert fake.accounts_get("t") == {"accounts": [{"id": "a"}]}


def test_unexpected_call_fails_loudly() -> None:
    fake = FakePlaidClient()
    with pytest.raises(AssertionError, match="unexpected call exchange_public_token"):
        fake.exchange_public_token("public")
    assert fake.count("exchange_public_token") == 1


def test_plaid_error_str_never_includes_message() -> None:
    err = PlaidError("API_ERROR", "INTERNAL_SERVER_ERROR", display_message="secret body")
    assert "secret" not in str(err)
    assert err.display_message == "secret body"
