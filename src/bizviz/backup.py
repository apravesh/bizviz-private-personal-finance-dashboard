import base64
import binascii
import logging
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import sqlcipher3

from bizviz.config import KC_DB_KEY, AppConfig
from bizviz.context import AppContext
from bizviz.db import connection as db_connection
from bizviz.db.connection import Connection, verify_key
from bizviz.platform.base import SecretStore

log = logging.getLogger(__name__)

DAILY_KEEP = 7
PREMIGRATE_KEEP = 3
_KEY_RE = re.compile(r"[0-9a-f]{64}")
_PREMIGRATE_RE = re.compile(r"bizviz-premigrate-(\d+)-to-(\d+)-(\d{14})\.db")


class RestoreRefused(Exception):
    """A database already exists and force was not given."""


class InvalidPhrase(ValueError):
    """The recovery phrase is malformed or does not open the database."""


@dataclass
class RestoreResult:
    moved_db: Path | None
    moved_backups: Path | None
    old_key_entry: str | None


def encode_recovery_phrase(key_hex: str) -> str:
    encoded = base64.b32encode(bytes.fromhex(key_hex)).decode("ascii").rstrip("=")
    return "-".join(encoded[i : i + 4] for i in range(0, len(encoded), 4))


def decode_recovery_phrase(phrase: str) -> str:
    cleaned = re.sub(r"[\s-]", "", phrase).upper()
    padded = cleaned + "=" * (-len(cleaned) % 8)
    try:
        raw = base64.b32decode(padded)
    except (binascii.Error, ValueError) as exc:
        raise InvalidPhrase("invalid_phrase") from exc
    if len(raw) != 32:
        raise InvalidPhrase("invalid_phrase")
    return raw.hex()


def make_backup(conn: Connection, key_hex: str, dest: Path) -> None:
    if not _KEY_RE.fullmatch(key_hex):
        raise ValueError("key_hex must be 64 lowercase hex characters")
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        with db_connection.WRITE_LOCK:
            conn.execute(f"ATTACH DATABASE ? AS bk KEY \"x'{key_hex}'\"", (str(tmp),))
            try:
                conn.execute("SELECT sqlcipher_export('bk')").fetchone()
            finally:
                conn.execute("DETACH DATABASE bk")
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        for suffix in ("-wal", "-shm"):
            Path(str(tmp) + suffix).unlink(missing_ok=True)


def _prune(files: list[Path], keep: int) -> None:
    for old in files[: max(0, len(files) - keep)]:
        old.unlink(missing_ok=True)


def daily_backup_if_needed(ctx: AppContext, today: date) -> Path | None:
    backups_dir = ctx.cfg.backups_dir
    backups_dir.mkdir(parents=True, exist_ok=True)
    dest = backups_dir / f"bizviz-{today.isoformat()}.db"
    if dest.exists():
        return None
    conn = ctx.connect()
    try:
        make_backup(conn, ctx.db_key_hex, dest)
    finally:
        conn.close()
    _prune(sorted(backups_dir.glob("bizviz-????-??-??.db"), key=lambda p: p.name), DAILY_KEEP)
    return dest


def _premigrate_sort_key(path: Path) -> tuple[str, int, int]:
    m = _PREMIGRATE_RE.fullmatch(path.name)
    assert m is not None
    return m.group(3), int(m.group(2)), int(m.group(1))


def premigration_backup(
    ctx: AppContext, conn: Connection, from_v: int, to_v: int, now: datetime
) -> Path:
    backups_dir = ctx.cfg.backups_dir
    backups_dir.mkdir(parents=True, exist_ok=True)
    dest = backups_dir / f"bizviz-premigrate-{from_v}-to-{to_v}-{now:%Y%m%d%H%M%S}.db"
    make_backup(conn, ctx.db_key_hex, dest)
    existing = [
        p for p in backups_dir.glob("bizviz-premigrate-*.db") if _PREMIGRATE_RE.fullmatch(p.name)
    ]
    _prune(sorted(existing, key=_premigrate_sort_key), PREMIGRATE_KEEP)
    return dest


def make_premigration_hook(ctx: AppContext, conn: Connection) -> Callable[[int, int], None]:
    def hook(from_v: int, to_v: int) -> None:
        premigration_backup(ctx, conn, from_v, to_v, ctx.now())

    return hook


def _verify_backup_file(backup_file: Path, key_hex: str) -> None:
    if not backup_file.is_file():
        raise FileNotFoundError(str(backup_file))
    # Read-only so the backup is not rewritten (open_db would switch it to WAL mode).
    conn = sqlcipher3.connect(f"{backup_file.resolve().as_uri()}?mode=ro", uri=True)
    try:
        conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlcipher3.DatabaseError as exc:
            raise InvalidPhrase("phrase_does_not_open_backup") from exc
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        except sqlcipher3.DatabaseError as exc:
            raise ValueError("backup_missing_schema_version") from exc
        if row is None:
            raise ValueError("backup_missing_schema_version")
    finally:
        conn.close()


def _sidecars(db_path: Path) -> list[tuple[Path, str]]:
    return [(Path(str(db_path) + suffix), suffix) for suffix in ("-wal", "-shm")]


def restore_backup(
    cfg: AppConfig,
    secrets: SecretStore,
    backup_file: Path,
    phrase: str,
    *,
    force: bool,
    now: datetime,
) -> RestoreResult:
    key = decode_recovery_phrase(phrase)
    _verify_backup_file(backup_file, key)
    db_exists = cfg.db_path.exists()
    if db_exists and not force:
        raise RestoreRefused("database_exists")

    moved_db: Path | None = None
    moved_backups: Path | None = None
    old_key_entry: str | None = None
    source = backup_file
    ts = f"{now:%Y%m%d%H%M%S}"

    if db_exists:
        moved_db = cfg.db_path.with_name(f"{cfg.db_path.name}.bak-{ts}")
        shutil.move(cfg.db_path, moved_db)
    for sidecar, suffix in _sidecars(cfg.db_path):
        if sidecar.exists():
            shutil.move(sidecar, cfg.db_path.with_name(f"{cfg.db_path.name}{suffix}.bak-{ts}"))
    if db_exists and cfg.backups_dir.exists():
        moved_backups = cfg.backups_dir.with_name(f"{cfg.backups_dir.name}.bak-{ts}")
        shutil.move(cfg.backups_dir, moved_backups)
        if backup_file.resolve().is_relative_to(cfg.backups_dir.resolve()):
            source = moved_backups / backup_file.resolve().relative_to(cfg.backups_dir.resolve())
    if db_exists:
        old_key = secrets.get(KC_DB_KEY)
        if old_key is not None:
            old_key_entry = f"{KC_DB_KEY}.bak-{ts}"
            secrets.set(old_key_entry, old_key)

    shutil.copy2(source, cfg.db_path)
    cfg.db_path.chmod(0o600)
    cfg.backups_dir.mkdir(parents=True, exist_ok=True)
    secrets.set(KC_DB_KEY, key)
    return RestoreResult(
        moved_db=moved_db, moved_backups=moved_backups, old_key_entry=old_key_entry
    )


def recover_key(cfg: AppConfig, secrets: SecretStore, phrase: str) -> None:
    key = decode_recovery_phrase(phrase)
    if not cfg.db_path.is_file():
        raise FileNotFoundError(str(cfg.db_path))
    if not verify_key(cfg.db_path, key):
        raise InvalidPhrase("phrase_does_not_open_database")
    secrets.set(KC_DB_KEY, key)
