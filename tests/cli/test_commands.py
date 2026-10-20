import socket
import threading
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from bizviz import certs, cli, tokens
from bizviz.auth import check_dashboard_password, create_session, validate_session
from bizviz.config import (
    KC_CA_KEY,
    KC_DB_KEY,
    KC_ITEM_INDEX,
    KC_PLAID_CLIENT_ID,
    KC_PLAID_SECRET,
    KC_TOKEN_PREFIX,
    AppConfig,
    load_config,
)
from bizviz.platform import get_platform
from bizviz.platform.base import Platform
from bizviz.platform.fake import FakeSystemStatus, FakeTrustStore
from tests.cli.conftest import PASSWORD, FakeRunner, ScriptedPrompter
from tests.cli.test_restore import NOW
from tests.cli.test_setup import fresh_prompter, open_ctx, run_setup, service
from tests.factories import make_item


@pytest.fixture
def installed(platform: Platform, cfg: AppConfig) -> Platform:
    assert run_setup(platform, fresh_prompter(platform), FakeRunner()) == 0
    return platform


def run(platform: Platform, *argv: str, prompter: ScriptedPrompter | None = None) -> int:
    return cli.main(
        list(argv), prompter=prompter or ScriptedPrompter(), runner=FakeRunner(), platform=platform
    )


def sans(cfg: AppConfig) -> list[str]:
    return list(certs.cert_info(cfg)["leaf_sans"])


