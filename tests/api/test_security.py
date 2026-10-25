import re

from fastapi.testclient import TestClient

from tests.api.conftest import HEADERS, PASSWORD, ClientFactory

PLAID_SCRIPT = "https://cdn.plaid.com/link/v2/stable/link-initialize.js"


def test_post_without_csrf_header_is_403(make_client: ClientFactory) -> None:
    c = make_client()
    resp = c.post("/api/auth/login", json={"password": "x", "device_name": "d"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf"


def test_authenticated_write_without_csrf_header_is_403(client: TestClient) -> None:
    del client.headers["X-BizViz"]
    assert client.post("/api/sync").status_code == 403
    assert client.get("/api/status").status_code == 200


def test_wrong_csrf_header_value_is_403(client: TestClient) -> None:
    resp = client.post("/api/sync", headers={"X-BizViz": "0"})
    assert resp.status_code == 403


def test_cross_origin_write_is_403(client: TestClient) -> None:
    resp = client.post("/api/sync", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf"


def test_http_origin_is_403_and_matching_origin_is_allowed(client: TestClient) -> None:
    assert client.post("/api/sync", headers={"Origin": "http://testserver"}).status_code == 403
    assert client.post("/api/sync", headers={"Origin": "https://testserver"}).status_code == 202


def test_index_replaces_nonce_and_matches_csp(make_client: ClientFactory) -> None:
    resp = make_client().get("/")
    assert resp.status_code == 200
    assert "__CSP_NONCE__" not in resp.text
    nonces = set(re.findall(r'nonce="([^"]+)"', resp.text))
    assert len(nonces) == 1
    nonce = nonces.pop()
    csp = resp.headers["content-security-policy"]
    assert f"'nonce-{nonce}'" in csp
    assert PLAID_SCRIPT in csp
    assert "script-src 'self' 'nonce-" in csp
    assert "frame-src https://cdn.plaid.com/" in csp
    assert "connect-src 'self' https://production.plaid.com/" in csp
    assert "style-src-attr 'unsafe-inline'" in csp
    assert "base-uri 'none'" in csp
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"


def test_index_answers_head(make_client: ClientFactory) -> None:
    c = make_client()
    for path in ("/", "/index.html"):
        resp = c.head(path)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")


def test_nonce_differs_per_request(make_client: ClientFactory) -> None:
    c = make_client()
    a = re.search(r'nonce="([^"]+)"', c.get("/").text)
    b = re.search(r'nonce="([^"]+)"', c.get("/index.html").text)
    assert a and b and a.group(1) != b.group(1)


def test_non_index_responses_have_no_nonce(make_client: ClientFactory) -> None:
    resp = make_client().get("/assets/app.js")
    assert resp.status_code == 200
    csp = resp.headers["content-security-policy"]
    assert "nonce-" not in csp
    assert PLAID_SCRIPT in csp


def test_sandbox_env_uses_sandbox_connect_src(make_client: ClientFactory, runtime) -> None:  # type: ignore[no-untyped-def]
    from bizviz.api.app import create_app
    from bizviz.config import replace_config

    runtime.ctx.cfg = replace_config(runtime.ctx.cfg, plaid_env="sandbox")
    c = TestClient(create_app(runtime), base_url="https://testserver")
    csp = c.get("/").headers["content-security-policy"]
    assert "connect-src 'self' https://sandbox.plaid.com/" in csp
    assert "production.plaid.com" not in csp


def test_api_responses_are_no_store_with_security_headers(client: TestClient) -> None:
    for resp in (client.get("/api/status"), client.get("/api/health"), client.get("/api/nope")):
        assert resp.headers["cache-control"] == "no-store"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["referrer-policy"] == "no-referrer"
        assert "content-security-policy" in resp.headers


def test_unauthenticated_error_responses_also_carry_headers(make_client: ClientFactory) -> None:
    resp = make_client().get("/api/data")
    assert resp.status_code == 401
    assert resp.headers["cache-control"] == "no-store"


def test_static_files_are_public_with_manifest_content_type(make_client: ClientFactory) -> None:
    c = make_client()
    assert c.get("/version.json").json() == {"app_version": "9.9.9+test"}
    manifest = c.get("/manifest.webmanifest")
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert c.get("/assets/app.js").status_code == 200


def test_unknown_api_route_is_json_404(client: TestClient) -> None:
    resp = client.get("/api/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_missing_frontend_serves_503_page(make_client: ClientFactory, web_dist) -> None:  # type: ignore[no-untyped-def]
    (web_dist / "index.html").unlink()
    resp = make_client().get("/")
    assert resp.status_code == 503
    assert "Frontend not built" in resp.text


def test_errors_use_error_envelope(client: TestClient) -> None:
    resp = client.post("/api/rules", json={"bogus": 1})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "invalid"
    assert isinstance(body["error"]["message"], str)


def test_session_cookie_name_follows_the_profile(runtime) -> None:  # type: ignore[no-untyped-def]
    from dataclasses import replace

    from bizviz.api.app import create_app

    runtime.ctx.cfg = replace(runtime.ctx.cfg, session_cookie="bv_session_wife")
    c = TestClient(create_app(runtime), base_url="https://testserver", headers=HEADERS)
    resp = c.post("/api/auth/login", json={"password": PASSWORD})
    assert resp.status_code == 204
    assert resp.headers["set-cookie"].startswith("bv_session_wife=")
    assert c.get("/api/session").status_code == 200
    # The default profile's cookie name does not sign in to this one.
    token = c.cookies.get("bv_session_wife")
    c.cookies.clear()
    c.cookies.set("bv_session", token)
    assert c.get("/api/session").status_code == 401
    c.cookies.clear()
    c.cookies.set("bv_session_wife", token)
    logout = c.post("/api/auth/logout")
    assert logout.headers["set-cookie"].startswith("bv_session_wife=;")
