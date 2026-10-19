import os
import plistlib
import subprocess
from pathlib import Path

import keyring
from keyring.errors import PasswordDeleteError

from bizviz.config import LAUNCH_AGENT_LABEL

_TIMEOUT = 30


def _run(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(args, check=False, capture_output=True, text=True, timeout=_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None


class KeyringSecretStore:
    def __init__(self, service: str) -> None:
        self.service = service

    def get(self, name: str) -> str | None:
        return keyring.get_password(self.service, name)

    def set(self, name: str, value: str) -> None:
        keyring.set_password(self.service, name, value)

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(self.service, name)
        except PasswordDeleteError:
            pass


class LaunchdServiceManager:
    def __init__(self, label: str = LAUNCH_AGENT_LABEL) -> None:
        self.label = label

    @property
    def plist_path(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{self.label}.plist"

    @property
    def _domain(self) -> str:
        return f"gui/{os.getuid()}"

    @property
    def _target(self) -> str:
        return f"{self._domain}/{self.label}"

    def install(self, python_path: Path, repo_dir: Path, log_dir: Path) -> None:
        plist = {
            "Label": self.label,
            "ProgramArguments": [str(python_path), "-m", "bizviz", "serve"],
            "WorkingDirectory": str(repo_dir),
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(log_dir / "launchd.out.log"),
            "StandardErrorPath": str(log_dir / "launchd.err.log"),
        }
        self.plist_path.parent.mkdir(parents=True, exist_ok=True)
        with self.plist_path.open("wb") as f:
            plistlib.dump(plist, f)
        self.stop()
        self.start()

    def uninstall(self) -> None:
        self.stop()
        self.plist_path.unlink(missing_ok=True)

    def is_running(self) -> bool:
        result = _run(["launchctl", "print", self._target])
        return result is not None and result.returncode == 0 and "state = running" in result.stdout

    def stop(self) -> None:
        _run(["launchctl", "bootout", self._target])

    def start(self) -> None:
        _run(["launchctl", "bootstrap", self._domain, str(self.plist_path)])


class MacSystemStatus:
    def filevault_enabled(self) -> bool | None:
        result = _run(["fdesetup", "status"])
        if result is None or result.returncode != 0:
            return None
        if "FileVault is On" in result.stdout:
            return True
        if "FileVault is Off" in result.stdout:
            return False
        return None

    def time_machine_configured(self) -> bool | None:
        result = _run(["tmutil", "destinationinfo"])
        if result is None or result.returncode != 0:
            return None
        if "No destinations configured" in result.stdout:
            return False
        if any(line.startswith("Name") for line in result.stdout.splitlines()):
            return True
        return None

    def local_hostname(self) -> str:
        result = _run(["scutil", "--get", "LocalHostName"])
        name = result.stdout.strip() if result and result.returncode == 0 else ""
        return f"{name}.local" if name else "localhost"


class MacTrustStore:
    def trust_ca(self, pem_path: Path) -> None:
        keychain = Path.home() / "Library" / "Keychains" / "login.keychain-db"
        result = _run(
            ["security", "add-trusted-cert", "-r", "trustRoot", "-k", str(keychain), str(pem_path)]
        )
        if result is None or result.returncode != 0:
            detail = result.stderr.strip() if result else "command failed"
            raise RuntimeError(f"Could not trust the CA certificate: {detail}")

    def untrust_ca(self, pem_path: Path) -> None:
        _run(["security", "remove-trusted-cert", str(pem_path)])
