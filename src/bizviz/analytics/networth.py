from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from bizviz.analytics.tax import tax_group
from bizviz.api.schema import NetWorthOut, NetWorthPoint, NetWorthScope
from bizviz.db.connection import Connection

ASSET_TYPES = ("depository", "investment")
CASH_STALE_DAYS = 3
INVESTMENT_STALE_DAYS = 5


@dataclass(frozen=True, slots=True)
class EligibleAccount:
    account_id: str
    type: str
    ownership: str
    cutoff: str | None
    tax_advantaged: bool


def eligible_accounts(conn: Connection) -> list[EligibleAccount]:
    """Included, non-duplicate accounts with the last date they may contribute (YYYY-MM-DD)."""
    result = []
    for r in conn.execute(
        "SELECT a.account_id, a.type, a.subtype, a.ownership, a.gone_at, i.removed_at"
        " FROM accounts a"
        " LEFT JOIN items i ON i.item_id = a.item_id"
        " WHERE a.included = 1 AND a.duplicate_of_account IS NULL ORDER BY a.account_id"
    ):
        ends = [str(v)[:10] for v in (r["gone_at"], r["removed_at"]) if v]
        result.append(
            EligibleAccount(
                r["account_id"],
                r["type"],
                r["ownership"],
                min(ends) if ends else None,
                tax_group(r["subtype"]) is not None,
            )
        )
    return result


def compute_net_worth(conn: Connection, today: date) -> NetWorthOut:
    accounts = eligible_accounts(conn)
    snapshots: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for r in conn.execute(
        "SELECT account_id, date, current FROM balance_snapshots WHERE current IS NOT NULL"
        " ORDER BY account_id, date"
    ):
        snapshots[r["account_id"]].append((r["date"], r["current"]))

    series_dates: set[str] = set()
    for acc in accounts:
        if acc.type == "investment":
            continue
        series_dates.update(d for d, _ in snapshots[acc.account_id] if _within(d, acc.cutoff))
    dates = sorted(series_dates)

    totals = {
        scope: [[0.0, 0.0, 0, 0.0] for _ in dates] for scope in ("mine", "household")
    }  # assets, debts, stale, of which assets in tax-advantaged accounts
    ordinals = [date.fromisoformat(d).toordinal() for d in dates]
    for acc in accounts:
        snaps = snapshots[acc.account_id]
        if not snaps:
            continue
        snap_dates = [d for d, _ in snaps]
        limit = INVESTMENT_STALE_DAYS if acc.type == "investment" else CASH_STALE_DAYS
        scopes = ("household", "mine") if acc.ownership == "mine" else ("household",)
        for idx, d in enumerate(dates):
            if not _within(d, acc.cutoff):
                break
            pos = bisect_right(snap_dates, d) - 1
            if pos < 0:
                continue
            snap_date, value = snaps[pos]
            if acc.type in ASSET_TYPES:
                assets, debts = value, 0.0
            elif value < 0:
                assets, debts = -value, 0.0
            else:
                assets, debts = 0.0, value
            stale = ordinals[idx] - date.fromisoformat(snap_date).toordinal() > limit
            for scope in scopes:
                cell = totals[scope][idx]
                cell[0] += assets
                cell[1] += debts
                cell[2] += int(stale)
                if acc.tax_advantaged:
                    cell[3] += assets

    def scope_out(cell: list[float]) -> NetWorthScope:
        return {
            "assets": round(cell[0], 2),
            "debts": round(cell[1], 2),
            "stale_accounts": int(cell[2]),
            "retirement": round(cell[3], 2),
        }

    series: list[NetWorthPoint] = [
        {
            "date": d,
            "mine": scope_out(totals["mine"][i]),
            "household": scope_out(totals["household"][i]),
        }
        for i, d in enumerate(dates)
    ]
    return {"starts_on": dates[0] if dates else None, "series": series}


def _within(day: str, cutoff: str | None) -> bool:
    return cutoff is None or day <= cutoff
