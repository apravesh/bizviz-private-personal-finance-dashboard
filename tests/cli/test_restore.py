import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bizviz import cli, tokens
from bizviz.auth import check_dashboard_password, create_session, validate_session
from bizviz.backup import decode_recovery_phrase, encode_recovery_phrase, make_backup
from bizviz.config import KC_DB_KEY, KC_TOKEN_PREFIX, AppConfig
from bizviz.context import AppContext
from bizviz.platform.base import Platform
from bizviz.platform.fake import FakeSecretStore, FakeServiceManager
from tests.cli.conftest import PASSWORD, FakeRunner, ScriptedPrompter
from tests.cli.test_setup import fresh_prompter, open_ctx, run_setup, service
from tests.factories import make_account, make_item

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
NEW_PASSWORD = "a brand new password"


class Machine:
    def __init__(self, platform: Platform, cfg: AppConfig, backup: Path, phrase: str) -> None:
        self.platform = platform
        self.cfg = cfg
        self.backup = backup
        self.phrase = phrase
        self.token = ""


@pytest.fixture
def machine(platform: Platform, cfg: AppConfig, tmp_path: Path) -> Machine:
    """A set-up Mac with one connected bank and a backup taken while a session was active."""
    assert run_setup(platform, fresh_prompter(platform), FakeRunner()) == 0
    ctx = open_ctx(cfg, platform)
    conn = ctx.connect()
    try:
        make_item(conn, "item-a", institution_name="Bank A", access_token="access-a")
        make_account(conn, "acc-a", "item-a")
        token, _ = create_session(conn, "iPhone", "ua", NOW)
        backup = tmp_path / "offsite" / "bizviz-2026-10-14.db"
        backup.parent.mkdir()
        make_backup(conn, ctx.db_key_hex, backup)
    finally:
        conn.close()
    m = Machine(platform, cfg, backup, encode_recovery_phrase(ctx.db_key_hex))
    m.token = token
    return m


def snapshot(cfg: AppConfig) -> dict[str, bytes]:
    return {
        str(p.relative_to(cfg.home)): p.read_bytes() for p in cfg.home.rglob("*") if p.is_file()
    }


def items(cfg: AppConfig, platform: Platform) -> dict[str, tuple[str, str | None]]:
    conn = open_ctx(cfg, platform).connect()
    try:
        rows = conn.execute("SELECT item_id, access_token, removed_at FROM items").fetchall()
    finally:
        conn.close()
    return {r["item_id"]: (r["access_token"], r["removed_at"]) for r in rows}


def test_restore_refused_without_force_changes_nothing(
    machine: Machine, capsys: pytest.CaptureFixture[str]
) -> None:
    before = snapshot(machine.cfg)
    secrets_before = dict(machine.platform.secrets._data)  # type: ignore[attr-defined]
    calls = list(service(machine.platform).calls)
    prompter = ScriptedPrompter()
    code = run_setup(machine.platform, prompter, FakeRunner(), "--restore", str(machine.backup))
    assert code == cli.EXIT_RESTORE_REFUSED
    assert "--force" in capsys.readouterr().err
    assert prompter.asked == []
    assert snapshot(machine.cfg) == before
    assert machine.platform.secrets._data == secrets_before  # type: ignore[attr-defined]
    assert service(machine.platform).calls == calls


def test_restore_force_on_same_machine(machine: Machine) -> None:
    platform, cfg = machine.platform, machine.cfg
    # Added after the backup: only the Keychain index and token know about it.
    tokens.index_merge(
        platform.secrets,
        [{"item_id": "item-b", "institution_name": "Bank B", "link_mode": "bank"}],
    )
    platform.secrets.set(KC_TOKEN_PREFIX + "item-b", "access-b")
    tokens.index_merge(
        platform.secrets,
        [{"item_id": "item-a", "removed_at": "2026-10-14T00:00:00Z"}],
    )
    runner = FakeRunner()
    prompter = ScriptedPrompter(asks=["y"], secrets=[machine.phrase, NEW_PASSWORD, NEW_PASSWORD])

    code = run_setup(platform, prompter, runner, "--restore", str(machine.backup), "--force")

    assert code == 0
    assert (cfg.db_path.with_name(cfg.db_path.name)).exists()
    assert list(cfg.db_path.parent.glob("bizviz.db.bak-*"))
    assert any(k.startswith(KC_DB_KEY + ".bak-") for k in platform.secrets._data)  # type: ignore[attr-defined]
    conn = open_ctx(cfg, platform).connect()
    try:
        assert validate_session(conn, machine.token, NOW) is None
        assert check_dashboard_password(conn, NEW_PASSWORD)
    finally:
        conn.close()
    found = items(cfg, platform)
    assert found["item-a"] == ("access-a", "2026-10-14T00:00:00Z")
    assert found["item-b"][0] == "access-b"
    assert platform.secrets.get(KC_TOKEN_PREFIX + "item-a") == "access-a"
    index = {e["item_id"] for e in tokens.index_read(platform.secrets)}
    assert index == {"item-a", "item-b"}
    assert [c[0] for c in runner.calls] == [("npm", "ci"), ("npm", "run", "build")]
    kinds = [c[0] for c in service(platform).calls]
    assert kinds[kinds.index("stop") :] == ["stop", "install"]
    assert service(platform).running


