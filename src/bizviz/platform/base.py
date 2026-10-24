from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class PlatformError(RuntimeError):
    """A macOS tool (launchctl, security) failed; the message is safe to show the user."""


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class ServiceManager(Protocol):
    def install(self, python_path: Path, repo_dir: Path, log_dir: Path) -> None: ...

    def uninstall(self) -> None: ...

    def is_running(self) -> bool: ...

    def stop(self) -> None: ...

    def start(self) -> None: ...


class SystemStatus(Protocol):
    def filevault_enabled(self) -> bool | None: ...

    def time_machine_configured(self) -> bool | None: ...

    def local_hostname(self) -> str: ...


class TrustStore(Protocol):
    def trust_ca(self, pem_path: Path) -> None: ...

    def untrust_ca(self, pem_path: Path) -> None: ...


@dataclass
class Platform:
    secrets: SecretStore
    service: ServiceManager
    system: SystemStatus
    trust: TrustStore
