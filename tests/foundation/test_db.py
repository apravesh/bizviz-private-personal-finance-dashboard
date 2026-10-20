import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlcipher3

from bizviz.db.connection import (
    Connection,
    WrongKeyError,
    bump_data_version,
    get_meta,
    get_setting,
    now_iso,
    open_db,
    read_unit,
    set_meta,
    set_setting,
    verify_key,
    write_unit,
)
from bizviz.db.migrate import Migration, current_version, migrate

KEY = "a" * 64
OTHER_KEY = "b" * 64


def test_encrypted_file_and_keys(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    conn = open_db(path, KEY)
    conn.execute("CREATE TABLE t(x)")
    conn.close()
    assert not path.read_bytes().startswith(b"SQLite format 3")
    assert verify_key(path, KEY)
    assert not verify_key(path, OTHER_KEY)
    with pytest.raises(WrongKeyError):
        open_db(path, OTHER_KEY)
    open_db(path, KEY).close()


@pytest.mark.parametrize("bad", ["", "abc", "A" * 64, "g" * 64, "a" * 63, "a" * 65])
def test_invalid_key_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        open_db(tmp_path / "t.db", bad)


def test_connection_pragmas(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "t.db", KEY)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    conn.execute("CREATE TABLE t(x)")
    conn.execute("INSERT INTO t VALUES (1)")
    assert conn.execute("SELECT x FROM t").fetchone()["x"] == 1
    conn.close()


def test_migrate_fresh_seeds_and_skips_backup(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "t.db", KEY)
    assert current_version(conn) == 0
    calls: list[tuple[int, int]] = []
    assert migrate(conn, pre_migration_backup=lambda a, b: calls.append((a, b))) == (0, 1)
    assert calls == []
    assert get_meta(conn, "schema_version") == "1"
    assert get_meta(conn, "data_version") == "0"
    assert current_version(conn) == 1
    rows = conn.execute("SELECT category_id, name, kind, applies_to FROM categories").fetchall()
    assert len(rows) == 34
    by_id = {r["category_id"]: r for r in rows}
    assert by_id["plaid:FOOD_AND_DRINK"]["name"] == "Food & Drink"
    assert by_id["plaid:FOOD_AND_DRINK"]["applies_to"] == "spending"
    assert by_id["plaid:INCOME_SALARY"]["applies_to"] == "earning"
    assert by_id["sys:transfer"]["applies_to"] == "transfer"
    assert by_id["sys:uncategorized"]["applies_to"] == "both"
    assert by_id["sys:cc_unlinked"]["name"] == "Credit card payment (unlinked card)"
    kinds = [r["kind"] for r in rows]
    assert kinds.count("plaid") == 27 and kinds.count("system") == 7
    assert sum(r["applies_to"] == "spending" for r in rows if r["kind"] == "plaid") == 14
    assert sum(r["applies_to"] == "earning" for r in rows if r["kind"] == "plaid") == 13
    conn.close()


def test_migrate_idempotent(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "t.db", KEY)
    migrate(conn)
    assert migrate(conn) == (1, 1)
    conn.close()


def _fake_migrations(monkeypatch: pytest.MonkeyPatch, extra_sql: str) -> None:
    from bizviz.db import migrate as migrate_mod

    real = migrate_mod.discover_migrations()
    monkeypatch.setattr(
        migrate_mod,
        "discover_migrations",
        lambda: [*real, Migration(2, "0002_fake.sql", extra_sql)],
    )


def test_migrate_calls_backup_hook_once_before_applying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = open_db(tmp_path / "t.db", KEY)
    migrate(conn)
    _fake_migrations(monkeypatch, "CREATE TABLE extra(x TEXT);\n-- trailing comment\n")
    seen: list[tuple[int, int, int]] = []
    result = migrate(
        conn, pre_migration_backup=lambda a, b: seen.append((a, b, current_version(conn)))
    )
    assert result == (1, 2)
    assert seen == [(1, 2, 1)]
    assert get_meta(conn, "schema_version") == "2"
    conn.execute("SELECT * FROM extra")
    conn.close()


def test_failed_migration_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = open_db(tmp_path / "t.db", KEY)
    migrate(conn)
    _fake_migrations(monkeypatch, "CREATE TABLE extra(x TEXT);\nINSERT INTO nope VALUES (1);\n")
    with pytest.raises(sqlcipher3.OperationalError):
        migrate(conn)
    assert current_version(conn) == 1
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='extra'").fetchone() is None
    conn.close()


def test_migration_statement_splitting_handles_triggers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = open_db(tmp_path / "t.db", KEY)
    migrate(conn)
    sql = (
        "CREATE TABLE a(x);\nCREATE TABLE log(v);\n"
        "CREATE TRIGGER trg AFTER INSERT ON a BEGIN\n  INSERT INTO log VALUES ('semi;colon');\n"
        "END;\n"
    )
    _fake_migrations(monkeypatch, sql)
    migrate(conn)
    conn.execute("INSERT INTO a VALUES (1)")
    assert conn.execute("SELECT v FROM log").fetchone()[0] == "semi;colon"
    conn.close()


@pytest.fixture
def counter_conn(tmp_path: Path) -> Iterator[Connection]:
    conn = open_db(tmp_path / "t.db", KEY)
    conn.execute("CREATE TABLE c(n INTEGER)")
    conn.execute("INSERT INTO c VALUES (0)")
    yield conn
    conn.close()


def test_write_unit_no_lost_updates(counter_conn: Connection) -> None:
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(100):
                with write_unit(counter_conn):
                    n = counter_conn.execute("SELECT n FROM c").fetchone()[0]
                    counter_conn.execute("UPDATE c SET n = ?", (n + 1,))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert counter_conn.execute("SELECT n FROM c").fetchone()[0] == 200


def test_write_unit_rolls_back_on_exception(counter_conn: Connection) -> None:
    with pytest.raises(RuntimeError), write_unit(counter_conn):
        counter_conn.execute("UPDATE c SET n = 99")
        raise RuntimeError("boom")
    assert counter_conn.execute("SELECT n FROM c").fetchone()[0] == 0
    assert not counter_conn.in_transaction
    with write_unit(counter_conn):
        counter_conn.execute("UPDATE c SET n = 5")
    assert counter_conn.execute("SELECT n FROM c").fetchone()[0] == 5


def test_nested_write_unit_same_thread(counter_conn: Connection) -> None:
    with write_unit(counter_conn):
        counter_conn.execute("UPDATE c SET n = 1")
        with write_unit(counter_conn):
            counter_conn.execute("UPDATE c SET n = 2")
        assert counter_conn.in_transaction
    assert not counter_conn.in_transaction
    assert counter_conn.execute("SELECT n FROM c").fetchone()[0] == 2


def test_nested_exception_rolls_back_everything(counter_conn: Connection) -> None:
    with pytest.raises(RuntimeError), write_unit(counter_conn):
        counter_conn.execute("UPDATE c SET n = 1")
        with write_unit(counter_conn):
            counter_conn.execute("UPDATE c SET n = 2")
            raise RuntimeError("boom")
    assert counter_conn.execute("SELECT n FROM c").fetchone()[0] == 0
    with write_unit(counter_conn):
        counter_conn.execute("UPDATE c SET n = 3")
    assert counter_conn.execute("SELECT n FROM c").fetchone()[0] == 3


def test_read_unit_is_consistent_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    reader = open_db(path, KEY)
    writer = open_db(path, KEY)
    writer.execute("CREATE TABLE c(n INTEGER)")
    writer.execute("INSERT INTO c VALUES (1)")
    with read_unit(reader):
        assert reader.execute("SELECT n FROM c").fetchone()[0] == 1
        with write_unit(writer):
            writer.execute("UPDATE c SET n = 2")
        assert reader.execute("SELECT n FROM c").fetchone()[0] == 1
    assert reader.execute("SELECT n FROM c").fetchone()[0] == 2
    reader.close()
    writer.close()


def test_bump_data_version_and_helpers(db: Connection) -> None:
    assert bump_data_version(db) == 1
    assert bump_data_version(db) == 2
    assert get_meta(db, "data_version") == "2"
    with write_unit(db):
        assert bump_data_version(db) == 3
    assert get_meta(db, "data_version") == "3"
    assert get_meta(db, "missing") is None
    with write_unit(db):
        set_meta(db, "k", "v1")
        set_meta(db, "k", "v2")
        set_setting(db, "s", "1")
        set_setting(db, "s", "2")
    assert get_meta(db, "k") == "v2"
    assert get_setting(db, "s") == "2"
    assert get_setting(db, "nope") is None


def test_now_iso_format() -> None:
    import re

    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", now_iso())
