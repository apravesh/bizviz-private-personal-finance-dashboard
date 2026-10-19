from datetime import date

from fastapi.testclient import TestClient

from bizviz.analytics.classify import rebuild_classification
from bizviz.db.connection import Connection
from tests.api.conftest import ClientFactory
from tests.factories import make_account, make_item, make_txn


def _seed(db: Connection) -> None:
    make_item(db, "item-1", last_success_at="2026-10-15T14:00:00Z")
    make_account(db, "acc-1", "item-1")
    make_txn(
        db, "t1", "acc-1", "2026-10-10", 12.5, name="Corner Cafe", pfc_primary="FOOD_AND_DRINK"
    )
    make_txn(db, "t2", "acc-1", "2026-10-11", 9.0, name="Pending Shop", pending=True)
    rebuild_classification(db, date(2026, 10, 15))


def _spending_category(db: Connection) -> str:
    row = db.execute(
        "SELECT category_id FROM categories WHERE applies_to = 'spending' ORDER BY rowid LIMIT 1"
    ).fetchone()
    return str(row[0])


def _tx(client: TestClient, tx_id: str) -> dict:  # type: ignore[type-arg]
    return next(t for t in client.get("/api/data").json()["transactions"] if t["id"] == tx_id)


def test_data_payload_is_gzipped(client: TestClient, db: Connection) -> None:
    _seed(db)
    resp = client.get("/api/data", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers["content-encoding"] == "gzip"
    body = resp.json()
    assert {"data_version", "accounts", "categories", "transactions", "net_worth"} <= set(body)
    assert [a["account_id"] for a in body["accounts"]] == ["acc-1"]
    assert {t["id"] for t in body["transactions"]} == {"t1", "t2"}


def test_status_matches_data_version_and_app_version(client: TestClient, db: Connection) -> None:
    _seed(db)
    status = client.get("/api/status").json()
    data = client.get("/api/data").json()
    assert status["app_version"] == "9.9.9+test"
    assert status["data_version"] == data["data_version"]
    assert status["newest_data_at"] == "2026-10-15T14:00:00Z"
    assert status["syncing"] is False
    assert [i["item_id"] for i in status["items"]] == ["item-1"]
    assert "institution_id" in status["items"][0]


def test_sync_endpoint_requests_all_items(client: TestClient, runtime) -> None:  # type: ignore[no-untyped-def]
    assert client.post("/api/sync").status_code == 202
    assert runtime.scheduler.is_busy
    assert client.get("/api/status").json()["syncing"] is True


def test_override_returns_new_data_version(client: TestClient, db: Connection) -> None:
    _seed(db)
    cat = _spending_category(db)
    before = client.get("/api/status").json()["data_version"]
    resp = client.patch("/api/transactions/t1/override", json={"category_id": cat})
    assert resp.status_code == 200
    after = client.get("/api/status").json()["data_version"]
    assert resp.json() == {"data_version": after} and after > before
    assert _tx(client, "t1")["override"]["category_id"] == cat


def test_override_distinguishes_absent_from_null(client: TestClient, db: Connection) -> None:
    _seed(db)
    cat = _spending_category(db)
    client.patch("/api/transactions/t1/override", json={"category_id": cat})
    client.patch("/api/transactions/t1/override", json={"flow_class": "ignored"})
    override = _tx(client, "t1")["override"]
    assert override["category_id"] == cat and override["flow_class"] == "ignored"
    client.patch("/api/transactions/t1/override", json={"category_id": None})
    override = _tx(client, "t1")["override"]
    assert override["category_id"] is None and override["flow_class"] == "ignored"


def test_override_errors(client: TestClient, db: Connection) -> None:
    _seed(db)
    missing = client.patch("/api/transactions/nope/override", json={"flow_class": "ignored"})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    bad_cat = client.patch("/api/transactions/t1/override", json={"category_id": "nope"})
    assert bad_cat.status_code == 400
    assert bad_cat.json()["error"]["code"] == "unknown_category"
    bad_flow = client.patch("/api/transactions/t1/override", json={"flow_class": "weird"})
    assert bad_flow.json()["error"]["code"] == "invalid_flow"
    bad_rec = client.patch("/api/transactions/t1/override", json={"recurring": "yes"})
    assert bad_rec.json()["error"]["code"] == "invalid_recurring"
    assert client.patch("/api/transactions/t1/override", json={"extra": 1}).status_code == 400


def test_apply_to_merchant_creates_rule(client: TestClient, db: Connection) -> None:
    _seed(db)
    cat = _spending_category(db)
    resp = client.post("/api/transactions/t1/apply-to-merchant", json={"category_id": cat})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_version"] == client.get("/api/status").json()["data_version"]
    rules = client.get("/api/rules").json()
    assert [r["rule_id"] for r in rules] == [body["rule_id"]]
    assert rules[0]["category_id"] == cat


def test_apply_to_merchant_pending_is_400(client: TestClient, db: Connection) -> None:
    _seed(db)
    resp = client.post("/api/transactions/t2/apply-to-merchant", json={"flow_class": "ignored"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "pending"
    assert client.post("/api/transactions/nope/apply-to-merchant", json={}).status_code == 404


def test_rules_crud(client: TestClient, db: Connection) -> None:
    cat = _spending_category(db)
    created = client.post("/api/rules", json={"merchant_key": "Corner Cafe", "category_id": cat})
    assert created.status_code == 201
    rule = created.json()
    assert rule["category_id"] == cat and rule["flow_class"] is None
    dup = client.post("/api/rules", json={"merchant_key": "corner cafe"})
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "rule_exists"
    patched = client.patch(f"/api/rules/{rule['rule_id']}", json={"flow_class": "ignored"})
    assert patched.status_code == 200
    assert patched.json()["flow_class"] == "ignored" and patched.json()["category_id"] == cat
    assert client.delete(f"/api/rules/{rule['rule_id']}").status_code == 204
    assert client.get("/api/rules").json() == []
    assert client.delete(f"/api/rules/{rule['rule_id']}").status_code == 404
    assert client.patch("/api/rules/999", json={}).status_code == 404
    blank = client.post("/api/rules", json={"merchant_key": "   "})
    assert blank.status_code == 400
    assert blank.json()["error"]["code"] == "invalid_merchant_key"


def test_categories(client: TestClient, db: Connection) -> None:
    created = client.post("/api/categories", json={"name": "Pets", "applies_to": "spending"})
    assert created.status_code == 201
    cat = created.json()
    assert cat["name"] == "Pets" and cat["kind"] == "custom" and cat["hidden"] is False
    renamed = client.patch(f"/api/categories/{cat['category_id']}", json={"name": "Pets & Vet"})
    assert renamed.json()["name"] == "Pets & Vet"
    hidden = client.patch(f"/api/categories/{cat['category_id']}", json={"hidden": True})
    assert hidden.json()["hidden"] is True
    builtin = _spending_category(db)
    assert client.patch(f"/api/categories/{builtin}", json={"hidden": True}).status_code == 200
    rename_builtin = client.patch(f"/api/categories/{builtin}", json={"name": "Nope"})
    assert rename_builtin.status_code == 400
    assert rename_builtin.json()["error"]["code"] == "not_custom"
    assert client.patch("/api/categories/nope", json={"hidden": True}).status_code == 404
    bad = client.post("/api/categories", json={"name": "X", "applies_to": "sideways"})
    assert bad.json()["error"]["code"] == "invalid_applies_to"


def test_account_patch(client: TestClient, db: Connection) -> None:
    _seed(db)
    resp = client.patch("/api/accounts/acc-1", json={"ownership": "joint", "reviewed": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ownership"] == "joint" and body["reviewed"] is True and body["included"] is True
    assert client.patch("/api/accounts/nope", json={"included": False}).status_code == 404
    bad = client.patch("/api/accounts/acc-1", json={"ownership": "someone"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_ownership"


def test_system_response(client: TestClient, runtime, make_client: ClientFactory) -> None:  # type: ignore[no-untyped-def]
    from bizviz.config import replace_config

    runtime.ctx.cfg = replace_config(runtime.ctx.cfg, hostname="pinned.local")
    body = client.get("/api/system").json()
    assert body["app_version"] == "9.9.9+test"
    assert body["filevault"] is True and body["time_machine"] is True
    assert body["hostname"] == "pinned.local"
    assert body["current_hostname"] == "testmac.local"
    assert body["hostname_mismatch"] is True
    assert body["plaid_env"] == "production" and body["plaid_configured"] is True
    assert body["plaid_items_created_count"] == 0
    assert body["recovered_items"] == []
    assert body["login_locked_until"] is None
    assert body["cert_expires_at"] is None and body["ca_expiring"] is False


def test_system_reports_recovered_items_and_missing_plaid(
    client: TestClient,
    runtime,
    db: Connection,  # type: ignore[no-untyped-def]
) -> None:
    import json

    from bizviz.db.connection import set_setting, write_unit

    with write_unit(db):
        set_setting(db, "plaid_items_created_count", "3")
        set_setting(
            db,
            "recovered_items",
            json.dumps([{"item_id": "i1", "institution_name": "Bank One", "recovered_at": "x"}]),
        )
    runtime.ctx.plaid = None
    body = client.get("/api/system").json()
    assert body["recovered_items"] == ["Bank One"]
    assert body["plaid_items_created_count"] == 3
    assert body["plaid_configured"] is False
