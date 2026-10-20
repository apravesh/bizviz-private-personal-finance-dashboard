import json
from collections.abc import Iterator
from typing import Any

import pytest

from bizviz.config import KC_TOKEN_PREFIX
from bizviz.context import AppContext
from bizviz.db.connection import Connection, get_setting, set_setting
from bizviz.errors import InvalidInputError, NotFoundError
from bizviz.plaid_fake import FakePlaidClient
from bizviz.plaid_types import PlaidError
from bizviz.sync import linking
from bizviz.sync.linking import (
    SlotLimitError,
    create_link_token,
    disconnect_item,
    exchange_public_token,
    mark_repaired,
)
from bizviz.tokens import index_read, recover_tokens
from tests.factories import make_account, make_item
from tests.sync.helpers import data_version, item_get, item_row, plaid_account


@pytest.fixture(autouse=True)
def clean_exchange_cache() -> Iterator[None]:
    linking._cache.clear()
    linking._locks.clear()
    yield
    linking._cache.clear()
    linking._locks.clear()


@pytest.fixture
def sent(app_ctx: AppContext) -> list[str | None]:
    out: list[str | None] = []
    app_ctx.request_sync = out.append
    return out


def queue_exchange(plaid: FakePlaidClient, item_id: str = "item-new", accounts: Any = None) -> None:
    plaid.queue("exchange_public_token", {"item_id": item_id, "access_token": f"tok-{item_id}"})
    plaid.queue("item_get", item_get(products=["transactions"], billed=["transactions"]))
    plaid.queue(
        "accounts_get",
        {"accounts": accounts if accounts is not None else [plaid_account("a1")]},
    )


def test_exchange_creates_item_accounts_and_requests_sync(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient, sent: list[str | None]
) -> None:
    queue_exchange(fake_plaid)
    before = data_version(db)
    res = exchange_public_token(app_ctx, "pub-1", "ins_9", "Nine Bank", "bank")
    assert (res.item_id, res.duplicate, res.pending) == ("item-new", False, False)
    row = item_row(db, "item-new")
    assert (row["access_token"], row["link_mode"], row["status"]) == ("tok-item-new", "bank", "ok")
    assert row["institution_name"] == "Nine Bank"
    assert json.loads(row["products"]) == ["transactions"]
    assert db.execute("SELECT count(*) FROM accounts WHERE item_id='item-new'").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM balance_snapshots").fetchone()[0] == 1
    assert get_setting(db, "plaid_items_created_count") == "1"
    assert data_version(db) > before
    assert sent == ["item-new"]
    assert fake_plaid.calls[0][0] == "exchange_public_token"
    log = db.execute("SELECT job, ok FROM sync_log WHERE item_id='item-new'").fetchall()
    assert [(r["job"], r["ok"]) for r in log] == [("link", 1)]


def test_exchange_rejects_invalid_mode(app_ctx: AppContext, fake_plaid: FakePlaidClient) -> None:
    with pytest.raises(InvalidInputError):
        exchange_public_token(app_ctx, "pub", None, None, "update")  # type: ignore[arg-type]
    assert fake_plaid.calls == []


