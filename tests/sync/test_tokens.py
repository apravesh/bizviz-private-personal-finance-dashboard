import json
import threading
from typing import Any

import pytest

from bizviz import tokens
from bizviz.config import KC_ITEM_INDEX, KC_TOKEN_PREFIX
from bizviz.context import AppContext
from bizviz.db.connection import Connection, get_setting, set_setting
from bizviz.tokens import (
    apply_index_removed_at,
    index_merge,
    index_read,
    index_upsert,
    mirror_all_from_db,
    mirror_token,
    recover_tokens,
    recovered_items,
)
from tests.factories import make_item
from tests.sync.helpers import data_version, item_row


def entry(item_id: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "item_id": item_id,
        "institution_id": "ins_1",
        "institution_name": "Bank One",
        "link_mode": "bank",
        "created_at": "2026-01-01T00:00:00Z",
        "removed_at": None,
    }
    base.update(kw)
    return base


def test_mirror_token_and_index_upsert(app_ctx: AppContext) -> None:
    secrets = app_ctx.platform.secrets
    mirror_token(secrets, "i1", "tok")
    assert secrets.get(KC_TOKEN_PREFIX + "i1") == "tok"
    index_upsert(secrets, entry("i1"))
    index_upsert(secrets, entry("i2", institution_name="Two"))
    assert [e["item_id"] for e in index_read(secrets)] == ["i1", "i2"]


def test_upsert_merges_and_removed_at_wins(app_ctx: AppContext) -> None:
    secrets = app_ctx.platform.secrets
    index_upsert(secrets, entry("i1"))
    index_upsert(secrets, entry("i1", removed_at="2026-06-01T00:00:00Z"))
    index_upsert(secrets, entry("i1", removed_at=None, institution_name="Renamed"))
    (only,) = index_read(secrets)
    assert only["removed_at"] == "2026-06-01T00:00:00Z"
    assert only["institution_name"] == "Renamed"
    assert only["created_at"] == "2026-01-01T00:00:00Z"
    index_upsert(secrets, entry("i1", created_at="2027-01-01T00:00:00Z"))
    assert index_read(secrets)[0]["created_at"] == "2026-01-01T00:00:00Z"


def test_index_merge_is_union_and_never_drops(app_ctx: AppContext) -> None:
    secrets = app_ctx.platform.secrets
    index_upsert(secrets, entry("keep"))
    index_merge(secrets, [entry("new"), entry("keep", removed_at="2026-06-01T00:00:00Z")])
    got = {e["item_id"]: e for e in index_read(secrets)}
    assert set(got) == {"keep", "new"}
    assert got["keep"]["removed_at"] == "2026-06-01T00:00:00Z"
    index_merge(secrets, [])
    assert len(index_read(secrets)) == 2


