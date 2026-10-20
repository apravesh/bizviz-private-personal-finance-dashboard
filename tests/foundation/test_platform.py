import stat
import sys
from pathlib import Path

import pytest

from bizviz.config import load_config
from bizviz.platform import UnsupportedPlatformError, get_platform
from bizviz.platform.fake import (
    FakeServiceManager,
    FakeSystemStatus,
    FakeTrustStore,
)


def test_fake_platform_components() -> None:
    cfg = load_config("production")
    platform = get_platform(cfg)
    assert isinstance(platform.service, FakeServiceManager)
    assert isinstance(platform.system, FakeSystemStatus)
    assert isinstance(platform.trust, FakeTrustStore)


def test_fake_secret_store_persists_outside_data_dir() -> None:
    cfg = load_config("production")
    get_platform(cfg).secrets.set("db-key", "abc")
    key_file = cfg.home.parent / "fake-keychain-BizViz.json"
    assert key_file.exists()
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert get_platform(cfg).secrets.get("db-key") == "abc"
    import shutil

    shutil.rmtree(cfg.home)
    assert get_platform(cfg).secrets.get("db-key") == "abc"


def test_sandbox_uses_separate_fake_keychain() -> None:
    sandbox = load_config("sandbox")
    get_platform(sandbox).secrets.set("k", "v")
    assert (sandbox.home.parent / "fake-keychain-BizViz-sandbox.json").exists()
    assert get_platform(load_config("production")).secrets.get("k") is None


def test_fake_keychain_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.json"
    monkeypatch.setenv("BIZVIZ_FAKE_KEYCHAIN", str(target))
    get_platform(load_config("production")).secrets.set("a", "b")
    assert target.exists()


def test_fake_secret_store_delete_and_in_memory() -> None:
    from bizviz.platform.fake import FakeSecretStore

    store = FakeSecretStore(None)
    store.set("a", "1")
    assert store.get("a") == "1"
    store.delete("a")
    store.delete("missing")
    assert store.get("a") is None


def test_fake_service_manager_tracks_state() -> None:
    svc = FakeServiceManager()
    assert not svc.is_running()
    svc.install(Path("/py"), Path("/repo"), Path("/logs"))
    assert svc.is_running()
    svc.stop()
    assert not svc.is_running()
    svc.start()
    svc.uninstall()
    assert [c[0] for c in svc.calls] == ["install", "stop", "start", "uninstall"]
    assert not svc.is_running()


def test_fake_trust_store_records() -> None:
    trust = FakeTrustStore()
    trust.trust_ca(Path("/ca.pem"))
    assert trust.trusted == [Path("/ca.pem")]
    trust.untrust_ca(Path("/ca.pem"))
    assert trust.trusted == []


def test_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BIZVIZ_PLATFORM")
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(UnsupportedPlatformError, match=r"BizViz currently supports macOS only\."):
        get_platform(load_config("production"))


def test_macos_module_imports_on_linux() -> None:
    from bizviz.platform import macos

    assert macos.LaunchdServiceManager().label == "com.bizviz.agent"
