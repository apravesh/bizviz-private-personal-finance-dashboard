"""Service bootstrap: everything `serve` needs, built once per process."""

import json
from dataclasses import dataclass

from bizviz import __version__
from bizviz.auth import LoginRateLimiter
from bizviz.backup import make_premigration_hook
from bizviz.config import KC_DB_KEY, AppConfig, Env, load_config
from bizviz.context import AppContext
from bizviz.db.connection import WrongKeyError, open_db
from bizviz.db.migrate import migrate
from bizviz.logging_setup import configure_logging
from bizviz.plaid_client import build_plaid_client
from bizviz.platform import get_platform
from bizviz.prices import YahooPriceSource
from bizviz.sync.scheduler import Scheduler
from bizviz.tokens import recover_tokens

SETUP_HINT = "Run `bizviz setup` (or `bizviz setup --recover-key`)."


@dataclass
class Runtime:
    ctx: AppContext
    rate_limiter: LoginRateLimiter
    scheduler: Scheduler
    app_version: str


def read_app_version(cfg: AppConfig) -> str:
    try:
        data = json.loads((cfg.web_dist / "version.json").read_text(encoding="utf-8"))
        return str(data["app_version"])
    except (OSError, ValueError, KeyError, TypeError):
        return f"{__version__}+unknown"


def make_runtime(ctx: AppContext) -> Runtime:
    scheduler = Scheduler(ctx)
    ctx.request_sync = scheduler.request_sync
    return Runtime(
        ctx=ctx,
        rate_limiter=LoginRateLimiter(),
        scheduler=scheduler,
        app_version=read_app_version(ctx.cfg),
    )


def build_runtime(env: Env) -> Runtime:
    cfg = load_config(env)
    configure_logging(cfg)
    platform = get_platform(cfg)
    db_key = platform.secrets.get(KC_DB_KEY)
    if not db_key:
        raise SystemExit(f"The database key is missing from the Keychain. {SETUP_HINT}")
    if not cfg.db_path.exists():
        raise SystemExit(f"No database found. {SETUP_HINT}")
    ctx = AppContext(
        cfg=cfg,
        platform=platform,
        db_key_hex=db_key,
        plaid=build_plaid_client(cfg, platform.secrets),
        price_source=YahooPriceSource(),
    )
    try:
        conn = open_db(cfg.db_path, db_key)
    except WrongKeyError:
        raise SystemExit(f"The Keychain key does not open the database. {SETUP_HINT}") from None
    try:
        migrate(conn, pre_migration_backup=make_premigration_hook(ctx, conn))
    finally:
        conn.close()
    recover_tokens(ctx)
    return make_runtime(ctx)