def test_concurrent_upserts_lose_nothing(app_ctx: AppContext) -> None:
    secrets = app_ctx.platform.secrets
    threads = [
        threading.Thread(target=index_upsert, args=(secrets, entry(f"i{n}"))) for n in range(40)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(index_read(secrets)) == 40


def test_corrupt_index_is_backed_up_not_lost(app_ctx: AppContext) -> None:
    secrets = app_ctx.platform.secrets
    secrets.set(KC_ITEM_INDEX, "{not json")
    assert index_read(secrets) == []
    index_upsert(secrets, entry("i1"))
    assert secrets.get(tokens.INDEX_BACKUP_KEY) == "{not json"
    assert [e["item_id"] for e in index_read(secrets)] == ["i1"]


def test_mirror_all_from_db(app_ctx: AppContext, db: Connection) -> None:
    make_item(db, "a")
    make_item(db, "b", removed_at="2026-06-01T00:00:00Z", link_mode="loan")
    secrets = app_ctx.platform.secrets
    index_upsert(secrets, entry("orphan"))
    mirror_all_from_db(app_ctx)
    assert secrets.get(KC_TOKEN_PREFIX + "a") == "access-a"
    assert secrets.get(KC_TOKEN_PREFIX + "b") == "access-b"
    got = {e["item_id"]: e for e in index_read(secrets)}
    assert set(got) == {"a", "b", "orphan"}
    assert got["b"]["removed_at"] == "2026-06-01T00:00:00Z"
    assert got["b"]["link_mode"] == "loan"


def requests(ctx: AppContext) -> list[str | None]:
    sent: list[str | None] = []
    ctx.request_sync = sent.append
    return sent


def test_recover_inserts_missing_item_and_records_it(app_ctx: AppContext, db: Connection) -> None:
    secrets = app_ctx.platform.secrets
    sent = requests(app_ctx)
    mirror_token(secrets, "lost", "tok-lost")
    mirror_token(secrets, "gone", "tok-gone")
    index_merge(
        secrets,
        [
            entry("lost", institution_name="Lost Bank", link_mode="investment"),
            entry("gone", institution_name="Gone Bank", removed_at="2026-06-01T00:00:00Z"),
            entry("no-token", institution_name="No Token"),
        ],
    )
    make_item(db, "present")
    index_upsert(secrets, entry("present"))
    before = data_version(db)

    names = recover_tokens(app_ctx)

    assert sorted(names) == ["Gone Bank", "Lost Bank"]
    lost = item_row(db, "lost")
    assert (lost["access_token"], lost["status"], lost["products"]) == ("tok-lost", "ok", "[]")
    assert lost["link_mode"] == "investment"
    assert (lost["transactions_cursor"], lost["historical_complete"]) == (None, 0)
    assert lost["investments_history_complete"] == 0
    assert lost["created_at"] == "2026-01-01T00:00:00Z"
    assert lost["removed_at"] is None
    assert item_row(db, "gone")["removed_at"] == "2026-06-01T00:00:00Z"
    assert item_row(db, "no-token") is None
    assert sent == ["lost"]
    assert data_version(db) > before
    assert get_setting(db, "plaid_items_created_count") == "4"
    recorded = recovered_items(db)
    assert {r["item_id"] for r in recorded} == {"lost", "gone"}
    assert all(r["recovered_at"] == "2026-10-15T15:00:00Z" for r in recorded)
    assert {r["institution_name"] for r in recorded} == {"Lost Bank", "Gone Bank"}
    logs = db.execute("SELECT item_id, ok FROM sync_log WHERE job = 'recovery'").fetchall()
    assert {r["item_id"] for r in logs} == {"lost", "gone"}
    assert set(tokens.RECOVERED_THIS_RUN) >= {"Lost Bank", "Gone Bank"}


def test_recover_leaves_existing_items_untouched_and_is_idempotent(
    app_ctx: AppContext, db: Connection
) -> None:
    secrets = app_ctx.platform.secrets
    make_item(db, "present", status="needs_attention", error_code="X")
    mirror_token(secrets, "present", "different-token")
    index_upsert(secrets, entry("present", institution_name="Other Name"))
    set_setting(db, "plaid_items_created_count", "1")
    assert recover_tokens(app_ctx) == []
    row = item_row(db, "present")
    assert (row["access_token"], row["status"], row["institution_name"]) == (
        "access-present",
        "needs_attention",
        "Bank One",
    )
    assert recovered_items(db) == []
    assert db.execute("SELECT count(*) FROM sync_log").fetchone()[0] == 0


def test_recover_raises_created_count_to_index_length_only_upward(
    app_ctx: AppContext, db: Connection
) -> None:
    secrets = app_ctx.platform.secrets
    index_merge(secrets, [entry("a"), entry("b"), entry("c")])
    recover_tokens(app_ctx)
    assert get_setting(db, "plaid_items_created_count") == "3"
    set_setting(db, "plaid_items_created_count", "7")
    recover_tokens(app_ctx)
    assert get_setting(db, "plaid_items_created_count") == "7"


def test_recover_appends_to_existing_recovered_list(app_ctx: AppContext, db: Connection) -> None:
    secrets = app_ctx.platform.secrets
    set_setting(db, "recovered_items", json.dumps([{"item_id": "old", "institution_name": "Old"}]))
    mirror_token(secrets, "x", "tok")
    index_upsert(secrets, entry("x"))
    recover_tokens(app_ctx)
    assert [r["item_id"] for r in recovered_items(db)] == ["old", "x"]


def test_recovered_items_empty_by_default(db: Connection) -> None:
    assert recovered_items(db) == []


def test_apply_index_removed_at(app_ctx: AppContext, db: Connection) -> None:
    secrets = app_ctx.platform.secrets
    make_item(db, "active")
    make_item(db, "to-remove")
    make_item(db, "already", removed_at="2026-02-02T00:00:00Z")
    index_merge(
        secrets,
        [
            entry("active"),
            entry("to-remove", removed_at="2026-06-01T00:00:00Z"),
            entry("already", removed_at="2026-09-09T00:00:00Z"),
            entry("not-in-db", removed_at="2026-06-01T00:00:00Z"),
        ],
    )
    before = data_version(db)
    apply_index_removed_at(app_ctx)
    assert item_row(db, "active")["removed_at"] is None
    assert item_row(db, "to-remove")["removed_at"] == "2026-06-01T00:00:00Z"
    assert item_row(db, "already")["removed_at"] == "2026-02-02T00:00:00Z"
    assert item_row(db, "not-in-db") is None
    assert data_version(db) == before + 1
    apply_index_removed_at(app_ctx)
    assert data_version(db) == before + 1


@pytest.mark.parametrize("bad_mode", [None, "weird"])
def test_recover_defaults_unknown_link_mode_to_bank(
    app_ctx: AppContext, db: Connection, bad_mode: str | None
) -> None:
    secrets = app_ctx.platform.secrets
    mirror_token(secrets, "x", "tok")
    index_upsert(secrets, entry("x", link_mode=bad_mode))
    recover_tokens(app_ctx)
    assert item_row(db, "x")["link_mode"] == "bank"
