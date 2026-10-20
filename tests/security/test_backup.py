import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from bizviz.backup import (
    InvalidPhrase,
    RestoreRefused,
    daily_backup_if_needed,
    decode_recovery_phrase,
    encode_recovery_phrase,
    make_backup,
    make_premigration_hook,
    premigration_backup,
    recover_key,
    restore_backup,
)
from bizviz.config import KC_DB_KEY, AppConfig
from bizviz.context import AppContext
from bizviz.db.connection import (
    Connection,
    WrongKeyError,
    get_meta,
    open_db,
    set_setting,
    verify_key,
    write_unit,
)
from bizviz.db.migrate import migrate
from bizviz.platform.fake import FakeSecretStore

NOW = datetime(2026, 10, 15, 15, 0, 0, tzinfo=UTC)
OTHER_KEY = "b" * 64


def _seed(conn: Connection, value: str = "v") -> None:
    with write_unit(conn):
        set_setting(conn, "marker", value)
        for i in range(25):
            set_setting(conn, f"k{i}", "x" * 200)


def test_make_backup_opens_with_same_key_only(db: Connection, db_key: str, tmp_path: Path) -> None:
    _seed(db)
    dest = tmp_path / "copy.db"
    make_backup(db, db_key, dest)
    assert dest.exists()
    assert not list(tmp_path.glob("copy.db.*"))
    with pytest.raises(WrongKeyError):
        open_db(dest, OTHER_KEY)
    copy = open_db(dest, db_key)
    try:
        assert get_meta(copy, "schema_version") == get_meta(db, "schema_version")
        for table in ("settings", "meta", "items", "device_sessions"):
            q = f"SELECT count(*) FROM {table}"
            assert copy.execute(q).fetchone()[0] == db.execute(q).fetchone()[0]
        assert copy.execute("SELECT value FROM settings WHERE key='marker'").fetchone()[0] == "v"
    finally:
        copy.close()
    db.execute("SELECT 1").fetchone()
    assert db.execute("SELECT count(*) FROM settings").fetchone()[0] == 26


def test_make_backup_cleans_up_on_failure(db: Connection, db_key: str, tmp_path: Path) -> None:
    key = db_key
    dest = tmp_path / "copy.db"
    with pytest.raises(ValueError):
        make_backup(db, "nothex", dest)
    assert not dest.exists()
    assert not list(tmp_path.glob("copy.db*"))
    missing_dir = tmp_path / "nope" / "copy.db"
    with pytest.raises(Exception, match="unable to open"):
        make_backup(db, key, missing_dir)
    assert [r[1] for r in db.execute("PRAGMA database_list")] == ["main"]
    make_backup(db, key, dest)
    assert dest.exists()


def _make_ctx_backups(ctx: AppContext, names: list[str]) -> None:
    ctx.cfg.backups_dir.mkdir(parents=True, exist_ok=True)
    for n in names:
        (ctx.cfg.backups_dir / n).write_bytes(b"x")


def test_daily_backup_once_per_date_and_retention(app_ctx: AppContext) -> None:
    backups = app_ctx.cfg.backups_dir
    _make_ctx_backups(
        app_ctx,
        [f"bizviz-2026-10-{d:02d}.db" for d in range(1, 9)]
        + [
            "bizviz-premigrate-1-to-2-20260101000000.db",
            "bizviz-premigrate-1-to-2-20250101000000.db",
        ],
    )
    first = daily_backup_if_needed(app_ctx, date(2026, 10, 15))
    assert first == backups / "bizviz-2026-10-15.db"
    assert verify_key(first, app_ctx.db_key_hex)
    assert daily_backup_if_needed(app_ctx, date(2026, 10, 15)) is None
    assert not list(backups.glob("*.tmp"))
    daily = sorted(p.name for p in backups.glob("bizviz-????-??-??.db"))
    assert daily == [f"bizviz-2026-10-{d:02d}.db" for d in (3, 4, 5, 6, 7, 8, 15)]
    assert len(list(backups.glob("bizviz-premigrate-*"))) == 2


def test_daily_backup_creates_missing_dir(app_ctx: AppContext) -> None:
    app_ctx.cfg.backups_dir.rmdir()
    path = daily_backup_if_needed(app_ctx, date(2026, 10, 15))
    assert path is not None and path.exists()


def test_premigration_backup_retention(app_ctx: AppContext, db: Connection) -> None:
    backups = app_ctx.cfg.backups_dir
    _make_ctx_backups(app_ctx, ["bizviz-2026-10-01.db"])
    paths = []
    for i in range(5):
        paths.append(premigration_backup(app_ctx, db, 9 + i, 10 + i, NOW + timedelta(minutes=i)))
    assert paths[-1].name == "bizviz-premigrate-13-to-14-20261015150400.db"
    remaining = sorted(p.name for p in backups.glob("bizviz-premigrate-*"))
    assert remaining == [p.name for p in paths[-3:]]
    assert (backups / "bizviz-2026-10-01.db").exists()
    assert verify_key(paths[-1], app_ctx.db_key_hex)


