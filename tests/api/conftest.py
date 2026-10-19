import json
from pathlib import Path

import pytest

from bizviz.auth import set_dashboard_password
from bizviz.config import replace_config
from bizviz.context import AppContext
from bizviz.db.connection import Connection

PASSWORD = "correct horse battery"
INDEX_HTML = (
    '<!doctype html><html><head><title>t</title></head><body><div id="app"></div>'
    '<script nonce="__CSP_NONCE__" type="module" src="/assets/app.js"></script>'
    '<script nonce="__CSP_NONCE__">window.x = 1</script></body></html>'
)


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
