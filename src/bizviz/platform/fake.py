import json
import os
from pathlib import Path


class FakeSecretStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._data: dict[str, str] = {}
        if path is not None and path.exists():
            self._data = dict(json.loads(path.read_text(encoding="utf-8")))

    def _save(self) -> None:
        if self.path is None:
            return
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._data, f)
        self.path.chmod(0o600)

    def get(self, name: str) -> str | None:
        return self._data.get(name)

    def set(self, name: str, value: str) -> None:
        self._data[name] = value
        self._save()

    def delete(self, name: str) -> None:
        self._data.pop(name, None)
        self._save()


class FakeServiceManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.running = False

    def install(self, python_path: Path, repo_dir: Path, log_dir: Path) -> None:
        self.calls.append(("install", str(python_path), str(repo_dir), str(log_dir)))
        self.running = True

    def uninstall(self) -> None:
        self.calls.append(("uninstall",))
        self.running = False

    def is_running(self) -> bool:
        return self.running

    def stop(self) -> None:
        self.calls.append(("stop",))
        self.running = False

    def start(self) -> None:
        self.calls.append(("start",))
        self.running = True


class FakeSystemStatus:
    def __init__(
        self,
        filevault: bool | None = True,
        time_machine: bool | None = True,
        hostname: str = "testmac.local",
    ) -> None:
        self.filevault = filevault
        self.time_machine = time_machine
        self.hostname = hostname

    def filevault_enabled(self) -> bool | None:
        return self.filevault

    def time_machine_configured(self) -> bool | None:
        return self.time_machine

    def local_hostname(self) -> str:
        return self.hostname


class FakeTrustStore:
    def __init__(self) -> None:
        self.trusted: list[Path] = []

    def trust_ca(self, pem_path: Path) -> None:
        self.trusted.append(pem_path)

    def untrust_ca(self, pem_path: Path) -> None:
        self.trusted = [p for p in self.trusted if p != pem_path]
