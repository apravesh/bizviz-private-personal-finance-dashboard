import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from bizviz.config import AppConfig
from bizviz.context import AppContext
from bizviz.db.connection import Connection
from bizviz.errors import InvalidInputError, NotFoundError
from bizviz.logging_setup import configure_logging
from bizviz.platform.base import Platform
from tests import factories


def test_app_ctx_is_migrated_with_fixed_clock(app_ctx: AppContext, db: Connection) -> None:
    assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1"
    assert app_ctx.now() == datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
    assert app_ctx.today().isoformat() == "2026-10-15"


def test_today_uses_local_timezone(app_ctx: AppContext) -> None:
    factories.set_now(app_ctx, datetime(2026, 10, 16, 2, 0, tzinfo=UTC))
    assert app_ctx.today().isoformat() == "2026-10-15"
    app_ctx.local_tz = ZoneInfo("Asia/Tokyo")
    assert app_ctx.today().isoformat() == "2026-10-16"
    factories.set_now(app_ctx, app_ctx.now() + timedelta(days=1))
    assert app_ctx.today().isoformat() == "2026-10-17"


def test_default_context_uses_system_tz_and_real_clock(
    cfg: AppConfig, fake_platform: Platform, db_key: str
) -> None:
    ctx = AppContext(cfg=cfg, platform=fake_platform, db_key_hex=db_key)
    assert ctx.tz() is not None
    assert abs(ctx.now() - datetime.now(UTC)) < timedelta(seconds=5)
    assert ctx.now().tzinfo is not None
    assert ctx.request_sync("item-1") is None


def test_factories_build_graph(db: Connection) -> None:
    factories.make_item(db)
    factories.make_account(db)
    factories.make_txn(db, "t1", "acc-1", "2026-10-01", -12.5, name="Coffee")
    factories.make_override(db, "t1", category_id="plaid:FOOD_AND_DRINK", recurring=True)
    rule_id = factories.make_rule(db, "coffee", flow_class="spending")
    factories.make_balance_snapshot(db, "acc-1", "2026-10-01", 100.0)
    factories.make_security(db, "sec-1", "VTI")
    factories.make_holding_snapshot(db, "acc-1", "sec-1", "2026-10-01", 2, 250.0)
    factories.make_inv_txn(db, "it-1", "acc-1", "sec-1", "2026-10-01", "buy", "buy", 2, 500.0)
    assert rule_id == 1
    assert db.execute("SELECT count(*) FROM transactions").fetchone()[0] == 1
    assert db.execute("SELECT recurring FROM tx_overrides").fetchone()[0] == 1
    row = db.execute("SELECT captured_at FROM holdings_snapshots").fetchone()
    assert row[0] == "2026-10-01T16:00:00Z"
    assert db.execute("SELECT products FROM items").fetchone()[0] == '["transactions"]'


def test_errors() -> None:
    assert issubclass(NotFoundError, LookupError)
    err = InvalidInputError("bad_thing", "details")
    assert err.code == "bad_thing" and isinstance(err, ValueError)


def test_configure_logging_writes_file(cfg: AppConfig) -> None:
    configure_logging(cfg)
    configure_logging(cfg)
    root = logging.getLogger()
    try:
        logging.getLogger("bizviz.test").info("hello-id-42")
        for handler in root.handlers:
            handler.flush()
        assert "hello-id-42" in (cfg.log_dir / "service.log").read_text()
        assert logging.getLogger("yfinance").level == logging.WARNING
        assert logging.getLogger("uvicorn.access").disabled
        assert sum(getattr(h, "_bizviz", False) for h in root.handlers) == 1
    finally:
        for handler in list(root.handlers):
            if getattr(handler, "_bizviz", False):
                root.removeHandler(handler)
                handler.close()
        logging.getLogger("uvicorn.access").disabled = False


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    from bizviz.cli import main

    main(["version"])
    assert capsys.readouterr().out.strip() == "0.1.0"
