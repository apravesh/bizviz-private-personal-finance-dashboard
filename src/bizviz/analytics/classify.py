from dataclasses import dataclass
from datetime import date

from bizviz.analytics.categories import (
    INCOME_OTHER,
    SPENDING_OTHER,
    TRANSFER,
    applies_to_map,
    default_category,
)
from bizviz.analytics.merchant import merchant_key, raw_display
from bizviz.analytics.recurring import RecRow, detect
from bizviz.analytics.transfers import CC_PAYMENT, INV_PREFIX, Side, match_transfers
from bizviz.api.schema import QualityOut, SupersededOut, SuspectCardPayments
from bizviz.db.connection import Connection, bump_data_version, write_unit

SCOPES = ("mine", "household")
CASH_TYPES = ("depository", "credit")
_E_DETAILED = frozenset(
    {
        "TRANSFER_OUT_SAVINGS",
        "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS",
        "TRANSFER_IN_SAVINGS",
        "TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS",
    }
)


@dataclass(slots=True)
class _Tx:
    id: str
    account_id: str
    date: str
    amount: float
    name: str | None
    merchant_name: str | None
    pfc_primary: str | None
    pfc_detailed: str | None
    key: str = ""
    display: str | None = None


def _in_scope(scope: str, ownership: str) -> bool:
    return scope == "household" or ownership == "mine"


def _fits(applies_to: str, flow: str) -> bool:
    if flow in ("internal", "ignored"):
        return True
    return applies_to == "both" or applies_to == flow


def _auto(
    tx: _Tx, outflow: bool, paired: bool, counterpart_in_scope: bool, default_cat: str
) -> tuple[str, str]:
    if paired:
        if counterpart_in_scope:
            return "internal", TRANSFER
        return ("spending", "sys:to_joint") if outflow else ("earning", "sys:from_joint")
    primary, detailed = tx.pfc_primary, tx.pfc_detailed
    if outflow:
        if detailed == CC_PAYMENT:
            return "spending", "sys:cc_unlinked"
        if primary == "LOAN_PAYMENTS":
            return "spending", default_cat
    if detailed in _E_DETAILED:
        return "internal", TRANSFER
    if not outflow:
        if primary in ("LOAN_PAYMENTS", "LOAN_DISBURSEMENTS"):
            return "internal", TRANSFER
        if primary == "INCOME":
            return "earning", default_cat
        if primary == "TRANSFER_IN":
            return "earning", "sys:money_in_others"
        return "spending", default_cat
    if primary == "TRANSFER_OUT":
        return "spending", "sys:transfers_out"
    return "spending", default_cat


def _layered(
    outflow: bool,
    auto: tuple[str, str],
    override: tuple[str | None, str | None],
    rule: tuple[str | None, str | None],
    applies: dict[str, str],
) -> tuple[str, str]:
    ov_flow, ov_cat = override
    rule_flow, rule_cat = rule
    if ov_flow:
        flow = ov_flow
    elif rule_flow and (rule_flow != "earning" or not outflow):
        flow = rule_flow
    else:
        flow = auto[0]

    user_cat: str | None = None
    if ov_cat:
        user_cat = ov_cat
    elif rule_cat and _fits(applies.get(rule_cat, "both"), flow):
        user_cat = rule_cat

    if flow in ("internal", "ignored"):
        return flow, user_cat or TRANSFER
    if user_cat:
        return flow, user_cat
    category = auto[1]
    if not _fits(applies.get(category, "both"), flow):
        category = SPENDING_OTHER if flow == "spending" else INCOME_OTHER
    return flow, category