def test_exchange_writes_keychain_and_index_before_db_insert(
    app_ctx: AppContext,
    db: Connection,
    fake_plaid: FakePlaidClient,
    sent: list[str | None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue_exchange(fake_plaid)
    secrets = app_ctx.platform.secrets
    seen: dict[str, Any] = {}
    real = linking._finish_exchange

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen["token"] = secrets.get(KC_TOKEN_PREFIX + "item-new")
        seen["index"] = [e["item_id"] for e in index_read(secrets)]
        seen["in_db"] = item_row(db, "item-new") is not None
        return real(*args, **kwargs)

    monkeypatch.setattr(linking, "_finish_exchange", spy)
    exchange_public_token(app_ctx, "pub-1", "ins_9", "Nine Bank", "investment")
    assert seen == {"token": "tok-item-new", "index": ["item-new"], "in_db": False}
    (entry,) = index_read(secrets)
    assert entry["link_mode"] == "investment"
    assert entry["institution_name"] == "Nine Bank"
    assert entry["removed_at"] is None


def test_same_public_token_is_idempotent(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient, sent: list[str | None]
) -> None:
    queue_exchange(fake_plaid)
    first = exchange_public_token(app_ctx, "pub-1", "ins_9", "Nine", "bank")
    second = exchange_public_token(app_ctx, "pub-1", "ins_9", "Nine", "bank")
    assert second.item_id == first.item_id
    assert fake_plaid.count("exchange_public_token") == 1
    assert fake_plaid.count("item_get") == 1
    assert fake_plaid.count("accounts_get") == 1
    assert db.execute("SELECT count(*) FROM items").fetchone()[0] == 1
    assert get_setting(db, "plaid_items_created_count") == "1"
    assert sent == ["item-new", "item-new"]


def test_exchange_cache_expires_after_ttl(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient, sent: list[str | None]
) -> None:
    queue_exchange(fake_plaid)
    exchange_public_token(app_ctx, "pub-1", None, None, "bank")
    app_ctx.clock_holder.now += linking.CACHE_TTL
    fake_plaid.queue("exchange_public_token", {"item_id": "item-2", "access_token": "tok-2"})
    fake_plaid.queue("item_get", item_get())
    fake_plaid.queue("accounts_get", {"accounts": []})
    res = exchange_public_token(app_ctx, "pub-1", None, None, "bank")
    assert res.item_id == "item-2"
    assert fake_plaid.count("exchange_public_token") == 2


def test_exchange_db_failure_leaves_pending_and_recovery_inserts_item(
    app_ctx: AppContext,
    db: Connection,
    fake_plaid: FakePlaidClient,
    sent: list[str | None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue_exchange(fake_plaid)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("disk full")

    monkeypatch.setattr(linking, "_exchange_steps", boom)
    res = exchange_public_token(app_ctx, "pub-1", "ins_9", "Nine Bank", "bank")
    assert (res.item_id, res.pending, res.duplicate) == ("item-new", True, False)
    assert item_row(db, "item-new") is None
    assert sent == ["item-new"]
    assert app_ctx.platform.secrets.get(KC_TOKEN_PREFIX + "item-new") == "tok-item-new"

    monkeypatch.undo()
    assert recover_tokens(app_ctx) == ["Nine Bank"]
    row = item_row(db, "item-new")
    assert row["access_token"] == "tok-item-new"
    assert row["institution_name"] == "Nine Bank"


def test_exchange_followup_plaid_failure_is_pending_and_logged(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient, sent: list[str | None]
) -> None:
    fake_plaid.queue("exchange_public_token", {"item_id": "item-new", "access_token": "tok"})
    fake_plaid.queue("item_get", PlaidError("ITEM_ERROR", "INSTITUTION_DOWN"))
    res = exchange_public_token(app_ctx, "pub-1", "ins_9", "Nine", "bank")
    assert (res.pending, res.duplicate) == (True, False)
    assert item_row(db, "item-new")["products"] == "[]"
    assert sent == ["item-new"]
    log = db.execute("SELECT ok, error_code FROM sync_log WHERE job='link'").fetchone()
    assert (log["ok"], log["error_code"]) == (0, "INSTITUTION_DOWN")


def test_exchange_created_count_is_max_of_increment_and_index(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient, sent: list[str | None]
) -> None:
    from bizviz.tokens import index_merge

    index_merge(
        app_ctx.platform.secrets,
        [
            {
                "item_id": f"old-{n}",
                "institution_id": None,
                "institution_name": None,
                "link_mode": "bank",
                "created_at": "2026-01-01T00:00:00Z",
                "removed_at": None,
            }
            for n in range(4)
        ],
    )
    set_setting(db, "plaid_items_created_count", "1")
    queue_exchange(fake_plaid)
    exchange_public_token(app_ctx, "pub-1", None, None, "bank")
    assert get_setting(db, "plaid_items_created_count") == "5"
    set_setting(db, "plaid_items_created_count", "9")
    queue_exchange(fake_plaid, "item-b")
    exchange_public_token(app_ctx, "pub-2", None, None, "bank")
    assert get_setting(db, "plaid_items_created_count") == "10"


def test_exchange_flags_duplicate_of_active_item(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient, sent: list[str | None]
) -> None:
    make_item(db, "item-1", institution_id="ins_9")
    make_account(db, "acc-old", "item-1", name="Checking", mask="0001", subtype="checking")
    queue_exchange(fake_plaid, accounts=[plaid_account("acc-new")])
    res = exchange_public_token(app_ctx, "pub-1", "ins_9", "Bank One", "bank")
    assert res.duplicate is True
    assert item_row(db, "item-new")["duplicate_of"] == "item-1"
    acc = db.execute("SELECT * FROM accounts WHERE account_id='acc-new'").fetchone()
    assert (acc["included"], acc["duplicate_of_account"]) == (0, "acc-old")


def test_exchange_with_removed_match_supersedes_and_keeps_account_included(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient, sent: list[str | None]
) -> None:
    make_item(db, "item-1", institution_id="ins_9", removed_at="2026-06-01T00:00:00Z")
    make_account(db, "acc-old", "item-1", name="Checking", mask="0001", subtype="checking")
    queue_exchange(fake_plaid, accounts=[plaid_account("acc-new")])
    res = exchange_public_token(app_ctx, "pub-1", "ins_9", "Bank One", "bank")
    assert res.duplicate is True
    assert item_row(db, "item-new")["duplicate_of"] == "item-1"
    acc = db.execute("SELECT * FROM accounts WHERE account_id='acc-new'").fetchone()
    assert (acc["included"], acc["duplicate_of_account"]) == (1, None)
    old = db.execute("SELECT * FROM accounts WHERE account_id='acc-old'").fetchone()
    assert old["superseded_by"] == "acc-new"


def test_create_link_token_modes(app_ctx: AppContext, fake_plaid: FakePlaidClient) -> None:
    fake_plaid.set_default("create_link_token", "link-tok")
    assert create_link_token(app_ctx, "bank") == "link-tok"
    assert create_link_token(app_ctx, "loan") == "link-tok"
    assert fake_plaid.calls[0] == ("create_link_token", (), {"mode": "bank"})


def test_create_link_token_slot_limit(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    fake_plaid.set_default("create_link_token", "link-tok")
    set_setting(db, "plaid_items_created_count", "9")
    assert create_link_token(app_ctx, "bank") == "link-tok"
    set_setting(db, "plaid_items_created_count", "10")
    with pytest.raises(SlotLimitError):
        create_link_token(app_ctx, "bank")
    assert fake_plaid.count("create_link_token") == 1


def test_create_link_token_update_mode(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    fake_plaid.set_default("create_link_token", "upd-tok")
    make_item(db, "item-1")
    make_item(db, "gone", removed_at="2026-06-01T00:00:00Z")
    set_setting(db, "plaid_items_created_count", "10")
    assert create_link_token(app_ctx, "update", "item-1") == "upd-tok"
    assert fake_plaid.calls[-1] == (
        "create_link_token",
        (),
        {"mode": "update", "access_token": "access-item-1"},
    )
    for bad in ("missing", "gone"):
        with pytest.raises(NotFoundError):
            create_link_token(app_ctx, "update", bad)
    with pytest.raises(NotFoundError):
        create_link_token(app_ctx, "update", None)


def test_mark_repaired(app_ctx: AppContext, db: Connection, sent: list[str | None]) -> None:
    make_item(
        db,
        status="needs_attention",
        error_code="ITEM_LOGIN_REQUIRED",
        unavailable_steps=json.dumps(["holdings"]),
    )
    before = data_version(db)
    mark_repaired(app_ctx, "item-1")
    row = item_row(db)
    assert (row["status"], row["error_code"], row["unavailable_steps"]) == ("ok", None, "[]")
    assert data_version(db) > before
    assert sent == ["item-1"]
    with pytest.raises(NotFoundError):
        mark_repaired(app_ctx, "nope")
    assert sent == ["item-1"]


def test_disconnect_item(app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient) -> None:
    make_item(db)
    make_account(db, "acc-1", "item-1")
    fake_plaid.queue("item_remove", None)
    app_ctx.platform.secrets.set(KC_TOKEN_PREFIX + "item-1", "access-item-1")
    before = data_version(db)
    disconnect_item(app_ctx, "item-1")
    assert fake_plaid.calls == [("item_remove", ("access-item-1",), {})]
    row = item_row(db)
    assert row["removed_at"] == "2026-10-15T15:00:00Z"
    assert db.execute("SELECT included FROM accounts").fetchone()[0] == 1
    (entry,) = index_read(app_ctx.platform.secrets)
    assert entry["item_id"] == "item-1"
    assert entry["removed_at"] == "2026-10-15T15:00:00Z"
    assert data_version(db) > before
    disconnect_item(app_ctx, "item-1")
    assert fake_plaid.count("item_remove") == 1


def test_disconnect_item_not_found_at_plaid_is_success(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db)
    fake_plaid.queue("item_remove", PlaidError("ITEM_ERROR", "ITEM_NOT_FOUND"))
    disconnect_item(app_ctx, "item-1")
    assert item_row(db)["removed_at"] is not None


def test_disconnect_other_plaid_error_propagates_and_changes_nothing(
    app_ctx: AppContext, db: Connection, fake_plaid: FakePlaidClient
) -> None:
    make_item(db)
    fake_plaid.queue("item_remove", PlaidError("API_ERROR", "INTERNAL_SERVER_ERROR"))
    with pytest.raises(PlaidError):
        disconnect_item(app_ctx, "item-1")
    assert item_row(db)["removed_at"] is None
    assert index_read(app_ctx.platform.secrets) == []


def test_disconnect_unknown_item(app_ctx: AppContext, fake_plaid: FakePlaidClient) -> None:
    with pytest.raises(NotFoundError):
        disconnect_item(app_ctx, "nope")
