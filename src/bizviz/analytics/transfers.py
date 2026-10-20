from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from typing import NamedTuple

TRANSFER_PRIMARIES = frozenset({"TRANSFER_IN", "TRANSFER_OUT", "LOAN_PAYMENTS"})
CC_PAYMENT = "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"
INV_PREFIX = "inv:"
MAX_DAY_GAP = 5


class Side(NamedTuple):
    id: str
    account_id: str
    account_type: str
    date: str
    amount: float
    pfc_primary: str | None
    pfc_detailed: str | None


def _is_transfer_typed(side: Side) -> bool:
    return side.id.startswith(INV_PREFIX) or side.pfc_primary in TRANSFER_PRIMARIES


def _kind(counterpart_id: str) -> str:
    return "investment" if counterpart_id.startswith(INV_PREFIX) else "cash"


def match_transfers(sides: Iterable[Side]) -> dict[str, tuple[str, str]]:
    """Greedily pair outflows with inflows; returns side id -> (counterpart id, kind)."""
    prepared = [(s, date.fromisoformat(s.date).toordinal(), _is_transfer_typed(s)) for s in sides]
    inflows: dict[float, list[tuple[Side, int, bool]]] = defaultdict(list)
    for item in prepared:
        side = item[0]
        if side.amount < 0 and side.pfc_primary != "INCOME":
            inflows[round(-side.amount, 2)].append(item)

    candidates: list[tuple[int, int, str, str]] = []
    for o, o_day, o_transfer in prepared:
        if o.amount <= 0 or o.pfc_primary == "INCOME":
            continue
        if o.pfc_primary == "LOAN_PAYMENTS" and o.pfc_detailed != CC_PAYMENT:
            continue
        o_inv = o.id.startswith(INV_PREFIX)
        for i, i_day, i_transfer in inflows.get(round(o.amount, 2), ()):
            if i.account_id == o.account_id:
                continue
            gap = abs(o_day - i_day)
            if gap > MAX_DAY_GAP:
                continue
            if o_inv and i.id.startswith(INV_PREFIX):
                continue
            if not (o_transfer or i_transfer):
                continue
            if o.pfc_detailed == CC_PAYMENT and i.account_type != "credit":
                continue
            candidates.append((0 if o_transfer and i_transfer else 1, gap, o.id, i.id))

    candidates.sort()
    result: dict[str, tuple[str, str]] = {}
    for _, _, o_id, i_id in candidates:
        if o_id in result or i_id in result:
            continue
        result[o_id] = (i_id, _kind(i_id))
        result[i_id] = (o_id, _kind(o_id))
    return result