def rebuild_classification(conn: Connection, today: date) -> int:
    """Rebuild tx_classification and recurring_groups in one write unit; returns data_version."""
    with write_unit(conn):
        applies = applies_to_map(conn)
        accounts = {
            r["account_id"]: r
            for r in conn.execute(
                "SELECT account_id, type, ownership, included, duplicate_of_account,"
                " superseded_by FROM accounts"
            )
        }
        cash_accounts = {
            aid
            for aid, a in accounts.items()
            if a["type"] in CASH_TYPES and a["included"] and a["duplicate_of_account"] is None
        }
        earliest = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT account_id, MIN(date) FROM transactions GROUP BY account_id"
            )
        }

        txs: list[_Tx] = []
        for r in conn.execute(
            "SELECT transaction_id, account_id, date, amount, name, merchant_name, pfc_primary,"
            " pfc_detailed FROM transactions"
        ):
            aid = r["account_id"]
            if aid not in cash_accounts:
                continue
            superseding = accounts[aid]["superseded_by"]
            if superseding and superseding in earliest and r["date"] >= earliest[superseding]:
                continue
            txs.append(_Tx(*tuple(r)))

        latest: dict[str, tuple[str, str]] = {}
        displays: dict[str, str | None] = {}
        for tx in txs:
            tx.key = merchant_key(tx.merchant_name, tx.name)
            order = (tx.date, tx.id)
            if tx.key not in latest or order > latest[tx.key]:
                latest[tx.key] = order
                displays[tx.key] = raw_display(tx.merchant_name, tx.name)
        for tx in txs:
            tx.display = displays[tx.key]

        sides = [
            Side(
                tx.id,
                tx.account_id,
                accounts[tx.account_id]["type"],
                tx.date,
                tx.amount,
                tx.pfc_primary,
                tx.pfc_detailed,
            )
            for tx in txs
        ]
        side_account = {tx.id: tx.account_id for tx in txs}
        for r in conn.execute(
            "SELECT it.investment_transaction_id, it.account_id, it.date, it.amount"
            " FROM investment_transactions it JOIN accounts a ON a.account_id = it.account_id"
            " LEFT JOIN securities s ON s.security_id = it.security_id"
            " WHERE a.type = 'investment' AND a.included = 1 AND a.duplicate_of_account IS NULL"
            " AND it.amount IS NOT NULL AND ((it.type = 'cash' AND it.subtype IN"
            " ('deposit', 'withdrawal', 'contribution', 'transfer')) OR (it.type = 'transfer'"
            " AND (it.security_id IS NULL OR s.is_cash_equivalent = 1)))"
        ):
            side_id = INV_PREFIX + r[0]
            side_account[side_id] = r[1]
            sides.append(Side(side_id, r[1], "investment", r[2], r[3], None, None))
        pairs = match_transfers(sides)

        overrides = {
            r["transaction_id"]: r
            for r in conn.execute(
                "SELECT transaction_id, category_id, flow_class, recurring FROM tx_overrides"
            )
        }
        rules = {
            r["merchant_key"]: r
            for r in conn.execute(
                "SELECT merchant_key, category_id, flow_class, recurring FROM merchant_rules"
            )
        }
        rule_recurring = {k: r["recurring"] for k, r in rules.items()}
        override_recurring = {k: r["recurring"] for k, r in overrides.items()}
        no_rule = (None, None)

        classes: dict[str, dict[str, tuple[str, str]]] = {s: {} for s in SCOPES}
        rec_rows: dict[str, list[RecRow]] = {s: [] for s in SCOPES}
        for tx in txs:
            outflow = tx.amount >= 0
            default_cat = default_category(tx.pfc_primary, tx.pfc_detailed, applies)
            pair = pairs.get(tx.id)
            counterpart_owner = (
                accounts[side_account[pair[0]]]["ownership"] if pair is not None else None
            )
            ov = overrides.get(tx.id)
            override = (ov["flow_class"], ov["category_id"]) if ov else no_rule
            rule_row = rules.get(tx.key)
            rule = (rule_row["flow_class"], rule_row["category_id"]) if rule_row else no_rule
            owner = accounts[tx.account_id]["ownership"]
            for scope in SCOPES:
                if not _in_scope(scope, owner):
                    continue
                cp_in_scope = counterpart_owner is not None and _in_scope(scope, counterpart_owner)
                auto = _auto(tx, outflow, pair is not None, cp_in_scope, default_cat)
                result = _layered(outflow, auto, override, rule, applies)
                classes[scope][tx.id] = result
                if result[0] == "spending" and tx.amount > 0:
                    rec_rows[scope].append(RecRow(tx.id, tx.date, tx.amount, tx.key, tx.display))

        flagged: dict[str, set[str]] = {}
        group_rows: list[tuple[object, ...]] = []
        for scope in SCOPES:
            ids, groups = detect(rec_rows[scope], rule_recurring, override_recurring, today)
            flagged[scope] = ids
            group_rows.extend(
                (
                    scope,
                    g.merchant_key,
                    g.merchant_display,
                    g.cadence,
                    g.typical_amount,
                    g.last_date,
                    g.count,
                    int(g.active),
                )
                for g in groups
            )

        class_rows: list[tuple[object, ...]] = []
        for tx in txs:
            row: list[object] = [tx.id, tx.key, tx.display]
            for scope in SCOPES:
                scoped = classes[scope].get(tx.id)
                if scoped is None:
                    row += [None, None, None]
                else:
                    row += [scoped[0], scoped[1], int(tx.id in flagged[scope])]
            tx_pair = pairs.get(tx.id)
            row += (
                [None, None]
                if tx_pair is None
                else [tx_pair[0].removeprefix(INV_PREFIX), tx_pair[1]]
            )
            class_rows.append(tuple(row))

        conn.execute("DELETE FROM tx_classification")
        conn.execute("DELETE FROM recurring_groups")
        conn.executemany(
            "INSERT INTO tx_classification(transaction_id, merchant_key, merchant_display,"
            " mine_flow, mine_category_id, mine_recurring, household_flow,"
            " household_category_id, household_recurring, pair_transaction_id, pair_kind)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            class_rows,
        )
        conn.executemany(
            "INSERT INTO recurring_groups(scope, merchant_key, merchant_display, cadence,"
            " typical_amount, last_date, count, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            group_rows,
        )
        return bump_data_version(conn)


