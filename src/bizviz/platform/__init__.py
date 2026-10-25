import os
import sys
from pathlib import Path

from bizviz.config import AppConfig
from bizviz.platform.base import Platform


class UnsupportedPlatformError(RuntimeError):
    pass


def get_platform(cfg: AppConfig) -> Platform:
    if os.environ.get("BIZVIZ_PLATFORM") == "fake":
        from bizviz.platform.fake import (
            FakeSecretStore,
            FakeServiceManager,
            FakeSystemStatus,
            FakeTrustStore,
        )

        override = os.environ.get("BIZVIZ_FAKE_KEYCHAIN")
        # Deliberately outside the data dir, like the real Keychain.
        path = (
            Path(override)
            if override
            else cfg.home.parent / f"fake-keychain-{cfg.keychain_service}.json"
        )
        return Platform(
            secrets=FakeSecretStore(path),
            service=FakeServiceManager(),
            system=FakeSystemStatus(),
            trust=FakeTrustStore(),
        )
    if sys.platform == "darwin":
        from bizviz.platform.macos import (
            KeyringSecretStore,
            LaunchdServiceManager,
            MacSystemStatus,
            MacTrustStore,
        )

        return Platform(
            secrets=KeyringSecretStore(cfg.keychain_service),
            service=LaunchdServiceManager(cfg.launch_agent_label, cfg.profile),
            system=MacSystemStatus(),
            trust=MacTrustStore(),
        )
    raise UnsupportedPlatformError("BizViz currently supports macOS only.")
