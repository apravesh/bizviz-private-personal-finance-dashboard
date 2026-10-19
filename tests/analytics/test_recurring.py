from datetime import date, timedelta

import pytest

from bizviz.analytics.classify import rebuild_classification
from bizviz.analytics.recurring import RecRow, detect
from bizviz.db.connection import Connection
from tests import factories as f
from tests.analytics.conftest import TODAY, World


def rows(key: str, start: date, gaps: list[int], amounts: list[float]) -> list[RecRow]:
    days = [start]
    for g in gaps:
        days.append(days[-1] + timedelta(days=g))
    return [
        RecRow(f"{key}-{i}", d.isoformat(), a, key, key.title())
        for i, (d, a) in enumerate(zip(days, amounts, strict=True))
    ]


def run(
    r: list[RecRow],
    rules: dict[str, int | None] | None = None,
    ov: dict[str, int | None] | None = None,
):
    return detect(r, rules or {}, ov or {}, TODAY)


@pytest.mark.parametrize(
    ("cadence", "gap", "count"),
    [
        ("weekly", 7, 4),
        ("weekly", 9, 4),
        ("biweekly", 14, 4),
        ("monthly", 30, 5),
        ("monthly", 31, 5),
        ("quarterly", 91, 3),
        ("annual", 365, 2),
    ],
)
def test_cadence_detected(cadence: str, gap: int, count: int) -> None:
    r = rows("m", date(2025, 1, 1), [gap] * (count - 1), [20.0] * count)
    flagged, groups = run(r)
    assert len(flagged) == count
    assert [(g.cadence, g.count) for g in groups] == [(cadence, count)]


def test_monthly_with_amount_noise() -> None:
    amounts = [10.0, 10.5, 9.6, 10.9, 9.2]
    r = rows("sub", date(2026, 5, 1), [30, 29, 31, 30], amounts)
    flagged, groups = run(r)
    assert len(flagged) == 5
    assert groups[0].cadence == "monthly"
    assert groups[0].typical_amount == pytest.approx(10.0, abs=1.0)
    assert groups[0].last_date == r[-1].date


def test_irregular_merchant_not_flagged() -> None:
    r = rows("shop", date(2026, 1, 1), [3, 40, 12, 90], [20, 55, 8, 31, 20])
    assert run(r) == (set(), [])


def test_too_few_transactions() -> None:
    assert run(rows("m", date(2026, 1, 1), [30, 30], [10, 10, 10])[:2]) == (set(), [])
    assert run(rows("a", date(2024, 1, 1), [], [10]))[0] == set()


def test_outlier_amount_not_flagged_but_group_stays() -> None:
    r = rows("m", date(2026, 1, 1), [30, 30, 30, 30], [10, 10, 10, 10, 90])
    flagged, groups = run(r)
    assert flagged == {"m-0", "m-1", "m-2", "m-3"}
    assert groups[0].count == 4


def test_too_many_different_amounts_rejects_group() -> None:
    r = rows("m", date(2026, 1, 1), [30, 30, 30, 30], [10, 50, 90, 10, 70])
    assert run(r) == (set(), [])


def test_gap_share_below_threshold() -> None:
    r = rows("m", date(2026, 1, 1), [30, 30, 100, 5, 30, 30, 120], [10] * 8)
    assert run(r)[0] == set()


def test_rule_recurring_one_forces_with_irregular_cadence() -> None:
    r = rows("shop", date(2026, 1, 1), [3, 90, 40], [20, 55, 8, 31])
    flagged, groups = run(r, rules={"shop": 1})
    assert flagged == {x.id for x in r}
    assert groups[0].cadence == "irregular" and groups[0].active and groups[0].count == 4


def test_rule_recurring_one_keeps_detected_cadence() -> None:
    r = rows("m", date(2026, 5, 1), [30, 30, 30], [10, 10, 10, 90])
    flagged, groups = run(r, rules={"m": 1})
    assert len(flagged) == 4 and groups[0].cadence == "monthly"


def test_rule_recurring_zero_removes_group() -> None:
    r = rows("m", date(2026, 5, 1), [30, 30, 30], [10] * 4)
    assert run(r, rules={"m": 0}) == (set(), [])


