import json
from pathlib import Path

import pytest

from bizviz import certs, cli
from bizviz.auth import check_dashboard_password
from bizviz.backup import encode_recovery_phrase
from bizviz.config import (
    KC_CA_KEY,
    KC_DB_KEY,
    KC_PLAID_CLIENT_ID,
    KC_PLAID_SECRET,
    AppConfig,
    load_config,
)
from bizviz.context import AppContext
from bizviz.db.connection import get_setting
from bizviz.platform.base import Platform
from bizviz.platform.fake import FakeServiceManager, FakeSystemStatus, FakeTrustStore
from tests.cli.conftest import PASSWORD, FakeRunner, ScriptedPrompter


class PhrasePrompter(ScriptedPrompter):
    """Answers the 'last group' question from the key stored in the platform."""

    def __init__(self, platform: Platform, *args: object, **kw: object) -> None:
        super().__init__(*args, **kw)  # type: ignore[arg-type]
        self.platform = platform

    def ask(self, prompt: str) -> str:
        if "LAST group" in prompt:
            self.asked.append(prompt)
            key = self.platform.secrets.get(KC_DB_KEY)
            assert key
            return encode_recovery_phrase(key).rsplit("-", 1)[-1].lower()
        return super().ask(prompt)


def fresh_prompter(platform: Platform, *, plaid: bool = True) -> PhrasePrompter:
    return PhrasePrompter(
        platform,
        asks=["ci-id"] if plaid else [],
        secrets=(["ci-secret"] if plaid else []) + [PASSWORD, PASSWORD],
    )


def service(platform: Platform) -> FakeServiceManager:
    assert isinstance(platform.service, FakeServiceManager)
    return platform.service


def open_ctx(cfg: AppConfig, platform: Platform) -> AppContext:
    key = platform.secrets.get(KC_DB_KEY)
    assert key
    return AppContext(cfg=cfg, platform=platform, db_key_hex=key)


def run_setup(
    platform: Platform, prompter: ScriptedPrompter, runner: FakeRunner, *args: str
) -> int:
    return cli.main(["setup", *args], prompter=prompter, runner=runner, platform=platform)