def test_version_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    from bizviz import __version__

    assert cli.main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_main_requires_a_command() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_status_before_setup(platform: Platform, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(platform, "status") == 0
    out = capsys.readouterr().out
    assert "Database: not set up" in out
    assert "Service: not running" in out
    assert "Last backup: none" in out


def test_status_after_setup(
    installed: Platform, cfg: AppConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = open_ctx(cfg, installed).connect()
    try:
        make_item(conn, "item-a", institution_name="Bank A", last_success_at="2026-10-14T09:00:00Z")
        make_item(
            conn,
            "item-b",
            institution_name="Bank B",
            status="needs_attention",
            error_code="ITEM_LOGIN_REQUIRED",
        )
        make_item(conn, "item-c", institution_name="Gone", removed_at="2026-10-01T00:00:00Z")
    finally:
        conn.close()
    capsys.readouterr()
    assert run(installed, "status") == 0
    out = capsys.readouterr().out
    assert "Service: running" in out
    assert "Connections: 2" in out
    assert "Bank A: ok, last sync 2026-10-14T09:00:00Z" in out
    assert "Bank B: needs_attention (ITEM_LOGIN_REQUIRED), last sync never" in out
    assert "Gone" not in out
    assert "Certificate expires:" in out
    assert "Hostname: pinned testmac.local, current testmac.local" in out
    assert "FileVault: on" in out
    assert "Warning" not in out


def test_status_reports_hostname_change_and_missing_key(
    installed: Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    assert isinstance(installed.system, FakeSystemStatus)
    installed.system.hostname = "renamed.local"
    installed.system.filevault = None
    capsys.readouterr()
    assert run(installed, "status") == 0
    out = capsys.readouterr().out
    assert "current renamed.local" in out
    assert "rotate-cert --hostname" in out
    assert "FileVault: unknown" in out

    installed.secrets.delete(KC_DB_KEY)
    assert run(installed, "status") == 0
    assert "key missing" in capsys.readouterr().out


def test_rotate_cert_reissues_and_restarts(installed: Platform, cfg: AppConfig) -> None:
    before = certs.leaf_path(cfg).read_bytes()
    ca_before = certs.ca_path(cfg).read_bytes()
    assert run(installed, "rotate-cert") == 0
    assert certs.leaf_path(cfg).read_bytes() != before
    assert certs.ca_path(cfg).read_bytes() == ca_before
    assert [c[0] for c in service(installed).calls][-2:] == ["stop", "start"]


def test_rotate_cert_hostname_keeps_previous_name(installed: Platform) -> None:
    assert run(installed, "rotate-cert", "--hostname", "newname.local") == 0
    cfg = load_config("production")
    assert cfg.hostname == "newname.local"
    assert cfg.previous_hostnames == ("testmac.local",)
    assert {"newname.local", "testmac.local"} <= set(sans(cfg))


def test_rotate_cert_add_hostname(installed: Platform) -> None:
    assert run(installed, "rotate-cert", "--add-hostname", "bizviz.example") == 0
    assert run(installed, "rotate-cert", "--add-hostname", "bizviz.example") == 0
    cfg = load_config("production")
    assert cfg.extra_hostnames == ("bizviz.example",)
    assert cfg.hostname == "testmac.local"
    assert "bizviz.example" in sans(cfg)


def test_rotate_cert_ca_replaces_and_trusts_new_ca(installed: Platform, cfg: AppConfig) -> None:
    ca_before = certs.ca_path(cfg).read_bytes()
    key_before = installed.secrets.get(KC_CA_KEY)
    assert isinstance(installed.trust, FakeTrustStore)
    trusted_before = len(installed.trust.trusted)
    assert run(installed, "rotate-cert", "--ca") == 0
    assert certs.ca_path(cfg).read_bytes() != ca_before
    assert installed.secrets.get(KC_CA_KEY) != key_before
    assert len(installed.trust.trusted) == trusted_before + 1
    assert not certs.leaf_needs_renewal(cfg, NOW.replace(year=2026))


def test_reset_password_changes_password_and_signs_everyone_out(
    installed: Platform, cfg: AppConfig
) -> None:
    conn = open_ctx(cfg, installed).connect()
    try:
        token, _ = create_session(conn, "iPhone", "ua", NOW)
    finally:
        conn.close()
    prompter = ScriptedPrompter(secrets=["a different password", "a different password"])
    assert run(installed, "reset-password", prompter=prompter) == 0
    conn = open_ctx(cfg, installed).connect()
    try:
        assert check_dashboard_password(conn, "a different password")
        assert not check_dashboard_password(conn, PASSWORD)
        assert validate_session(conn, token, NOW) is None
    finally:
        conn.close()


def test_reset_password_without_key_exits_3(platform: Platform) -> None:
    assert run(platform, "reset-password") == cli.EXIT_KEY_MISSING


def seed_token(platform: Platform) -> None:
    tokens.index_merge(platform.secrets, [{"item_id": "item-a", "institution_name": "Bank A"}])
    platform.secrets.set(KC_TOKEN_PREFIX + "item-a", "access-a")


def test_uninstall_keeps_data(installed: Platform, cfg: AppConfig) -> None:
    assert run(installed, "uninstall") == 0
    assert service(installed).calls[-1] == ("uninstall",)
    assert cfg.db_path.exists()
    assert installed.secrets.get(KC_DB_KEY)


def test_uninstall_delete_data_keeps_tokens(installed: Platform, cfg: AppConfig) -> None:
    seed_token(installed)
    prompter = ScriptedPrompter(asks=[cli.DELETE_DATA_PHRASE])
    assert run(installed, "uninstall", "--delete-data", prompter=prompter) == 0
    assert not cfg.home.exists()
    for name in (KC_DB_KEY, KC_CA_KEY, KC_PLAID_CLIENT_ID, KC_PLAID_SECRET):
        assert installed.secrets.get(name) is None
    assert installed.secrets.get(KC_TOKEN_PREFIX + "item-a") == "access-a"
    assert installed.secrets.get(KC_ITEM_INDEX)
    assert isinstance(installed.trust, FakeTrustStore)
    assert installed.trust.trusted == []


def test_uninstall_delete_data_and_tokens(installed: Platform, cfg: AppConfig) -> None:
    seed_token(installed)
    prompter = ScriptedPrompter(asks=[cli.DELETE_DATA_PHRASE, cli.DELETE_TOKENS_PHRASE])
    assert run(installed, "uninstall", "--delete-data", "--delete-tokens", prompter=prompter) == 0
    assert not cfg.home.exists()
    assert installed.secrets.get(KC_TOKEN_PREFIX + "item-a") is None
    assert installed.secrets.get(KC_ITEM_INDEX) is None


def test_uninstall_wrong_confirmation_changes_nothing(installed: Platform, cfg: AppConfig) -> None:
    calls = list(service(installed).calls)
    prompter = ScriptedPrompter(asks=["yes"])
    assert run(installed, "uninstall", "--delete-data", prompter=prompter) == cli.EXIT_ABORTED
    assert cfg.db_path.exists()
    assert service(installed).calls == calls

    prompter = ScriptedPrompter(asks=[cli.DELETE_DATA_PHRASE, "no"])
    code = run(installed, "uninstall", "--delete-data", "--delete-tokens", prompter=prompter)
    assert code == cli.EXIT_ABORTED
    assert cfg.db_path.exists()
    assert service(installed).calls == calls


def test_delete_tokens_requires_delete_data(installed: Platform, cfg: AppConfig) -> None:
    prompter = ScriptedPrompter()
    assert run(installed, "uninstall", "--delete-tokens", prompter=prompter) == cli.EXIT_ABORTED
    assert prompter.asked == []
    assert cfg.db_path.exists()


def test_sandbox_setup_installs_no_service(
    platform: Platform, capsys: pytest.CaptureFixture[str]
) -> None:
    prompter = fresh_prompter(platform)
    code = cli.main(
        ["--env", "sandbox", "setup"], prompter=prompter, runner=FakeRunner(), platform=platform
    )
    assert code == 0
    assert service(platform).calls == []
    cfg = load_config("sandbox")
    assert cfg.db_path.exists()
    assert not load_config("production").db_path.exists()
    out = capsys.readouterr().out
    assert "https://testmac.local:8445" in out
    assert "bizviz --env sandbox serve" in out


class UvicornSpy:
    def __init__(self) -> None:
        self.config: Any = None
        self.sockets: list[socket.socket] = []
        self.ports: list[int] = []

    @property
    def app(self) -> Any:
        return None if self.config is None else self.config.app

    @property
    def port(self) -> int:
        return self.ports[0]


@pytest.fixture
def uvicorn_spy(monkeypatch: pytest.MonkeyPatch) -> UvicornSpy:
    spy = UvicornSpy()

    real_listen = cli.listen_socket

    def fake_listen(port: int) -> socket.socket:
        spy.ports.append(port)
        return real_listen(0)

    def fake_run(self: uvicorn.Server, sockets: list[socket.socket] | None = None) -> None:
        spy.config = self.config
        spy.sockets = list(sockets or [])

    monkeypatch.setattr(cli, "listen_socket", fake_listen)
    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
    return spy


@pytest.fixture
def stored(cfg: AppConfig) -> None:
    """Set up with the file-backed fake Keychain, which `serve` and `demo` open themselves."""
    platform = get_platform(cfg)
    assert run_setup(platform, fresh_prompter(platform), FakeRunner()) == 0


@pytest.mark.usefixtures("stored")
def test_serve_runs_uvicorn_with_tls_on_the_env_port(
    cfg: AppConfig, uvicorn_spy: UvicornSpy
) -> None:
    assert cli.main(["serve"]) == 0
    config = uvicorn_spy.config
    assert uvicorn_spy.ports == [8443]
    assert len(uvicorn_spy.sockets) == 1
    assert config.ssl_certfile == str(certs.leaf_path(cfg))
    assert config.ssl_keyfile == str(certs.leaf_key_path(cfg))
    assert config.workers == 1
    assert config.access_log is False
    assert config.log_config is None


@pytest.mark.usefixtures("stored")
def test_serve_port_override_and_missing_leaf_is_issued(
    cfg: AppConfig, uvicorn_spy: UvicornSpy
) -> None:
    certs.leaf_path(cfg).unlink()
    assert cli.main(["serve", "--port", "9999"]) == 0
    assert uvicorn_spy.port == 9999
    assert certs.leaf_path(cfg).exists()


def test_serve_before_setup_points_at_setup(
    uvicorn_spy: UvicornSpy, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["serve"]) == cli.EXIT_ABORTED
    assert "bizviz setup" in capsys.readouterr().err
    assert uvicorn_spy.app is None


def test_demo_prints_url_and_password_and_serves(
    uvicorn_spy: UvicornSpy, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "https://localhost:8444" in out
    assert "demo-password" in out
    assert uvicorn_spy.port == 8444
    assert uvicorn_spy.app is not None


def test_demo_custom_port(uvicorn_spy: UvicornSpy, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["demo", "--port", "9001"]) == 0
    assert "https://localhost:9001" in capsys.readouterr().out
    assert uvicorn_spy.port == 9001


def test_repo_location_problem_unit(tmp_path: Path) -> None:
    home = tmp_path / "me"
    assert cli.repo_location_problem(home / "bizviz", home) is None
    assert cli.repo_location_problem(home / "Developer" / "bizviz", home) is None
    assert "Documents" in (cli.repo_location_problem(home / "Documents" / "x", home) or "")
    assert cli.repo_location_problem(home / "Library" / "Mobile Documents" / "x", home)


def test_ca_is_exported_to_the_desktop_when_allowed(
    platform: Platform,
    monkeypatch: pytest.MonkeyPatch,
    _fake_user_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (_fake_user_home / "Desktop").mkdir()
    monkeypatch.setattr(cli, "_desktop_export_allowed", lambda cfg: True)
    assert run_setup(platform, fresh_prompter(platform), FakeRunner()) == 0
    exported = _fake_user_home / "Desktop" / cli.CA_DESKTOP_NAME
    assert exported.exists()
    assert str(exported) in capsys.readouterr().out


@pytest.mark.parametrize(
    ("env", "home", "platform_name", "allowed"),
    [
        ("production", None, None, True),
        ("sandbox", None, None, False),
        ("production", "/tmp/x", None, False),
        ("production", None, "fake", False),
    ],
)
def test_desktop_export_gate(
    monkeypatch: pytest.MonkeyPatch,
    cfg: AppConfig,
    env: str,
    home: str | None,
    platform_name: str | None,
    allowed: bool,
) -> None:
    import dataclasses

    monkeypatch.delenv("BIZVIZ_HOME")
    monkeypatch.delenv("BIZVIZ_PLATFORM")
    if home:
        monkeypatch.setenv("BIZVIZ_HOME", home)
    if platform_name:
        monkeypatch.setenv("BIZVIZ_PLATFORM", platform_name)
    probe = dataclasses.replace(cfg, env=env)  # type: ignore[arg-type]
    assert cli._desktop_export_allowed(probe) is allowed


def accept_one(sock: socket.socket, reply: bytes) -> threading.Thread:
    def serve() -> None:
        conn, _ = sock.accept()
        with conn:
            conn.sendall(reply)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


def test_listen_socket_is_dual_stack_and_accepts_ipv4_clients() -> None:
    sock = cli.listen_socket(0)
    try:
        port = sock.getsockname()[1]
        if sock.family == socket.AF_INET6:
            assert sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) == 0
            assert sock.getsockname()[0] == "::"
        assert sock.get_inheritable()
        thread = accept_one(sock, b"hello")
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            assert client.recv(5) == b"hello"
        thread.join(5)
    finally:
        sock.close()


def test_listen_socket_falls_back_to_ipv4_without_ipv6(monkeypatch: pytest.MonkeyPatch) -> None:
    real = socket.socket

    def no_ipv6(family: int = -1, *args: Any, **kwargs: Any) -> socket.socket:
        if family == socket.AF_INET6:
            raise OSError("IPv6 unavailable")
        return real(family, *args, **kwargs)

    monkeypatch.setattr(cli.socket, "socket", no_ipv6)
    sock = cli.listen_socket(0)
    try:
        assert sock.family == socket.AF_INET
        assert sock.getsockname()[0] == "0.0.0.0"
    finally:
        sock.close()


def test_listen_socket_port_in_use_is_a_clear_error(capsys: pytest.CaptureFixture[str]) -> None:
    holder = cli.listen_socket(0)
    try:
        port = holder.getsockname()[1]
        assert cli.main(["demo", "--port", str(port)]) == cli.EXIT_ABORTED
        assert f"Cannot listen on port {port}" in capsys.readouterr().err
    finally:
        holder.close()
