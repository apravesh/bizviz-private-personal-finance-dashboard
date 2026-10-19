from pathlib import Path

import pytest

from bizviz.config import AppConfig, load_config
from bizviz.platform.base import Platform
from bizviz.platform.fake import (
    FakeSecretStore,
    FakeServiceManager,
    FakeSystemStatus,
    FakeTrustStore,
)


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
