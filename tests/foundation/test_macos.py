import plistlib
import subprocess
from pathlib import Path

import pytest

from bizviz.platform import macos


def _result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def _patch_run(
    monkeypatch: pytest.MonkeyPatch, result: subprocess.CompletedProcess[str] | None
) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake(args: list[str]) -> subprocess.CompletedProcess[str] | None:
        calls.append(args)
        return result

    monkeypatch.setattr(macos, "_run", fake)
    return calls


@pytest.mark.parametrize(
    ("stdout", "code", "expected"),
    [
        ("FileVault is On.\n", 0, True),
        ("FileVault is Off.\n", 0, False),
        ("garbage", 0, None),
        ("FileVault is On.", 1, None),
    ],
)
def test_filevault(
    monkeypatch: pytest.MonkeyPatch, stdout: str, code: int, expected: bool | None
) -> None:
    _patch_run(monkeypatch, _result(stdout, code))
    assert macos.MacSystemStatus().filevault_enabled() is expected


@pytest.mark.parametrize(
    ("stdout", "code", "expected"),
    [
        ("No destinations configured\n", 0, False),
        ("====\nName          : Backup\nKind          : Local\n", 0, True),
        ("", 1, None),
    ],
)
def test_time_machine(
    monkeypatch: pytest.MonkeyPatch, stdout: str, code: int, expected: bool | None
) -> None:
    _patch_run(monkeypatch, _result(stdout, code))
    assert macos.MacSystemStatus().time_machine_configured() is expected


def test_tool_missing_gives_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, None)
    assert macos.MacSystemStatus().filevault_enabled() is None
    assert macos.MacSystemStatus().local_hostname() == "localhost"


def test_local_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_run(monkeypatch, _result("Mymac\n"))
    assert macos.MacSystemStatus().local_hostname() == "Mymac.local"
    assert calls == [["scutil", "--get", "LocalHostName"]]


def test_trust_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = _patch_run(monkeypatch, _result())
    macos.MacTrustStore().trust_ca(Path("/ca.pem"))
    macos.MacTrustStore().untrust_ca(Path("/ca.pem"))
    keychain = str(tmp_path / "Library" / "Keychains" / "login.keychain-db")
    assert calls[0] == [
        "security",
        "add-trusted-cert",
        "-r",
        "trustRoot",
        "-k",
        keychain,
        "/ca.pem",
    ]
    assert calls[1] == ["security", "remove-trusted-cert", "/ca.pem"]


def _patch_launchctl(
    monkeypatch: pytest.MonkeyPatch,
    *,
    loaded_polls: int = 0,
    bootstrap_codes: tuple[int, ...] = (0,),
) -> list[list[str]]:
    """Fake launchctl: `print` reports loaded for `loaded_polls` calls after each bootout,
    and successive bootstraps exit with `bootstrap_codes` (the last one repeats)."""
    calls: list[list[str]] = []
    state = {"loaded": 0, "bootstraps": 0}

    def fake(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        verb = args[1]
        if verb == "bootout":
            state["loaded"] = loaded_polls
            return _result()
        if verb == "print":
            if state["loaded"] > 0:
                state["loaded"] -= 1
                return _result("\tstate = SIGTERMed\n")
            return _result(returncode=113)
        if verb == "bootstrap":
            i = min(state["bootstraps"], len(bootstrap_codes) - 1)
            state["bootstraps"] += 1
            code = bootstrap_codes[i]
            stderr = "Bootstrap failed: 5" if code else ""
            return subprocess.CompletedProcess(args, code, "", stderr)
        return _result()

    monkeypatch.setattr(macos, "_run", fake)
    monkeypatch.setattr(macos.time, "sleep", lambda _: None)
    return calls


def test_launchd_install_and_uninstall(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = _patch_launchctl(monkeypatch)
    svc = macos.LaunchdServiceManager()
    svc.install(Path("/repo/.venv/bin/python"), Path("/repo"), Path("/logs"))
    plist = tmp_path / "Library" / "LaunchAgents" / "com.bizviz.agent.plist"
    data = plistlib.loads(plist.read_bytes())
    assert data["Label"] == "com.bizviz.agent"
    assert data["ProgramArguments"] == ["/repo/.venv/bin/python", "-m", "bizviz", "serve"]
    assert data["WorkingDirectory"] == "/repo"
    assert data["RunAtLoad"] is True and data["KeepAlive"] is True
    assert data["StandardOutPath"] == "/logs/launchd.out.log"
    assert data["StandardErrorPath"] == "/logs/launchd.err.log"
    assert [c[1] for c in calls] == ["bootout", "print", "bootstrap"]
    assert calls[2][3] == str(plist)
    svc.uninstall()
    assert not plist.exists()


def test_launchd_stop_waits_until_unloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_launchctl(monkeypatch, loaded_polls=3)
    macos.LaunchdServiceManager().stop()
    assert [c[1] for c in calls] == ["bootout", "print", "print", "print", "print"]


def test_launchd_start_retries_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_launchctl(monkeypatch, bootstrap_codes=(5, 5, 0))
    macos.LaunchdServiceManager().start()
    assert [c[1] for c in calls] == ["bootstrap"] * 3


def test_launchd_start_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_launchctl(monkeypatch, bootstrap_codes=(5,))
    with pytest.raises(macos.PlatformError, match="Bootstrap failed: 5"):
        macos.LaunchdServiceManager().start()


def test_launchd_is_running(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = macos.LaunchdServiceManager()
    _patch_run(monkeypatch, _result("\tstate = running\n"))
    assert svc.is_running()
    _patch_run(monkeypatch, _result("\tstate = waiting\n"))
    assert not svc.is_running()
    _patch_run(monkeypatch, _result("state = running", 113))
    assert not svc.is_running()


def test_keyring_store_delete_ignores_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    store: dict[tuple[str, str], str] = {}

    def delete(service: str, name: str) -> None:
        if (service, name) not in store:
            raise macos.PasswordDeleteError("missing")
        del store[(service, name)]

    monkeypatch.setattr(macos.keyring, "get_password", lambda s, n: store.get((s, n)))
    monkeypatch.setattr(macos.keyring, "set_password", lambda s, n, v: store.update({(s, n): v}))
    monkeypatch.setattr(macos.keyring, "delete_password", delete)
    secrets = macos.KeyringSecretStore("BizViz")
    secrets.set("a", "1")
    assert secrets.get("a") == "1"
    secrets.delete("a")
    secrets.delete("a")
    assert secrets.get("a") is None


def test_launchd_profile_runs_its_own_serve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = _patch_launchctl(monkeypatch)
    svc = macos.LaunchdServiceManager("com.bizviz.agent.wife", "wife")
    svc.install(Path("/repo/.venv/bin/python"), Path("/repo"), Path("/logs"))
    plist = tmp_path / "Library" / "LaunchAgents" / "com.bizviz.agent.wife.plist"
    data = plistlib.loads(plist.read_bytes())
    assert data["Label"] == "com.bizviz.agent.wife"
    assert data["ProgramArguments"] == [
        "/repo/.venv/bin/python",
        "-m",
        "bizviz",
        "--profile",
        "wife",
        "serve",
    ]
    assert calls[0][2].endswith("/com.bizviz.agent.wife")