def test_fresh_setup_creates_everything(
    platform: Platform, runner: FakeRunner, cfg: AppConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    prompter = fresh_prompter(platform)
    assert run_setup(platform, prompter, runner) == 0

    assert platform.secrets.get(KC_PLAID_CLIENT_ID) == "ci-id"
    assert platform.secrets.get(KC_PLAID_SECRET) == "ci-secret"
    assert platform.secrets.get(KC_CA_KEY)
    assert (cfg.db_path.stat().st_mode & 0o777) == 0o600
    conn = open_ctx(cfg, platform).connect()
    try:
        assert get_setting(conn, "setup_completed_at")
        assert check_dashboard_password(conn, PASSWORD)
    finally:
        conn.close()
    reloaded = load_config("production")
    assert reloaded.hostname == "testmac.local"
    assert certs.leaf_path(reloaded).exists()
    assert platform.trust.trusted == [certs.ca_path(reloaded)]  # type: ignore[attr-defined]
    assert [c[0] for c in runner.calls] == [("npm", "ci"), ("npm", "run", "build")]
    assert runner.calls[0][1] == cfg.repo_dir / "web"
    install = service(platform).calls[-1]
    assert install[0] == "install"
    assert install[1] == str(cfg.repo_dir / ".venv" / "bin" / "python")
    out = capsys.readouterr().out
    assert "RECOVERY PHRASE" in out
    assert "https://testmac.local:8443" in out
    assert "https://localhost:8443" in out


def test_second_run_asks_nothing(platform: Platform, runner: FakeRunner, cfg: AppConfig) -> None:
    assert run_setup(platform, fresh_prompter(platform), runner) == 0
    key = platform.secrets.get(KC_DB_KEY)
    ca_key = platform.secrets.get(KC_CA_KEY)
    leaf_before = certs.leaf_path(cfg).read_bytes()

    silent = ScriptedPrompter()
    runner2 = FakeRunner()
    assert run_setup(platform, silent, runner2) == 0
    assert silent.asked == []
    assert platform.secrets.get(KC_DB_KEY) == key
    assert platform.secrets.get(KC_CA_KEY) == ca_key
    assert certs.leaf_path(cfg).read_bytes() == leaf_before
    assert len(runner2.calls) == 2
    assert [c[0] for c in service(platform).calls].count("stop") == 1
    assert service(platform).running


def test_reconfigure_plaid_reprompts(platform: Platform, runner: FakeRunner) -> None:
    assert run_setup(platform, fresh_prompter(platform), runner) == 0
    again = ScriptedPrompter(asks=["new-id"], secrets=["new-secret"])
    assert run_setup(platform, again, FakeRunner(), "--reconfigure-plaid") == 0
    assert platform.secrets.get(KC_PLAID_CLIENT_ID) == "new-id"
    assert platform.secrets.get(KC_PLAID_SECRET) == "new-secret"


def test_password_mismatch_and_short_password_are_reprompted(
    platform: Platform, runner: FakeRunner, cfg: AppConfig
) -> None:
    prompter = PhrasePrompter(
        platform,
        asks=["ci-id"],
        secrets=["ci-secret", "aaaaaaaaaaaa", "bbbbbbbbbbbb", "short", "short", PASSWORD, PASSWORD],
    )
    assert run_setup(platform, prompter, runner) == 0
    conn = open_ctx(cfg, platform).connect()
    try:
        assert check_dashboard_password(conn, PASSWORD)
    finally:
        conn.close()


def test_wrong_last_group_aborts_then_rerun_finishes(
    platform: Platform, runner: FakeRunner, cfg: AppConfig
) -> None:
    prompter = ScriptedPrompter(
        asks=["ci-id"] + ["WRONG"] * cli.PHRASE_ATTEMPTS, secrets=["ci-secret", PASSWORD, PASSWORD]
    )
    assert run_setup(platform, prompter, runner) == cli.EXIT_ABORTED
    assert runner.calls == []
    conn = open_ctx(cfg, platform).connect()
    try:
        assert get_setting(conn, "setup_completed_at") is None
    finally:
        conn.close()
    # Partial state recovers: the DB and key exist, the password is already set.
    again = PhrasePrompter(platform)
    assert run_setup(platform, again, runner) == 0
    conn = open_ctx(cfg, platform).connect()
    try:
        assert get_setting(conn, "setup_completed_at")
        assert check_dashboard_password(conn, PASSWORD)
    finally:
        conn.close()


@pytest.mark.parametrize("folder", ["Documents", "Desktop", "Downloads"])
def test_repo_in_protected_folder_is_refused(
    folder: str,
    platform: Platform,
    runner: FakeRunner,
    cfg: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
    _fake_user_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _fake_user_home / folder / "bizviz"
    repo.mkdir(parents=True)
    monkeypatch.setattr("bizviz.cli.load_config", lambda env: _with_repo(load_config(env), repo))
    prompter = ScriptedPrompter()
    assert run_setup(platform, prompter, runner) == cli.EXIT_BAD_LOCATION
    assert folder in capsys.readouterr().err
    assert prompter.asked == []
    assert runner.calls == []
    assert platform.secrets.get(KC_DB_KEY) is None
    assert not cfg.db_path.exists()


def _with_web_dist(cfg: AppConfig, dist: Path) -> AppConfig:
    import dataclasses

    return dataclasses.replace(cfg, web_dist=dist)


def _with_repo(cfg: AppConfig, repo: Path) -> AppConfig:
    import dataclasses

    return dataclasses.replace(cfg, repo_dir=repo)


def test_db_without_key_exits_3(
    platform: Platform, runner: FakeRunner, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_setup(platform, fresh_prompter(platform), runner) == 0
    platform.secrets.delete(KC_DB_KEY)
    assert run_setup(platform, ScriptedPrompter(), FakeRunner()) == cli.EXIT_KEY_MISSING
    assert "--recover-key" in capsys.readouterr().err


def test_npm_failure_exits_4_without_installing_service(platform: Platform, cfg: AppConfig) -> None:
    failing = FakeRunner(fail_on="npm ci")
    assert run_setup(platform, fresh_prompter(platform), failing) == cli.EXIT_BUILD_FAILED
    assert [c for c in service(platform).calls if c[0] == "install"] == []


def test_npm_failure_restarts_previous_build(
    platform: Platform, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dist = tmp_path / "dist"
    monkeypatch.setattr(
        "bizviz.cli.load_config", lambda env: _with_web_dist(load_config(env), dist)
    )
    assert run_setup(platform, fresh_prompter(platform), FakeRunner()) == 0
    dist.mkdir()
    (dist / "index.html").write_text("old")
    assert (
        run_setup(platform, ScriptedPrompter(), FakeRunner(fail_on="npm run build"))
        == cli.EXIT_BUILD_FAILED
    )
    assert service(platform).calls[-1] == ("start",)
    assert service(platform).running


def test_warns_when_filevault_and_time_machine_off(
    runner: FakeRunner, capsys: pytest.CaptureFixture[str]
) -> None:
    platform = Platform(
        secrets=__import__("bizviz.platform.fake", fromlist=["x"]).FakeSecretStore(None),
        service=FakeServiceManager(),
        system=FakeSystemStatus(filevault=False, time_machine=False),
        trust=FakeTrustStore(),
    )
    assert run_setup(platform, fresh_prompter(platform), runner) == 0
    err = capsys.readouterr().err
    assert "FileVault is off" in err
    assert "Time Machine" in err


def test_no_desktop_export_in_isolated_environment(
    platform: Platform, runner: FakeRunner, _fake_user_home: Path
) -> None:
    assert run_setup(platform, fresh_prompter(platform), runner) == 0
    assert not (_fake_user_home / "Desktop").exists()


def test_setup_recovers_unknown_tokens_from_keychain_index(
    platform: Platform, runner: FakeRunner, cfg: AppConfig
) -> None:
    from bizviz import tokens

    platform.secrets.set(
        "plaid-item-index",
        json.dumps(
            [{"item_id": "item-x", "institution_id": "ins_1", "institution_name": "Bank X"}]
        ),
    )
    platform.secrets.set("plaid-token:item-x", "access-x")
    assert run_setup(platform, fresh_prompter(platform), runner) == 0
    conn = open_ctx(cfg, platform).connect()
    try:
        rows = conn.execute("SELECT item_id, access_token FROM items").fetchall()
    finally:
        conn.close()
    assert [(r["item_id"], r["access_token"]) for r in rows] == [("item-x", "access-x")]
    assert tokens.index_read(platform.secrets)
