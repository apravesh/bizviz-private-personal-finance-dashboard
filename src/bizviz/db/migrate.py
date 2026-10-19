import re
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources

from sqlcipher3 import dbapi2

from bizviz.db.connection import Connection, get_meta, set_meta, write_unit

_NAME_RE = re.compile(r"(\d{4})_[A-Za-z0-9_]+\.sql")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def discover_migrations() -> list[Migration]:
    found: list[Migration] = []
    for entry in resources.files("bizviz.db.migrations").iterdir():
        match = _NAME_RE.fullmatch(entry.name)
        if match:
            found.append(Migration(int(match.group(1)), entry.name, entry.read_text("utf-8")))
    found.sort(key=lambda m: m.version)
    return found


def current_version(conn: Connection) -> int:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone()
    if row is None:
        return 0
    return int(get_meta(conn, "schema_version") or 0)


def _statements(sql: str) -> list[str]:
    """Split a script into complete statements (trigger bodies stay intact)."""
    statements: list[str] = []
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if dbapi2.complete_statement(buf):
            statements.append(buf)
            buf = ""
    if buf.strip():
        statements.append(buf)
    return statements


def migrate(
    conn: Connection,
    *,
    pre_migration_backup: Callable[[int, int], None] | None = None,
) -> tuple[int, int]:
    """Apply pending migrations; returns (from_version, to_version).

    Each migration runs in its own write unit, together with the schema_version update, so a
    failing migration leaves the database at the previous version.
    """
    start = current_version(conn)
    pending = [m for m in discover_migrations() if m.version > start]
    if not pending:
        return start, start
    target = pending[-1].version
    if start >= 1 and pre_migration_backup is not None:
        pre_migration_backup(start, target)
    for migration in pending:
        with write_unit(conn):
            for statement in _statements(migration.sql):
                conn.execute(statement)
            set_meta(conn, "schema_version", str(migration.version))
    return start, target
