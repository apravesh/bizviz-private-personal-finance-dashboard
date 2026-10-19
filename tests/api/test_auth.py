import re

from fastapi.testclient import TestClient

from bizviz.api import routes_auth, routes_data, routes_edits, routes_system
from tests.api.conftest import HEADERS, PASSWORD, ClientFactory, login

PROTECTED = [
    ("GET", "/api/status"),
    ("GET", "/api/data"),
    ("GET", "/api/session"),
    ("GET", "/api/system"),
    ("GET", "/api/rules"),
    ("GET", "/api/devices"),
    ("POST", "/api/sync"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/password"),
    ("DELETE", "/api/devices/{session_id}"),
    ("PATCH", "/api/transactions/{transaction_id}/override"),
    ("POST", "/api/transactions/{transaction_id}/apply-to-merchant"),
    ("POST", "/api/rules"),
    ("PATCH", "/api/rules/{rule_id}"),
    ("DELETE", "/api/rules/{rule_id}"),
    ("POST", "/api/categories"),
    ("PATCH", "/api/categories/{category_id}"),
    ("PATCH", "/api/accounts/{account_id}"),
]


def test_protected_routes_return_401_without_cookie(make_client: ClientFactory) -> None:
    c = make_client(headers=HEADERS)
    for method, template in PROTECTED:
        path = re.sub(r"\{[^}]+\}", "1", template)
        resp = c.request(method, path, json={})
        assert resp.status_code == 401, (method, path)
        assert resp.json()["error"]["code"] == "unauthorized"


def test_protected_routes_return_401_with_bogus_cookie(make_client: ClientFactory) -> None:
    c = make_client(headers=HEADERS)
    c.cookies.set("bv_session", "nope")
    assert c.get("/api/status").status_code == 401


def test_every_declared_route_is_in_the_401_list() -> None:
    routers = [
        routes_auth.public_router,
        routes_auth.router,
        routes_data.router,
        routes_edits.router,
        routes_system.router,
    ]
    declared = {
        (m, r.path)  # type: ignore[attr-defined]
        for router in routers
        for r in router.routes
        for m in r.methods  # type: ignore[attr-defined]
    }
    assert declared - {("POST", "/api/auth/login")} == set(PROTECTED)


def test_login_sets_session_cookie(make_client: ClientFactory) -> None:
    c = make_client()
    resp = c.post(
        "/api/auth/login", json={"password": PASSWORD, "device_name": "iPhone"}, headers=HEADERS
    )
    assert resp.status_code == 204
    cookie = resp.headers["set-cookie"]
    parts = [p.strip() for p in cookie.split(";")]
    assert parts[0].startswith("bv_session=") and len(parts[0]) > 20
    assert {"HttpOnly", "Secure", "SameSite=Strict", "Path=/", "Max-Age=34560000"} <= set(parts)
    assert c.get("/api/session").json()["device_name"] == "iPhone"


def test_wrong_password_is_401_bad_password(make_client: ClientFactory) -> None:
    resp = make_client().post(
        "/api/auth/login", json={"password": "wrong", "device_name": "x"}, headers=HEADERS
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "bad_password"
    assert "set-cookie" not in resp.headers


def test_login_rejects_long_device_name(make_client: ClientFactory) -> None:
    resp = make_client().post(
        "/api/auth/login", json={"password": PASSWORD, "device_name": "x" * 65}, headers=HEADERS
    )
    assert resp.status_code == 400


def test_rate_limit_locks_lan_ip_after_five_failures(make_client: ClientFactory) -> None:
    c = make_client(ip="192.168.1.20", headers=HEADERS)
    bad = {"password": "wrong", "device_name": "x"}
    statuses = [c.post("/api/auth/login", json=bad).status_code for _ in range(5)]
    assert statuses == [401] * 5
    resp = c.post("/api/auth/login", json=bad)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"
    assert "Try again in" in resp.json()["error"]["message"]
    right = c.post("/api/auth/login", json={"password": PASSWORD, "device_name": "x"})
    assert right.status_code == 429


def test_loopback_is_never_rate_limited(make_client: ClientFactory) -> None:
    c = make_client(ip="127.0.0.1", headers=HEADERS)
    bad = {"password": "wrong", "device_name": "x"}
    assert {c.post("/api/auth/login", json=bad).status_code for _ in range(8)} == {401}
    assert (
        c.post("/api/auth/login", json={"password": PASSWORD, "device_name": "x"}).status_code
        == 204
    )


def test_logout_revokes_session_and_clears_cookie(client: TestClient) -> None:
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 204
    assert "Max-Age=0" in resp.headers["set-cookie"]
    client.cookies.clear()
    assert client.get("/api/session").status_code == 401


def test_password_change_revokes_other_sessions(make_client: ClientFactory) -> None:
    a = make_client(headers=HEADERS)
    b = make_client(headers=HEADERS)
    login(a, "A")
    login(b, "B")
    resp = a.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": "a brand new passphrase"},
    )
    assert resp.status_code == 204
    assert a.get("/api/session").status_code == 200
    assert b.get("/api/session").status_code == 401
    fresh = make_client(headers=HEADERS)
    bad = fresh.post("/api/auth/login", json={"password": PASSWORD, "device_name": "c"})
    assert bad.status_code == 401
    ok = fresh.post(
        "/api/auth/login", json={"password": "a brand new passphrase", "device_name": "c"}
    )
    assert ok.status_code == 204


def test_password_change_wrong_current_is_400_never_401(client: TestClient) -> None:
    resp = client.post(
        "/api/auth/password", json={"current_password": "nope", "new_password": "x" * 12}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_password"
    assert client.get("/api/session").status_code == 200


def test_password_change_too_short(client: TestClient) -> None:
    resp = client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": "short"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "password_too_short"


def test_devices_list_marks_current_and_delete_revokes(make_client: ClientFactory) -> None:
    a = make_client(headers=HEADERS)
    b = make_client(headers=HEADERS)
    login(a, "A")
    login(b, "B")
    devices = a.get("/api/devices").json()
    assert {d["device_name"]: d["current"] for d in devices} == {"A": True, "B": False}
    assert set(devices[0]) == {
        "session_id",
        "device_name",
        "user_agent",
        "created_at",
        "last_seen_at",
        "current",
    }
    other = next(d for d in devices if not d["current"])
    assert a.delete(f"/api/devices/{other['session_id']}").status_code == 204
    assert b.get("/api/session").status_code == 401
    assert [d["device_name"] for d in a.get("/api/devices").json()] == ["A"]


def test_health_is_public(make_client: ClientFactory) -> None:
    resp = make_client().get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "version": "9.9.9+test"}
