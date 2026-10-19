import stat
from pathlib import Path

import pytest

from bizviz.config import PORT_PRODUCTION, PORT_SANDBOX, load_config, replace_config, save_config


def test_defaults_without_bizviz_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BIZVIZ_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    prod = load_config("production")
    assert prod.home == tmp_path / "Library" / "Application Support" / "BizViz"
    assert prod.log_dir == tmp_path / "Library" / "Logs" / "BizViz"
    assert prod.keychain_service == "BizViz"
    assert prod.port == PORT_PRODUCTION == 8443
    assert prod.plaid_env == "production"
    sandbox = load_config("sandbox")
    assert sandbox.home == tmp_path / "Library" / "Application Support" / "BizViz-sandbox"
    assert sandbox.log_dir == tmp_path / "Library" / "Logs" / "BizViz-sandbox"
    assert sandbox.keychain_service == "BizViz-sandbox"
    assert sandbox.port == PORT_SANDBOX == 8445
    assert sandbox.plaid_env == "sandbox"


def test_bizviz_home_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BIZVIZ_HOME", str(tmp_path / "x"))
    prod = load_config("production")
    sandbox = load_config("sandbox")
    assert prod.home == tmp_path / "x"
    assert prod.log_dir == tmp_path / "x" / "logs"
    assert sandbox.home == tmp_path / "x-sandbox"
    assert sandbox.log_dir == tmp_path / "x-sandbox" / "logs"
    assert not sandbox.home.is_relative_to(prod.home)


def test_paths_and_directories() -> None:
    cfg = load_config("production")
    assert cfg.db_path == cfg.home / "bizviz.db"
    assert cfg.backups_dir == cfg.home / "backups" and cfg.backups_dir.is_dir()
    assert cfg.certs_dir == cfg.home / "certs" and cfg.certs_dir.is_dir()
    assert cfg.log_dir.is_dir()
    assert cfg.config_toml_path == cfg.home / "config.toml"
    assert stat.S_IMODE(cfg.home.stat().st_mode) == 0o700
    assert cfg.web_dist == cfg.repo_dir / "web" / "dist"
    assert (cfg.repo_dir / "pyproject.toml").exists()


def test_load_defaults_when_no_file() -> None:
    cfg = load_config("production")
    assert cfg.hostname is None
    assert cfg.extra_hostnames == ()
    assert cfg.previous_hostnames == ()


def test_save_load_round_trip() -> None:
    cfg = load_config("sandbox")
    changed = replace_config(
        cfg,
        hostname='my"mac.local',
        extra_hostnames=("a.local", "b\\c"),
        previous_hostnames=("old.local",),
        port=9000,
    )
    save_config(changed)
    assert stat.S_IMODE(cfg.config_toml_path.stat().st_mode) == 0o600
    loaded = load_config("sandbox")
    assert loaded.hostname == 'my"mac.local'
    assert loaded.extra_hostnames == ("a.local", "b\\c")
    assert loaded.previous_hostnames == ("old.local",)
    assert loaded.port == 9000
    assert loaded.plaid_env == "sandbox"


def test_hostname_omitted_when_none_and_plaid_env_ignored_from_file() -> None:
    cfg = load_config("production")
    save_config(cfg)
    assert "\nhostname" not in "\n" + cfg.config_toml_path.read_text()
    cfg.config_toml_path.write_text('plaid_env = "sandbox"\n')
    assert load_config("production").plaid_env == "production"
