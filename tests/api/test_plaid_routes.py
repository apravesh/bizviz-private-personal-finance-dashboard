from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from bizviz.db.connection import Connection, set_setting, write_unit
from bizviz.plaid_fake import FakePlaidClient
from bizviz.plaid_types import PlaidError, PlaidNetworkError
from bizviz.service import Runtime
from bizviz.sync import linking
from tests.factories import make_account, make_item
from tests.sync.helpers import item_get, plaid_account


@pytest.fixture(autouse=True)
def clean_exchange_cache() -> Iterator[None]:
    linking._cache.clear()
    linking._locks.clear()
    yield
    linking._cache.clear()
    linking._locks.clear()


EXCHANGE = {
    "public_token": "pub-1",
    "institution_id": "ins_9",
    "institution_name": "Nine Bank",
    "mode": "bank",
}


def test_plaid_routes_return_503_without_client(client: TestClient, runtime: Runtime) -> None:
    runtime.ctx.plaid = None
    calls = [
        client.post("/api/plaid/link-token", json={"mode": "bank"}),
        client.post("/api/plaid/exchange", json=EXCHANGE),
        client.post("/api/plaid/items/x/repaired"),
        client.delete("/api/plaid/items/x"),
    ]
    for resp in calls:
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "plaid_not_configured"


def test_link_token(client: TestClient, fake_plaid: FakePlaidClient) -> None:
    fake_plaid.queue("create_link_token", "link-sandbox-123")
    resp = client.post("/api/plaid/link-token", json={"mode": "investment"})
    assert resp.status_code == 200
    assert resp.json() == {"link_token": "link-sandbox-123"}
    assert fake_plaid.calls[0][2] == {"mode": "investment"}


def test_link_token_update_mode_uses_stored_access_token(
    client: TestClient, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db, "item-1")
    fake_plaid.queue("create_link_token", "link-update")
    resp = client.post("/api/plaid/link-token", json={"mode": "update", "item_id": "item-1"})
    assert resp.json() == {"link_token": "link-update"}
    assert fake_plaid.calls[0][2] == {"mode": "update", "access_token": "access-item-1"}
    missing = client.post("/api/plaid/link-token", json={"mode": "update", "item_id": "nope"})
    assert missing.status_code == 404


def test_link_token_rejects_unknown_mode(client: TestClient) -> None:
    resp = client.post("/api/plaid/link-token", json={"mode": "crypto"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid"


def test_link_token_slot_limit_is_409(
    client: TestClient, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    with write_unit(db):
        set_setting(db, "plaid_items_created_count", "10")
    resp = client.post("/api/plaid/link-token", json={"mode": "bank"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "slot_limit"
    assert fake_plaid.calls == []


def test_exchange_returns_result(client: TestClient, fake_plaid: FakePlaidClient) -> None:
    fake_plaid.queue("exchange_public_token", {"item_id": "item-new", "access_token": "tok-new"})
    fake_plaid.queue("item_get", item_get(products=["transactions"], billed=["transactions"]))
    fake_plaid.queue("accounts_get", {"accounts": [plaid_account("a1")]})
    resp = client.post("/api/plaid/exchange", json=EXCHANGE)
    assert resp.status_code == 200
    assert resp.json() == {"item_id": "item-new", "duplicate": False, "pending": False}
    data = client.get("/api/data").json()
    assert [i["item_id"] for i in data["status"]["items"]] == ["item-new"]


def test_exchange_followup_failure_is_pending(
    client: TestClient, fake_plaid: FakePlaidClient
) -> None:
    fake_plaid.queue("exchange_public_token", {"item_id": "item-new", "access_token": "tok-new"})
    fake_plaid.queue("item_get", PlaidNetworkError())
    resp = client.post("/api/plaid/exchange", json=EXCHANGE)
    assert resp.status_code == 200
    assert resp.json() == {"item_id": "item-new", "duplicate": False, "pending": True}


def test_exchange_plaid_error_is_502_with_plaid_code(
    client: TestClient, fake_plaid: FakePlaidClient
) -> None:
    fake_plaid.queue(
        "exchange_public_token", PlaidError("INVALID_INPUT", "INVALID_PUBLIC_TOKEN", request_id="r")
    )
    resp = client.post("/api/plaid/exchange", json=EXCHANGE)
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "INVALID_PUBLIC_TOKEN"
    assert "request_id" not in resp.json()["error"]


def test_exchange_network_error_is_502(client: TestClient, fake_plaid: FakePlaidClient) -> None:
    fake_plaid.queue("exchange_public_token", PlaidNetworkError())
    resp = client.post("/api/plaid/exchange", json=EXCHANGE)
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "network_error"


def test_exchange_invalid_mode_is_400(client: TestClient, fake_plaid: FakePlaidClient) -> None:
    resp = client.post("/api/plaid/exchange", json={**EXCHANGE, "mode": "update"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_mode"
    assert fake_plaid.calls == []


def test_repaired_and_disconnect(
    client: TestClient, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db, "item-1", status="needs_attention", error_code="ITEM_LOGIN_REQUIRED")
    make_account(db, "acc-1", "item-1")
    assert client.post("/api/plaid/items/item-1/repaired").status_code == 204
    assert client.get("/api/data").json()["status"]["items"][0]["status"] == "ok"
    assert client.post("/api/plaid/items/nope/repaired").status_code == 404

    fake_plaid.queue("item_remove", None)
    assert client.delete("/api/plaid/items/item-1").status_code == 204
    assert fake_plaid.calls[-1][0] == "item_remove"
    assert client.get("/api/data").json()["status"]["items"][0]["removed"] is True
    assert client.delete("/api/plaid/items/nope").status_code == 404


def test_disconnect_plaid_error_is_502(
    client: TestClient, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db, "item-1")
    fake_plaid.queue("item_remove", PlaidError("API_ERROR", "INTERNAL_SERVER_ERROR"))
    resp = client.delete("/api/plaid/items/item-1")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
