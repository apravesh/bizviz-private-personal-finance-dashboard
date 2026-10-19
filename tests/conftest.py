import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bizviz.config import AppConfig, load_config
from bizviz.context import AppContext
from bizviz.db.connection import Connection, open_db
from bizviz.db.migrate import migrate
from bizviz.plaid_fake import FakePlaidClient
from bizviz.platform.base import Platform
from bizviz.platform.fake import (
    FakeSecretStore,
    FakeServiceManager,
    FakeSystemStatus,
    FakeTrustStore,
)

FIXED_NOW = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)


class ClockHolder:
    """Mutable clock: tests advance it via `ctx.clock_holder.now = ...` or factories.set_now."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BIZVIZ_PLATFORM", "fake")
    monkeypatch.setenv("BIZVIZ_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("BIZVIZ_FAKE_KEYCHAIN", raising=False)


@pytest.fixture
def cfg() -> AppConfig:
    return load_config("production")


@pytest.fixture
def fake_platform() -> Platform:
    return Platform(
        secrets=FakeSecretStore(None),
        service=FakeServiceManager(),
        system=FakeSystemStatus(),
        trust=FakeTrustStore(),
    )


@pytest.fixture
def db_key() -> str:
    return secrets.token_hex(32)


@pytest.fixture
def fake_plaid() -> FakePlaidClient:
    return FakePlaidClient()


@pytest.fixture
def app_ctx(
    cfg: AppConfig, fake_platform: Platform, db_key: str, fake_plaid: FakePlaidClient
) -> AppContext:
    conn = open_db(cfg.db_path, db_key)
    try:
        migrate(conn)
    finally:
        conn.close()
    holder = ClockHolder(FIXED_NOW)
    ctx = AppContext(
        cfg=cfg,
        platform=fake_platform,
        db_key_hex=db_key,
        plaid=fake_plaid,
        clock=holder,
        local_tz=ZoneInfo("America/New_York"),
    )
    ctx.clock_holder = holder  # type: ignore[attr-defined]
    return ctx


@pytest.fixture
def db(app_ctx: AppContext) -> Iterator[Connection]:
    conn = app_ctx.connect()
    try:
        yield conn
    finally:
        conn.close()
