import json
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bizviz.api.app import create_app
from bizviz.auth import set_dashboard_password
from bizviz.config import replace_config
from bizviz.context import AppContext
from bizviz.db.connection import Connection
from bizviz.service import Runtime, make_runtime

PASSWORD = "correct horse battery"
INDEX_HTML = (
    '<!doctype html><html><head><title>t</title></head><body><div id="app"></div>'
    '<script nonce="__CSP_NONCE__" type="module" src="/assets/app.js"></script>'
    '<script nonce="__CSP_NONCE__">window.x = 1</script></body></html>'
)
HEADERS = {"X-BizViz": "1"}


@pytest.fixture
def web_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (dist / "version.json").write_text(json.dumps({"app_version": "9.9.9+test"}), encoding="utf-8")
    (dist / "manifest.webmanifest").write_text("{}", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return dist


@pytest.fixture
def ctx(app_ctx: AppContext, web_dist: Path, db: Connection) -> AppContext:
    app_ctx.cfg = replace_config(app_ctx.cfg, web_dist=web_dist)
    set_dashboard_password(db, PASSWORD)
    return app_ctx


@pytest.fixture
def runtime(ctx: AppContext) -> Runtime:
    return make_runtime(ctx)


ClientFactory = Callable[..., TestClient]


@pytest.fixture
def make_client(runtime: Runtime) -> ClientFactory:
    app = create_app(runtime)

    def factory(ip: str = "127.0.0.1", headers: dict[str, str] | None = None) -> TestClient:
        return TestClient(app, base_url="https://testserver", client=(ip, 50000), headers=headers)

    return factory


def login(client: TestClient, device: str = "Test phone") -> None:
    resp = client.post(
        "/api/auth/login",
        json={"password": PASSWORD, "device_name": device},
        headers=HEADERS,
    )
    assert resp.status_code == 204, resp.text


@pytest.fixture
def client(make_client: ClientFactory) -> TestClient:
    c = make_client(headers=HEADERS)
    login(c)
    return c