def test_restore_declining_password_reset_keeps_backup_password(machine: Machine) -> None:
    prompter = ScriptedPrompter(asks=["n"], secrets=[machine.phrase])
    assert (
        run_setup(
            machine.platform, prompter, FakeRunner(), "--restore", str(machine.backup), "--force"
        )
        == 0
    )
    conn = open_ctx(machine.cfg, machine.platform).connect()
    try:
        assert check_dashboard_password(conn, PASSWORD)
        assert validate_session(conn, machine.token, NOW) is None
    finally:
        conn.close()


def test_restore_on_a_new_mac(machine: Machine, cfg: AppConfig) -> None:
    shutil.rmtree(cfg.home)
    new_platform = Platform(
        secrets=FakeSecretStore(None),
        service=FakeServiceManager(),
        system=machine.platform.system,
        trust=machine.platform.trust,
    )
    prompter = ScriptedPrompter(asks=["new-id", "n"], secrets=[machine.phrase, "new-secret"])
    runner = FakeRunner()

    code = run_setup(new_platform, prompter, runner, "--restore", str(machine.backup))

    assert code == 0
    assert new_platform.secrets.get(KC_DB_KEY) == machine.platform.secrets.get(KC_DB_KEY)
    assert items(cfg, new_platform)["item-a"][0] == "access-a"
    assert new_platform.secrets.get(KC_TOKEN_PREFIX + "item-a") == "access-a"
    assert service(new_platform).calls[-1][0] == "install"
    assert len(runner.calls) == 2


def test_restore_wrong_phrase_exits_6_and_restarts_service(machine: Machine) -> None:
    before = snapshot(machine.cfg)
    prompter = ScriptedPrompter(secrets=[encode_recovery_phrase("ab" * 32)])
    code = run_setup(
        machine.platform, prompter, FakeRunner(), "--restore", str(machine.backup), "--force"
    )
    assert code == cli.EXIT_BAD_PHRASE
    assert snapshot(machine.cfg) == before
    assert service(machine.platform).running
    assert service(machine.platform).calls[-2:] == [("stop",), ("start",)]


def test_restore_missing_backup_file(machine: Machine, tmp_path: Path) -> None:
    prompter = ScriptedPrompter()
    code = run_setup(
        machine.platform,
        prompter,
        FakeRunner(),
        "--restore",
        str(tmp_path / "missing.db"),
        "--force",
    )
    assert code == cli.EXIT_ABORTED
    assert prompter.asked == []


def test_recover_key_stores_key_without_touching_files(machine: Machine) -> None:
    machine.platform.secrets.delete(KC_DB_KEY)
    before = snapshot(machine.cfg)
    runner = FakeRunner()
    code = run_setup(
        machine.platform, ScriptedPrompter(secrets=[machine.phrase]), runner, "--recover-key"
    )
    assert code == 0
    assert machine.platform.secrets.get(KC_DB_KEY) == decode_recovery_phrase(machine.phrase)
    assert snapshot(machine.cfg) == before
    assert runner.calls == []
    ctx = open_ctx(machine.cfg, machine.platform)
    assert isinstance(ctx, AppContext)
    ctx.connect().close()


def test_recover_key_wrong_phrase_exits_6(machine: Machine) -> None:
    machine.platform.secrets.delete(KC_DB_KEY)
    code = run_setup(
        machine.platform,
        ScriptedPrompter(secrets=[encode_recovery_phrase("cd" * 32)]),
        FakeRunner(),
        "--recover-key",
    )
    assert code == cli.EXIT_BAD_PHRASE
    assert machine.platform.secrets.get(KC_DB_KEY) is None


def test_recover_key_without_database(
    platform: Platform, cfg: AppConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_setup(
        platform,
        ScriptedPrompter(secrets=[encode_recovery_phrase("ab" * 32)]),
        FakeRunner(),
        "--recover-key",
    )
    assert code == cli.EXIT_ABORTED
    assert "No database" in capsys.readouterr().err
