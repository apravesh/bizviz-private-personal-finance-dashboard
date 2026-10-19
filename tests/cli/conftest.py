from collections.abc import Sequence
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

PASSWORD = "correct horse battery"


class ScriptedPrompter:
    """Answers ask()/secret() from queues; running dry is a test failure."""

    def __init__(self, asks: Sequence[str] = (), secrets: Sequence[str] = ()) -> None:
        self.asks = list(asks)
        self.secrets = list(secrets)
        self.asked: list[str] = []

    def ask(self, prompt: str) -> str:
        self.asked.append(prompt)
        assert self.asks, f"unexpected prompt: {prompt!r}"
        return self.asks.pop(0)

    def secret(self, prompt: str) -> str:
        self.asked.append(prompt)
        assert self.secrets, f"unexpected secret prompt: {prompt!r}"
        return self.secrets.pop(0)


class FakeRunner:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[tuple[str, ...], Path]] = []
        self.fail_on = fail_on

    def run(self, args: Sequence[str], cwd: Path) -> int:
        self.calls.append((tuple(args), cwd))
        return 1 if self.fail_on and self.fail_on in " ".join(args) else 0


@pytest.fixture(autouse=True)
def _fake_user_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "user"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def platform() -> Platform:
    return Platform(
        secrets=FakeSecretStore(None),
        service=FakeServiceManager(),
        system=FakeSystemStatus(),
        trust=FakeTrustStore(),
    )


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def cfg() -> AppConfig:
    return load_config("production")


def setup_prompter(*, plaid: bool = True) -> ScriptedPrompter:
    """Answers for a fresh setup; the phrase's last group is filled in by the test."""
    return ScriptedPrompter(
        asks=(["ci-id"] if plaid else []),
        secrets=(["ci-secret"] if plaid else []) + [PASSWORD, PASSWORD],
    )