def test_premigration_retention_orders_by_timestamp_not_version(
    app_ctx: AppContext, db: Connection
) -> None:
    for i, (a, b) in enumerate([(9, 10), (10, 11), (11, 12), (1, 2)]):
        premigration_backup(app_ctx, db, a, b, NOW + timedelta(minutes=i))
    names = sorted(p.name for p in app_ctx.cfg.backups_dir.glob("bizviz-premigrate-*"))
    assert len(names) == 3
    assert not any("9-to-10" in n for n in names)


def test_premigration_hook_adapts_to_migrate(app_ctx: AppContext, db: Connection) -> None:
    hook = make_premigration_hook(app_ctx, db)
    hook(1, 2)
    files = list(app_ctx.cfg.backups_dir.glob("bizviz-premigrate-1-to-2-20261015150000.db"))
    assert len(files) == 1
    assert migrate(db, pre_migration_backup=hook) == (1, 1)
    assert len(list(app_ctx.cfg.backups_dir.glob("bizviz-premigrate-*"))) == 1


def test_recovery_phrase_format_and_round_trip() -> None:
    key = "00ff" * 16
    phrase = encode_recovery_phrase(key)
    assert re.fullmatch(r"([A-Z2-7]{4}-){12}[A-Z2-7]{4}", phrase)
    assert "=" not in phrase
    assert decode_recovery_phrase(phrase) == key
    assert decode_recovery_phrase(phrase.lower()) == key
    assert decode_recovery_phrase(phrase.replace("-", "")) == key
    assert decode_recovery_phrase(phrase.replace("-", " ")) == key
    assert decode_recovery_phrase("  " + phrase + "\n") == key


def test_recovery_phrase_random_keys(db_key: str) -> None:
    assert decode_recovery_phrase(encode_recovery_phrase(db_key)) == db_key


@pytest.mark.parametrize("bad", ["", "AAAA-BBBB", "!!!!", "A" * 51, "A" * 56])
def test_decode_rejects_invalid(bad: str) -> None:
    with pytest.raises(InvalidPhrase) as exc:
        decode_recovery_phrase(bad)
    assert isinstance(exc.value, ValueError)


@pytest.fixture
def backup_file(app_ctx: AppContext, db: Connection) -> Path:
    _seed(db, "from-backup")
    dest = app_ctx.cfg.backups_dir / "bizviz-2026-10-14.db"
    make_backup(db, app_ctx.db_key_hex, dest)
    return dest


def _snapshot(cfg: AppConfig) -> dict[str, bytes]:
    return {
        str(p.relative_to(cfg.home)): p.read_bytes() for p in cfg.home.rglob("*") if p.is_file()
    }


def test_restore_refused_without_force(app_ctx: AppContext, backup_file: Path, db_key: str) -> None:
    secrets = app_ctx.platform.secrets
    secrets.set(KC_DB_KEY, db_key)
    before = _snapshot(app_ctx.cfg)
    with pytest.raises(RestoreRefused):
        restore_backup(
            app_ctx.cfg, secrets, backup_file, encode_recovery_phrase(db_key), force=False, now=NOW
        )
    assert _snapshot(app_ctx.cfg) == before
    assert secrets.get(KC_DB_KEY) == db_key
    assert not any(k.startswith("db-key.bak") for k in secrets._data)  # type: ignore[attr-defined]


def test_restore_wrong_phrase_changes_nothing(
    app_ctx: AppContext, backup_file: Path, db_key: str
) -> None:
    secrets = app_ctx.platform.secrets
    secrets.set(KC_DB_KEY, db_key)
    before = _snapshot(app_ctx.cfg)
    for phrase in (encode_recovery_phrase(OTHER_KEY), "garbage"):
        with pytest.raises(InvalidPhrase):
            restore_backup(app_ctx.cfg, secrets, backup_file, phrase, force=True, now=NOW)
    assert _snapshot(app_ctx.cfg) == before
    assert secrets.get(KC_DB_KEY) == db_key


def test_restore_missing_backup_file(app_ctx: AppContext, tmp_path: Path, db_key: str) -> None:
    missing = tmp_path / "nope.db"
    with pytest.raises(FileNotFoundError):
        restore_backup(
            app_ctx.cfg,
            app_ctx.platform.secrets,
            missing,
            encode_recovery_phrase(db_key),
            force=True,
            now=NOW,
        )
    assert not missing.exists()


def test_restore_rejects_backup_without_schema_version(
    app_ctx: AppContext, tmp_path: Path, db_key: str
) -> None:
    bogus = tmp_path / "bogus.db"
    conn = open_db(bogus, db_key)
    conn.execute("CREATE TABLE other(x)")
    conn.close()
    with pytest.raises(ValueError, match="backup_missing_schema_version"):
        restore_backup(
            app_ctx.cfg,
            app_ctx.platform.secrets,
            bogus,
            encode_recovery_phrase(db_key),
            force=True,
            now=NOW,
        )


