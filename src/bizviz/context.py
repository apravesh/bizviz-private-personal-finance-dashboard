from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo

from bizviz.config import AppConfig
from bizviz.db.connection import Connection, open_db
from bizviz.plaid_types import PlaidClient
from bizviz.platform.base import Platform
from bizviz.price_types import PriceSource


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _no_sync(item_id: str | None) -> None:
    return None


@dataclass
class AppContext:
    cfg: AppConfig
    platform: Platform
    db_key_hex: str
    plaid: PlaidClient | None = None
    price_source: PriceSource | None = None
    clock: Callable[[], datetime] = _utc_now
    request_sync: Callable[[str | None], None] = _no_sync
    local_tz: tzinfo | None = None

    def connect(self) -> Connection:
        return open_db(self.cfg.db_path, self.db_key_hex)

    def now(self) -> datetime:
        return self.clock()

    def tz(self) -> tzinfo:
        if self.local_tz is not None:
            return self.local_tz
        tz = datetime.now().astimezone().tzinfo
        assert tz is not None
        return tz

    def today(self) -> date:
        return self.clock().astimezone(self.tz()).date()
