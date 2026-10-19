from bizviz.db.connection import Connection
from bizviz.sync import synclog


def test_start_and_finish(db: Connection) -> None:
    log_id = synclog.start(db, "daily", "item-1")
    row = db.execute("SELECT * FROM sync_log WHERE id = ?", (log_id,)).fetchone()
    assert (row["job"], row["item_id"], row["ok"], row["finished_at"]) == (
        "daily",
        "item-1",
        None,
        None,
    )
    synclog.finish(db, log_id, False, "NETWORK_ERROR", transient=True)
    row = db.execute("SELECT * FROM sync_log WHERE id = ?", (log_id,)).fetchone()
    assert (row["ok"], row["error_code"], row["transient"]) == (0, "NETWORK_ERROR", 1)
    assert row["finished_at"] is not None


def test_item_id_may_be_null_and_transient_defaults_false(db: Connection) -> None:
    log_id = synclog.start(db, "recovery", None)
    synclog.finish(db, log_id, True, None)
    row = db.execute("SELECT * FROM sync_log").fetchone()
    assert (row["item_id"], row["ok"], row["transient"]) == (None, 1, 0)


def test_prunes_to_newest_thousand(db: Connection) -> None:
    for _ in range(1005):
        synclog.start(db, "transactions", "item-1")
    last = synclog.start(db, "transactions", "item-1")
    synclog.finish(db, last, True, None)
    assert db.execute("SELECT count(*) FROM sync_log").fetchone()[0] == synclog.MAX_ROWS
    assert db.execute("SELECT max(id) FROM sync_log").fetchone()[0] == last
    assert db.execute("SELECT min(id) FROM sync_log").fetchone()[0] == last - synclog.MAX_ROWS + 1