def test_restore_force_moves_everything_and_writes_key_last(
    app_ctx: AppContext, backup_file: Path, db_key: str, tmp_path: Path
) -> None:
    cfg = app_ctx.cfg
    new_key = OTHER_KEY
    external = tmp_path / "external.db"
    conn = open_db(external, new_key)
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO meta VALUES('schema_version','1')")
    conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO settings VALUES('marker','restored')")
    conn.close()

    secrets = app_ctx.platform.secrets
    assert isinstance(secrets, FakeSecretStore)
    secrets.set(KC_DB_KEY, db_key)
    old_db_bytes = cfg.db_path.read_bytes()
    wal = cfg.db_path.with_name("bizviz.db-wal")
    shm = cfg.db_path.with_name("bizviz.db-shm")
    wal.write_bytes(b"wal")
    shm.write_bytes(b"shm")
    order: list[str] = []
    original_set = secrets.set

    def spy(name: str, value: str) -> None:
        order.append(name)
        original_set(name, value)

    secrets.set = spy  # type: ignore[method-assign]

    result = restore_backup(
        cfg, secrets, external, encode_recovery_phrase(new_key), force=True, now=NOW
    )

    ts = "20261015150000"
    assert result.moved_db == cfg.home / f"bizviz.db.bak-{ts}"
    assert result.moved_db.read_bytes() == old_db_bytes
    assert (cfg.home / f"bizviz.db-wal.bak-{ts}").read_bytes() == b"wal"
    assert (cfg.home / f"bizviz.db-shm.bak-{ts}").read_bytes() == b"shm"
    assert not wal.exists() and not shm.exists()
    assert result.moved_backups == cfg.home / f"backups.bak-{ts}"
    assert (result.moved_backups / backup_file.name).exists()
    assert cfg.backups_dir.is_dir() and list(cfg.backups_dir.iterdir()) == []
    assert result.old_key_entry == f"db-key.bak-{ts}"
    assert secrets.get(f"db-key.bak-{ts}") == db_key
    assert secrets.get(KC_DB_KEY) == new_key
    assert order == [f"db-key.bak-{ts}", KC_DB_KEY]
    assert verify_key(result.moved_db, db_key)

    restored = open_db(cfg.db_path, new_key)
    try:
        assert restored.execute("SELECT value FROM settings").fetchone()[0] == "restored"
    finally:
        restored.close()


def test_restore_from_file_inside_backups_dir(
    app_ctx: AppContext, backup_file: Path, db_key: str
) -> None:
    cfg = app_ctx.cfg
    secrets = app_ctx.platform.secrets
    secrets.set(KC_DB_KEY, OTHER_KEY)
    result = restore_backup(
        cfg, secrets, backup_file, encode_recovery_phrase(db_key), force=True, now=NOW
    )
    assert secrets.get(KC_DB_KEY) == db_key
    assert result.old_key_entry is not None
    assert secrets.get(result.old_key_entry) == OTHER_KEY
    restored = open_db(cfg.db_path, db_key)
    try:
        assert restored.execute("SELECT value FROM settings WHERE key='marker'").fetchone()[0] == (
            "from-backup"
        )
    finally:
        restored.close()


def test_restore_without_existing_db(app_ctx: AppContext, backup_file: Path, db_key: str) -> None:
    cfg = app_ctx.cfg
    backup_copy = backup_file.parent.parent / "outside.db"
    backup_copy.write_bytes(backup_file.read_bytes())
    cfg.db_path.unlink()
    secrets = FakeSecretStore(None)
    result = restore_backup(
        cfg, secrets, backup_copy, encode_recovery_phrase(db_key), force=False, now=NOW
    )
    assert result.moved_db is None and result.moved_backups is None
    assert result.old_key_entry is None
    assert secrets.get(KC_DB_KEY) == db_key
    assert verify_key(cfg.db_path, db_key)
    assert backup_file.exists()


def test_recover_key(app_ctx: AppContext, db_key: str) -> None:
    secrets = FakeSecretStore(None)
    before = _snapshot(app_ctx.cfg)
    with pytest.raises(InvalidPhrase):
        recover_key(app_ctx.cfg, secrets, encode_recovery_phrase(OTHER_KEY))
    with pytest.raises(InvalidPhrase):
        recover_key(app_ctx.cfg, secrets, "nonsense")
    assert secrets.get(KC_DB_KEY) is None
    recover_key(app_ctx.cfg, secrets, encode_recovery_phrase(db_key))
    assert secrets.get(KC_DB_KEY) == db_key
    assert _snapshot(app_ctx.cfg).keys() == before.keys()


def test_recover_key_without_database(cfg: AppConfig, db_key: str) -> None:
    secrets = FakeSecretStore(None)
    with pytest.raises(FileNotFoundError):
        recover_key(cfg, secrets, encode_recovery_phrase(db_key))
    assert not cfg.db_path.exists()
