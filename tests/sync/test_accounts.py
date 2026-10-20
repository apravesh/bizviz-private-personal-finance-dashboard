from datetime import date

import pytest

from bizviz.db.connection import Connection, write_unit
from bizviz.sync.accounts import normalize_name, snapshot_balances, upsert_accounts
from tests.factories import make_account, make_item
from tests.sync.helpers import plaid_account


def acct(db: Connection, account_id: str) -> dict[str, object]:
    row = db.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
    return dict(row)


def test_normalize_name() -> None:
    assert normalize_name("  Total   CHECKING 1234 ") == "total checking"
    assert normalize_name(None) == ""


def test_new_account_defaults_and_existing_update(db: Connection) -> None:
    make_item(db)
    with write_unit(db):
        new = upsert_accounts(db, "item-1", [plaid_account("a1", current=10.005)])
    assert new == ["a1"]
    row = acct(db, "a1")
    assert (row["reviewed"], row["ownership"], row["included"]) == (0, "mine", 1)
    assert row["current_balance"] == pytest.approx(10.0, abs=0.011)
    db.execute("UPDATE accounts SET ownership = 'joint', reviewed = 1, gone_at = '2026-01-01'")
    with write_unit(db):
        new = upsert_accounts(
            db, "item-1", [plaid_account("a1", name="Renamed", current=55.0, available=None)]
        )
    assert new == []
    row = acct(db, "a1")
    assert row["name"] == "Renamed"
    assert row["current_balance"] == 55.0
    assert row["available_balance"] is None
    assert row["gone_at"] is None
    assert (row["ownership"], row["reviewed"]) == ("joint", 1)


def test_duplicate_on_active_item_is_excluded_and_copies_ownership(db: Connection) -> None:
    make_item(db, "old")
    make_item(db, "new")
    make_account(db, "old-acc", "old", name="Total Checking 1", mask="0001", ownership="joint")
    with write_unit(db):
        upsert_accounts(
            db, "new", [plaid_account("new-acc", name="total  checking 2", mask="0001")]
        )
    row = acct(db, "new-acc")
    assert row["included"] == 0
    assert row["duplicate_of_account"] == "old-acc"
    assert row["ownership"] == "joint"
    assert db.execute("SELECT duplicate_of FROM items WHERE item_id='new'").fetchone()[0] == "old"
    assert acct(db, "old-acc")["superseded_by"] is None


def test_duplicate_on_removed_item_supersedes_old_account(db: Connection) -> None:
    make_item(db, "old", removed_at="2026-05-01T00:00:00Z")
    make_item(db, "new")
    make_account(db, "old-acc", "old", name="Checking", mask="0001", ownership="joint")
    with write_unit(db):
        upsert_accounts(db, "new", [plaid_account("new-acc", name="Checking", mask="0001")])
    row = acct(db, "new-acc")
    assert row["included"] == 1
    assert row["duplicate_of_account"] is None
    assert row["ownership"] == "joint"
    assert acct(db, "old-acc")["superseded_by"] == "new-acc"
    assert db.execute("SELECT duplicate_of FROM items WHERE item_id='new'").fetchone()[0] == "old"


@pytest.mark.parametrize(
    ("institution", "mask", "subtype", "name"),
    [
        ("ins_other", "0001", "checking", "Checking"),
        ("ins_1", "0002", "checking", "Checking"),
        ("ins_1", "0001", "savings", "Checking"),
        ("ins_1", "0001", "checking", "Savings"),
    ],
)
def test_no_duplicate_when_any_key_differs(
    db: Connection, institution: str, mask: str, subtype: str, name: str
) -> None:
    make_item(db, "old", institution_id=institution)
    make_item(db, "new", institution_id="ins_1")
    make_account(db, "old-acc", "old", name=name, mask=mask, subtype=subtype)
    with write_unit(db):
        upsert_accounts(db, "new", [plaid_account("new-acc", name="Checking", mask="0001")])
    row = acct(db, "new-acc")
    assert row["included"] == 1
    assert row["duplicate_of_account"] is None


def test_prefers_active_match_over_removed(db: Connection) -> None:
    make_item(db, "gone", removed_at="2026-05-01T00:00:00Z")
    make_item(db, "live")
    make_item(db, "new")
    make_account(db, "gone-acc", "gone")
    make_account(db, "live-acc", "live")
    with write_unit(db):
        upsert_accounts(db, "new", [plaid_account("new-acc")])
    assert acct(db, "new-acc")["duplicate_of_account"] == "live-acc"
    assert acct(db, "gone-acc")["superseded_by"] is None


def test_mark_gone_and_reappear(db: Connection) -> None:
    make_item(db)
    make_account(db, "a1")
    make_account(db, "a2", mask="0002")
    today = date(2026, 10, 15)
    with write_unit(db):
        upsert_accounts(db, "item-1", [plaid_account("a1")], mark_gone=True, today=today)
    assert acct(db, "a2")["gone_at"] == "2026-10-15"
    assert acct(db, "a1")["gone_at"] is None
    with write_unit(db):
        upsert_accounts(
            db,
            "item-1",
            [plaid_account("a1"), plaid_account("a2", mask="0002")],
            mark_gone=True,
            today=date(2026, 10, 16),
        )
    assert acct(db, "a2")["gone_at"] is None


def test_mark_gone_keeps_first_date_and_requires_today(db: Connection) -> None:
    make_item(db)
    make_account(db, "a1", gone_at="2026-01-01")
    with write_unit(db):
        upsert_accounts(db, "item-1", [], mark_gone=True, today=date(2026, 10, 15))
    assert acct(db, "a1")["gone_at"] == "2026-01-01"
    with pytest.raises(ValueError), write_unit(db):
        upsert_accounts(db, "item-1", [], mark_gone=True)


def test_mark_gone_skipped_for_removed_item(db: Connection) -> None:
    make_item(db, removed_at="2026-05-01T00:00:00Z")
    make_account(db, "a1")
    with write_unit(db):
        upsert_accounts(db, "item-1", [], mark_gone=True, today=date(2026, 10, 15))
    assert acct(db, "a1")["gone_at"] is None


def test_snapshot_balances_dates_and_replace(db: Connection) -> None:
    accounts = [
        plaid_account("chk", current=1.0, available=2.0),
        plaid_account("inv", type="investment", current=9.0, available=None),
    ]
    snapshot_balances(
        db,
        accounts,
        local_date=date(2026, 10, 15),
        investment_dates={"inv": date(2026, 10, 14), "chk": date(2020, 1, 1)},
    )
    rows = {
        r["account_id"]: dict(r) for r in db.execute("SELECT * FROM balance_snapshots").fetchall()
    }
    assert rows["chk"]["date"] == "2026-10-15"
    assert rows["inv"]["date"] == "2026-10-14"
    assert rows["inv"]["available"] is None
    accounts[0]["balances"]["current"] = 5.0
    snapshot_balances(db, accounts, local_date=date(2026, 10, 15), investment_dates={})
    assert db.execute("SELECT count(*) FROM balance_snapshots").fetchone()[0] == 3
    assert (
        db.execute("SELECT current FROM balance_snapshots WHERE account_id='chk'").fetchone()[0]
        == 5.0
    )
