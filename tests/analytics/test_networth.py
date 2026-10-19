from datetime import date

from bizviz.analytics.networth import compute_net_worth
from bizviz.db.connection import Connection
from tests import factories as f
from tests.analytics.conftest import World

TODAY = date(2026, 10, 15)


def snap(db: Connection, account: str, day: str, value: float | None) -> None:
    f.make_balance_snapshot(db, account, day, value)


def point(nw: dict, day: str) -> dict:
    return next(p for p in nw["series"] if p["date"] == day)


def test_assets_minus_debts_and_series_dates(db: Connection, world: World) -> None:
    snap(db, world.chk, "2026-10-10", 1000)
    snap(db, world.chk, "2026-10-12", 1200)
    snap(db, world.cc, "2026-10-12", 300)
    snap(db, world.loan, "2026-10-12", 5000)
    snap(db, world.brokerage, "2026-10-11", 10000)
    snap(db, world.brokerage, "2026-10-09", 9000)
    nw = compute_net_worth(db, TODAY)
    assert nw["starts_on"] == "2026-10-10"
    assert [p["date"] for p in nw["series"]] == ["2026-10-10", "2026-10-12"]
    p10, p12 = nw["series"]
    assert p10["household"] == {"assets": 1000 + 9000, "debts": 0, "stale_accounts": 0}
    assert p12["household"] == {"assets": 1200 + 10000, "debts": 5300, "stale_accounts": 0}
    assert p12["mine"] == p12["household"]


def test_empty(db: Connection, world: World) -> None:
    assert compute_net_worth(db, TODAY) == {"starts_on": None, "series": []}


def test_joint_accounts_excluded_from_mine(db: Connection, world: World) -> None:
    snap(db, world.chk, "2026-10-10", 1000)
    snap(db, world.joint, "2026-10-10", 4000)
    p = point(compute_net_worth(db, TODAY), "2026-10-10")
    assert p["mine"]["assets"] == 1000
    assert p["household"]["assets"] == 5000


def test_negative_credit_balance_becomes_asset(db: Connection, world: World) -> None:
    snap(db, world.chk, "2026-10-10", 100)
    snap(db, world.cc, "2026-10-10", -25.5)
    p = point(compute_net_worth(db, TODAY), "2026-10-10")["household"]
    assert p == {"assets": 125.5, "debts": 0, "stale_accounts": 0}


def test_null_snapshot_ignored(db: Connection, world: World) -> None:
    snap(db, world.chk, "2026-10-10", 100)
    snap(db, world.chk, "2026-10-11", None)
    nw = compute_net_worth(db, TODAY)
    assert [p["date"] for p in nw["series"]] == ["2026-10-10"]


def test_account_stops_after_removed_at_or_gone_at(db: Connection, world: World) -> None:
    f.make_item(db, "item-old", removed_at="2026-10-11T08:00:00Z")
    f.make_account(db, "old", "item-old")
    f.make_account(db, "vanished", "item-bank", gone_at="2026-10-10T00:00:00Z")
    for d in ("2026-10-10", "2026-10-11", "2026-10-12"):
        snap(db, world.chk, d, 100)
        snap(db, "old", d, 1000)
        snap(db, "vanished", d, 10000)
    nw = compute_net_worth(db, TODAY)
    assets = {p["date"]: p["household"]["assets"] for p in nw["series"]}
    assert assets == {"2026-10-10": 11100, "2026-10-11": 1100, "2026-10-12": 100}


def test_duplicate_and_excluded_never_count(db: Connection, world: World) -> None:
    f.make_account(db, "dup", "item-bank", duplicate_of_account="chk")
    f.make_account(db, "off", "item-bank", included=0)
    for a in ("dup", "off"):
        snap(db, a, "2026-10-09", 777)
        snap(db, a, "2026-10-10", 777)
    snap(db, world.chk, "2026-10-10", 100)
    nw = compute_net_worth(db, TODAY)
    assert [p["date"] for p in nw["series"]] == ["2026-10-10"]
    assert nw["series"][0]["household"]["assets"] == 100


def test_investments_alone_do_not_create_points(db: Connection, world: World) -> None:
    snap(db, world.brokerage, "2026-10-10", 5000)
    assert compute_net_worth(db, TODAY)["series"] == []


def test_stale_accounts_thresholds(db: Connection, world: World) -> None:
    snap(db, world.chk, "2026-10-01", 100)
    snap(db, world.sav, "2026-10-01", 100)
    snap(db, world.brokerage, "2026-10-01", 100)
    snap(db, world.sav, "2026-10-04", 100)
    snap(db, world.brokerage, "2026-10-06", 100)
    snap(db, world.chk, "2026-10-04", 100)
    snap(db, world.cc, "2026-10-07", 10)
    snap(db, world.cc, "2026-10-08", 10)
    nw = compute_net_worth(db, TODAY)
    stale = {p["date"]: p["household"]["stale_accounts"] for p in nw["series"]}
    # 10-07: chk/sav age 3 (not stale), brokerage age 1, cc fresh
    assert stale["2026-10-07"] == 0
    # 10-08: chk/sav age 4 (stale), brokerage age 2, cc fresh
    assert stale["2026-10-08"] == 2
    # 10-01: nothing stale yet
    assert stale["2026-10-01"] == 0


def test_investment_stale_after_five_days(db: Connection, world: World) -> None:
    snap(db, world.brokerage, "2026-10-01", 100)
    for d in ("2026-10-06", "2026-10-07"):
        snap(db, world.chk, d, 1)
    nw = compute_net_worth(db, TODAY)
    stale = {p["date"]: p["household"]["stale_accounts"] for p in nw["series"]}
    assert stale == {"2026-10-06": 0, "2026-10-07": 1}