def test_override_wins_over_rule_and_detection() -> None:
    r = rows("m", date(2026, 5, 1), [30, 30, 30], [10] * 4)
    flagged, groups = run(r, rules={"m": 1}, ov={"m-1": 0})
    assert flagged == {"m-0", "m-2", "m-3"} and groups[0].count == 3
    flagged, groups = run(r, rules={"m": 0}, ov={"m-1": 1})
    assert flagged == {"m-1"}
    assert groups[0].cadence == "irregular" and groups[0].count == 1


def test_override_flag_without_group_creates_irregular_group() -> None:
    r = rows("once", date(2026, 1, 1), [], [99.0])
    flagged, groups = run(r, ov={"once-0": 1})
    assert flagged == {"once-0"}
    assert [(g.cadence, g.active) for g in groups] == [("irregular", True)]


def test_typical_amount_is_median_of_last_three() -> None:
    r = rows("m", date(2026, 3, 1), [30, 30, 30, 30], [9, 100 / 11, 10, 11, 12])
    flagged, groups = run(r)
    assert groups[0].typical_amount == 11


@pytest.mark.parametrize(
    ("cadence_gap", "days_ago", "active"),
    [
        (30, 45, True),
        (30, 46, False),
        (7, 10, True),
        (7, 11, False),
        (365, 547, True),
        (365, 548, False),
    ],
)
def test_active_flag(cadence_gap: int, days_ago: int, active: bool) -> None:
    count = 2 if cadence_gap == 365 else 4
    last = TODAY - timedelta(days=days_ago)
    start = last - timedelta(days=cadence_gap * (count - 1))
    r = rows("m", start, [cadence_gap] * (count - 1), [10.0] * count)
    _, groups = run(r)
    assert groups[0].active is active


# ---- through the rebuild -------------------------------------------------------------------


def _monthly(w: World, account: str, prefix: str, name: str, n: int = 5) -> None:
    for i in range(n):
        d = TODAY - timedelta(days=30 * (n - i))
        w.tx(f"{prefix}{i}", account, d.isoformat(), 15.0, "ENTERTAINMENT", name=name)


def groups_in(db: Connection, scope: str) -> dict[str, tuple[str, int]]:
    return {
        r["merchant_key"]: (r["cadence"], r["count"])
        for r in db.execute("SELECT * FROM recurring_groups WHERE scope = ?", (scope,))
    }


def test_rebuild_flags_and_scopes(db: Connection, world: World) -> None:
    _monthly(world, world.chk, "c", "Netflix")
    _monthly(world, world.joint, "j", "Gym Joint")
    rebuild_classification(db, TODAY)
    assert groups_in(db, "mine") == {"netflix": ("monthly", 5)}
    assert groups_in(db, "household") == {"netflix": ("monthly", 5), "gym joint": ("monthly", 5)}
    assert db.execute(
        "SELECT mine_recurring, household_recurring FROM tx_classification"
        " WHERE transaction_id = 'c0'"
    ).fetchone()[:] == (1, 1)
    row = db.execute(
        "SELECT mine_recurring, household_recurring FROM tx_classification"
        " WHERE transaction_id = 'j0'"
    ).fetchone()
    assert (row[0], row[1]) == (None, 1)


def test_rebuild_rules_and_overrides(db: Connection, world: World) -> None:
    _monthly(world, world.chk, "c", "Netflix")
    _monthly(world, world.chk, "h", "Hulu")
    world.tx("odd", world.chk, TODAY.isoformat(), 33.0, "FOOD_AND_DRINK", name="Diner")
    f.make_rule(db, "netflix", recurring=False)
    f.make_rule(db, "diner", recurring=True)
    f.make_override(db, "h0", recurring=False)
    rebuild_classification(db, TODAY)
    assert groups_in(db, "mine") == {"hulu": ("monthly", 4), "diner": ("irregular", 1)}
    flags = {
        r[0]: r[1]
        for r in db.execute("SELECT transaction_id, mine_recurring FROM tx_classification")
    }
    assert flags["c1"] == 0 and flags["h0"] == 0 and flags["h1"] == 1 and flags["odd"] == 1


def test_non_spending_flows_never_recurring(db: Connection, world: World) -> None:
    for i in range(5):
        d = TODAY - timedelta(days=30 * (5 - i))
        world.tx(
            f"p{i}", world.chk, d.isoformat(), -3000.0, "INCOME", "INCOME_SALARY", name="Employer"
        )
    rebuild_classification(db, TODAY)
    assert groups_in(db, "mine") == {}
