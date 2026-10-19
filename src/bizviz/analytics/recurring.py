from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median

# (cadence, centre gap in days, tolerance)
CADENCES: tuple[tuple[str, int, int], ...] = (
    ("weekly", 7, 2),
    ("biweekly", 14, 3),
    ("monthly", 30, 5),
    ("quarterly", 91, 10),
    ("annual", 365, 20),
)
_CADENCE_DAYS = {name: centre for name, centre, _ in CADENCES}
AMOUNT_TOLERANCE = 0.25
MIN_SHARE = 0.75


@dataclass(frozen=True, slots=True)
class RecRow:
    id: str
    date: str
    amount: float
    merchant_key: str
    merchant_display: str | None


@dataclass(frozen=True, slots=True)
class Group:
    merchant_key: str
    merchant_display: str | None
    cadence: str
    typical_amount: float | None
    last_date: str | None
    count: int
    active: bool


def _fits(gap: float, centre: int, tol: int) -> bool:
    return abs(gap - centre) <= tol


def _detect_group(rows: Sequence[RecRow]) -> tuple[str | None, bool, set[str]]:
    """Returns (cadence of the median gap, whether the group qualifies, ids within tolerance)."""
    days = [date.fromisoformat(r.date).toordinal() for r in rows]
    gaps = [b - a for a, b in zip(days, days[1:], strict=False)]
    cadence: str | None = None
    centre = tol = 0
    if gaps:
        median_gap = median(gaps)
        for name, c, t in CADENCES:
            if _fits(median_gap, c, t):
                cadence, centre, tol = name, c, t
                break
    typical = median(r.amount for r in rows)
    within = {r.id for r in rows if abs(r.amount - typical) <= AMOUNT_TOLERANCE * abs(typical)}
    if cadence is None:
        return None, False, set()
    needed = 2 if cadence == "annual" else 3
    gaps_ok = sum(1 for g in gaps if _fits(g, centre, tol))
    qualifies = (
        len(rows) >= needed
        and gaps_ok >= MIN_SHARE * len(gaps)
        and len(within) >= MIN_SHARE * len(rows)
    )
    return cadence, qualifies, within if qualifies else set()


def detect(
    scope_rows: Sequence[RecRow],
    rules: Mapping[str, int | None],
    overrides: Mapping[str, int | None],
    today: date,
) -> tuple[set[str], list[Group]]:
    """Flag recurring transactions and build groups for one scope.

    `scope_rows` are that scope's spending transactions with amount > 0. `rules` maps merchant
    key -> rule.recurring and `overrides` maps transaction id -> override.recurring (None = unset).
    """
    by_key: dict[str, list[RecRow]] = defaultdict(list)
    for row in scope_rows:
        by_key[row.merchant_key].append(row)

    flagged_all: set[str] = set()
    groups: list[Group] = []
    for key, rows in by_key.items():
        rows.sort(key=lambda r: (r.date, r.id))
        cadence, qualifies, detected = _detect_group(rows)
        rule = rules.get(key)
        if rule == 1:
            flagged = {r.id for r in rows}
            group_cadence = cadence or "irregular"
        elif rule == 0:
            flagged = set()
            group_cadence = "irregular"
        else:
            flagged = detected
            group_cadence = cadence if qualifies and cadence else "irregular"
        for r in rows:
            ov = overrides.get(r.id)
            if ov == 1:
                flagged.add(r.id)
            elif ov == 0:
                flagged.discard(r.id)
        members = [r for r in rows if r.id in flagged]
        if not members:
            continue
        flagged_all.update(flagged)
        last = members[-1]
        days = _CADENCE_DAYS.get(group_cadence)
        active = days is None or (today - date.fromisoformat(last.date)).days <= 1.5 * days
        groups.append(
            Group(
                merchant_key=key,
                merchant_display=last.merchant_display,
                cadence=group_cadence,
                typical_amount=float(median(r.amount for r in members[-3:])),
                last_date=last.date,
                count=len(members),
                active=active,
            )
        )
    return flagged_all, groups