def _suspect_card_payments(conn: Connection) -> SuspectCardPayments:
    """Unpaired card-payment outflows naming the institution of a linked credit account."""
    names = {
        str(r[0]).lower()
        for r in conn.execute(
            "SELECT DISTINCT i.institution_name FROM items i JOIN accounts a ON a.item_id ="
            " i.item_id WHERE a.type = 'credit' AND a.included = 1 AND a.duplicate_of_account"
            " IS NULL AND i.institution_name IS NOT NULL AND i.institution_name != ''"
        )
    }
    ids: list[str] = []
    if names:
        for r in conn.execute(
            "SELECT t.transaction_id, t.name, t.merchant_name FROM transactions t"
            " JOIN tx_classification c ON c.transaction_id = t.transaction_id"
            " WHERE c.pair_transaction_id IS NULL AND t.amount > 0 AND t.pfc_detailed = ?"
            " ORDER BY t.date, t.transaction_id",
            (CC_PAYMENT,),
        ):
            text = f"{r['name'] or ''}\n{r['merchant_name'] or ''}".lower()
            if any(n in text for n in names):
                ids.append(r["transaction_id"])
    return {"count": len(ids), "transaction_ids": ids}


def compute_quality(conn: Connection) -> QualityOut:
    duplicates = [
        r[0]
        for r in conn.execute(
            "SELECT account_id FROM accounts WHERE duplicate_of_account IS NOT NULL"
            " ORDER BY account_id"
        )
    ]
    superseded: list[SupersededOut] = [
        {"account_id": r[0], "superseded_by": r[1], "from_date": r[2]}
        for r in conn.execute(
            "SELECT a.account_id, a.superseded_by,"
            " (SELECT MIN(date) FROM transactions t WHERE t.account_id = a.superseded_by)"
            " FROM accounts a WHERE a.superseded_by IS NOT NULL ORDER BY a.account_id"
        )
    ]
    return {
        "suspect_card_payments": _suspect_card_payments(conn),
        "duplicate_accounts": duplicates,
        "superseded_accounts": superseded,
    }
