import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlcipher3
from sqlcipher3 import dbapi2

# sqlcipher3 ships no usable type information; keep one alias so call sites stay readable.
type Connection = Any

_KEY_RE = re.compile(r"[0-9a-f]{64}")

# Process-wide: SQLite allows a single writer, so all write units serialize here.
WRITE_LOCK = threading.RLock()
_depth = threading.local()


class WrongKeyError(Exception):
    """The database could not be decrypted with the given key."""


def open_db(path: Path, key_hex: str) -> Connection:
    if not _KEY_RE.fullmatch(key_hex):
        raise ValueError("key_hex must be 64 lowercase hex characters")
    conn = sqlcipher3.connect(str(path), isolation_level=None, check_same_thread=False)
    try:
        conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlcipher3.DatabaseError as exc:
            raise WrongKeyError("database could not be opened with the given key") from exc
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
    except BaseException:
        conn.close()
        raise
    conn.row_factory = dbapi2.Row
    return conn


def verify_key(path: Path, key_hex: str) -> bool:
    try:
        conn = open_db(path, key_hex)
    except WrongKeyError:
        return False
    conn.close()
    return True


@contextmanager
def write_unit(conn: Connection) -> Iterator[Connection]:
    """Run a transaction under the process-wide write lock.

    Never perform network I/O inside a write unit, and never open a second connection inside
    one on the same thread: nesting depth is tracked per thread, so a second connection would
    block on SQLITE_BUSY. Nested write units on the same thread join the outer transaction
    (no second BEGIN); an exception anywhere rolls back the whole outermost unit.
    """
    with WRITE_LOCK:
        depth = getattr(_depth, "value", 0)
        if depth > 0:
            _depth.value = depth + 1
            try:
                yield conn
            finally:
                _depth.value = depth
            return
        conn.execute("BEGIN IMMEDIATE")
        _depth.value = 1
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
        finally:
            _depth.value = 0


@contextmanager
def read_unit(conn: Connection) -> Iterator[Connection]:
    """Run reads in one deferred transaction so they see a consistent WAL snapshot."""
    conn.execute("BEGIN")
    try:
        yield conn
    finally:
        conn.execute("COMMIT")


def get_meta(conn: Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def set_meta(conn: Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def bump_data_version(conn: Connection) -> int:
    with write_unit(conn):
        current = int(get_meta(conn, "data_version") or 0)
        new = current + 1
        set_meta(conn, "data_version", str(new))
    return new


def get_setting(conn: Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def set_setting(conn: Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
