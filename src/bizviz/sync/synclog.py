from bizviz.db.connection import Connection, now_iso, write_unit

MAX_ROWS = 1000


def start(conn: Connection, job: str, item_id: str | None) -> int:
    with write_unit(conn):
        cur = conn.execute(
            "INSERT INTO sync_log(item_id, job, started_at) VALUES (?, ?, ?)",
            (item_id, job, now_iso()),
        )
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


def finish(
    conn: Connection,
    log_id: int,
    ok: bool,
    error_code: str | None,
    transient: bool = False,
) -> None:
    with write_unit(conn):
        conn.execute(
            "UPDATE sync_log SET finished_at = ?, ok = ?, error_code = ?, transient = ? "
            "WHERE id = ?",
            (now_iso(), int(ok), error_code, int(transient), log_id),
        )
        conn.execute(
            "DELETE FROM sync_log WHERE id <= (SELECT max(id) FROM sync_log) - ?", (MAX_ROWS,)
        )
