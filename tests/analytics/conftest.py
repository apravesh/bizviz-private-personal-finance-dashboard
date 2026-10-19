from datetime import date

import pytest

from bizviz.db.connection import Connection
from tests import factories as f

TODAY = date(2026, 10, 15)


class World:
    """Typical household: mine checking+savings+card, a joint checking, brokerage, a loan."""

    chk = "chk"
    sav = "sav"
    joint = "joint"
    cc = "cc"
    brokerage = "brk"
    loan = "loan"

    def __init__(self, db: Connection) -> None:
        self.db = db
        f.make_item(db, "item-bank", institution_name="Bank One")
        f.make_item(db, "item-card", institution_name="Card Co")
        f.make_item(db, "item-brk", institution_name="Broker", link_mode="investment")
        f.make_item(db, "item-loan", institution_name="Lender", link_mode="loan")
        f.make_account(db, self.chk, "item-bank", "depository", "checking")
        f.make_account(db, self.sav, "item-bank", "depository", "savings")
        f.make_account(db, self.joint, "item-bank", "depository", "checking", ownership="joint")
        f.make_account(db, self.cc, "item-card", "credit", "credit card")
        f.make_account(db, self.brokerage, "item-brk", "investment", "brokerage")
        f.make_account(db, self.loan, "item-loan", "loan", "mortgage")

    def tx(
        self,
        tid: str,
        account: str,
        day: str,
        amount: float,
        primary: str | None = None,
        detailed: str | None = None,
        name: str = "",
        **kw: object,
    ) -> str:
        return f.make_txn(
            self.db,
            tid,
            account,
            day,
            amount,
            name=name or tid,
            pfc_primary=primary,
            pfc_detailed=detailed,
            **kw,  # type: ignore[arg-type]
        )


@pytest.fixture
def world(db: Connection) -> World:
    return World(db)


def classification(db: Connection, tid: str) -> dict[str, object]:
    row = db.execute("SELECT * FROM tx_classification WHERE transaction_id = ?", (tid,)).fetchone()
    assert row is not None, f"no classification for {tid}"
    return dict(row)


def scoped(db: Connection, tid: str, scope: str) -> tuple[object, object] | None:
    c = classification(db, tid)
    if c[f"{scope}_flow"] is None:
        return None
    return c[f"{scope}_flow"], c[f"{scope}_category_id"]
