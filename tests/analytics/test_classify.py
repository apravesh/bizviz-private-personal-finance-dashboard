import threading
from collections.abc import Callable
from typing import Any

import pytest

from bizviz.analytics import classify as classify_module
from bizviz.analytics.classify import compute_quality, rebuild_classification
from bizviz.context import AppContext
from bizviz.db.connection import Connection, get_meta
from tests import factories as f
from tests.analytics.conftest import TODAY, World, classification, scoped

CC = "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"

Setup = Callable[[World], str]


def _rule_a(w: World) -> str:
    w.tx("o", w.chk, "2026-02-01", 100, "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER")
    w.tx("i", w.sav, "2026-02-01", -100, "TRANSFER_IN", "TRANSFER_IN_ACCOUNT_TRANSFER")
    return "o"


def _rule_a_inflow_side(w: World) -> str:
    _rule_a(w)
    return "i"


def _rule_b_out(w: World) -> str:
    w.tx("o", w.chk, "2026-02-01", 100, "TRANSFER_OUT")
    w.tx("i", w.joint, "2026-02-01", -100, "TRANSFER_IN")
    return "o"


def _rule_b_in(w: World) -> str:
    w.tx("o", w.joint, "2026-02-01", 80, "TRANSFER_OUT")
    w.tx("i", w.chk, "2026-02-01", -80, "TRANSFER_IN")
    return "i"


def _simple(amount: float, primary: str | None, detailed: str | None = None) -> Setup:
    def setup(w: World) -> str:
        w.tx("t", w.chk, "2026-02-01", amount, primary, detailed)
        return "t"

    return setup


def _rule_a_investment(w: World) -> str:
    f.make_inv_txn(w.db, "dep", w.brokerage, None, "2026-02-02", "cash", "deposit", None, -500)
    w.tx("t", w.chk, "2026-02-01", 500, "TRANSFER_OUT")
    return "t"


def _rule_b_investment_joint(w: World) -> str:
    f.make_account(w.db, "brk-joint", "item-brk", "investment", "ira", ownership="joint")
    f.make_inv_txn(w.db, "dep", "brk-joint", None, "2026-02-02", "cash", "deposit", None, -500)
    w.tx("t", w.chk, "2026-02-01", 500, "TRANSFER_OUT")
    return "t"


RULE_CASES: list[tuple[str, Setup, tuple[str, str], tuple[str, str]]] = [
    ("A-out", _rule_a, ("internal", "sys:transfer"), ("internal", "sys:transfer")),
    ("A-in", _rule_a_inflow_side, ("internal", "sys:transfer"), ("internal", "sys:transfer")),
    (
        "A-investment",
        _rule_a_investment,
        ("internal", "sys:transfer"),
        ("internal", "sys:transfer"),
    ),
    ("B-out", _rule_b_out, ("spending", "sys:to_joint"), ("internal", "sys:transfer")),
    ("B-in", _rule_b_in, ("earning", "sys:from_joint"), ("internal", "sys:transfer")),
    (
        "B-investment",
        _rule_b_investment_joint,
        ("spending", "sys:to_joint"),
        ("internal", "sys:transfer"),
    ),
    (
        "C",
        _simple(100, "LOAN_PAYMENTS", CC),
        ("spending", "sys:cc_unlinked"),
        ("spending", "sys:cc_unlinked"),
    ),
    (
        "D",
        _simple(900, "LOAN_PAYMENTS", "LOAN_PAYMENTS_MORTGAGE_PAYMENT"),
        ("spending", "plaid:LOAN_PAYMENTS"),
        ("spending", "plaid:LOAN_PAYMENTS"),
    ),
    (
        "E-out",
        _simple(100, "TRANSFER_OUT", "TRANSFER_OUT_SAVINGS"),
        ("internal", "sys:transfer"),
        ("internal", "sys:transfer"),
    ),
    (
        "E-in",
        _simple(-100, "TRANSFER_IN", "TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS"),
        ("internal", "sys:transfer"),
        ("internal", "sys:transfer"),
    ),
    (
        "F-payments",
        _simple(-100, "LOAN_PAYMENTS", "LOAN_PAYMENTS_OTHER_PAYMENT"),
        ("internal", "sys:transfer"),
        ("internal", "sys:transfer"),
    ),
    (
        "F-disbursement",
        _simple(-1000, "LOAN_DISBURSEMENTS"),
        ("internal", "sys:transfer"),
        ("internal", "sys:transfer"),
    ),
    (
        "G",
        _simple(-3000, "INCOME", "INCOME_SALARY"),
        ("earning", "plaid:INCOME_SALARY"),
        ("earning", "plaid:INCOME_SALARY"),
    ),
    (
        "G-unknown-detail",
        _simple(-3000, "INCOME", "INCOME_WAGES"),
        ("earning", "plaid:INCOME_OTHER"),
        ("earning", "plaid:INCOME_OTHER"),
    ),
    (
        "H",
        _simple(-100, "TRANSFER_IN", "TRANSFER_IN_ACCOUNT_TRANSFER"),
        ("earning", "sys:money_in_others"),
        ("earning", "sys:money_in_others"),
    ),
    (
        "I",
        _simple(-25, "GENERAL_MERCHANDISE"),
        ("spending", "plaid:GENERAL_MERCHANDISE"),
        ("spending", "plaid:GENERAL_MERCHANDISE"),
    ),
    (
        "I-no-pfc",
        _simple(-25, None),
        ("spending", "sys:uncategorized"),
        ("spending", "sys:uncategorized"),
    ),
    (
        "J",
        _simple(100, "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER"),
        ("spending", "sys:transfers_out"),
        ("spending", "sys:transfers_out"),
    ),
    (
        "K",
        _simple(12.5, "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE"),
        ("spending", "plaid:FOOD_AND_DRINK"),
        ("spending", "plaid:FOOD_AND_DRINK"),
    ),
    (
        "K-zero",
        _simple(0, "FOOD_AND_DRINK"),
        ("spending", "plaid:FOOD_AND_DRINK"),
        ("spending", "plaid:FOOD_AND_DRINK"),
    ),
    (
        "K-no-pfc",
        _simple(5, None),
        ("spending", "sys:uncategorized"),
        ("spending", "sys:uncategorized"),
    ),
]


@pytest.mark.parametrize(
    ("setup", "mine", "household"),
    [pytest.param(s, m, h, id=i) for i, s, m, h in RULE_CASES],
)
def test_automatic_rules_per_scope(
    db: Connection, world: World, setup: Setup, mine: tuple[str, str], household: tuple[str, str]
) -> None:
    tid = setup(world)
    rebuild_classification(db, TODAY)
    assert scoped(db, tid, "mine") == mine
    assert scoped(db, tid, "household") == household


def test_rule_b_joint_transactions_have_no_mine_scope(db: Connection, world: World) -> None:
    _rule_b_out(world)
    rebuild_classification(db, TODAY)
    assert scoped(db, "i", "mine") is None
    assert scoped(db, "i", "household") == ("internal", "sys:transfer")
    c = classification(db, "i")
    assert c["mine_recurring"] is None and c["mine_category_id"] is None


def test_rule_b_inflow_in_mine_only_when_inflow_account_is_mine(
    db: Connection, world: World
) -> None:
    _rule_b_out(world)
    _rule_b_in_other = world.tx("o2", world.joint, "2026-03-01", 80, "TRANSFER_OUT")
    world.tx("i2", world.chk, "2026-03-01", -80, "TRANSFER_IN")
    rebuild_classification(db, TODAY)
    assert scoped(db, "i2", "mine") == ("earning", "sys:from_joint")
    assert scoped(db, _rule_b_in_other, "mine") is None


def test_joint_account_rules_household_only(db: Connection, world: World) -> None:
    world.tx("k", world.joint, "2026-02-01", 40, "FOOD_AND_DRINK")
    world.tx("g", world.joint, "2026-02-01", -400, "INCOME", "INCOME_SALARY")
    rebuild_classification(db, TODAY)
    assert scoped(db, "k", "mine") is None
    assert scoped(db, "k", "household") == ("spending", "plaid:FOOD_AND_DRINK")
    assert scoped(db, "g", "household") == ("earning", "plaid:INCOME_SALARY")


def test_card_payment_pair_is_internal(db: Connection, world: World) -> None:
    world.tx("pay", world.chk, "2026-02-01", 300, "LOAN_PAYMENTS", CC)
    world.tx("rcv", world.cc, "2026-02-02", -300, "LOAN_PAYMENTS", CC)
    rebuild_classification(db, TODAY)
    assert scoped(db, "pay", "mine") == ("internal", "sys:transfer")
    assert scoped(db, "rcv", "household") == ("internal", "sys:transfer")


def test_loan_account_and_investment_transactions_not_classified(
    db: Connection, world: World
) -> None:
    world.tx("loan-tx", world.loan, "2026-02-01", 50, "LOAN_PAYMENTS")
    world.tx("brk-tx", world.brokerage, "2026-02-01", 50)
    world.tx("ok", world.chk, "2026-02-01", 5)
    rebuild_classification(db, TODAY)
    rows = {r[0] for r in db.execute("SELECT transaction_id FROM tx_classification")}
    assert rows == {"ok"}


def test_pending_transactions_participate(db: Connection, world: World) -> None:
    world.tx("o", world.chk, "2026-02-01", 100, "TRANSFER_OUT", pending=True)
    world.tx("i", world.sav, "2026-02-01", -100, "TRANSFER_IN", pending=True)
    rebuild_classification(db, TODAY)
    assert scoped(db, "o", "mine") == ("internal", "sys:transfer")


# ---- layer precedence ----------------------------------------------------------------------


def _k(world: World, **kw: Any) -> str:
    return world.tx("t", world.chk, "2026-02-01", 20, "FOOD_AND_DRINK", name="Cafe", **kw)


def test_override_category_only_keeps_flow_from_auto(db: Connection, world: World) -> None:
    _k(world)
    f.make_override(db, "t", category_id="plaid:TRAVEL")
    rebuild_classification(db, TODAY)
    assert scoped(db, "t", "mine") == ("spending", "plaid:TRAVEL")


def test_override_category_only_keeps_flow_from_rule(db: Connection, world: World) -> None:
    _k(world)
    f.make_rule(db, "cafe", flow_class="internal")
    f.make_override(db, "t", category_id="plaid:TRAVEL")
    rebuild_classification(db, TODAY)
    assert scoped(db, "t", "mine") == ("internal", "plaid:TRAVEL")


def test_override_flow_beats_rule_flow(db: Connection, world: World) -> None:
    _k(world)
    f.make_rule(db, "cafe", flow_class="internal")
    f.make_override(db, "t", flow_class="spending")
    rebuild_classification(db, TODAY)
    assert scoped(db, "t", "mine") == ("spending", "plaid:FOOD_AND_DRINK")


def test_rule_earning_flow_ignored_for_outflow_but_applies_to_inflow(
    db: Connection, world: World
) -> None:
    _k(world)
    world.tx("in", world.chk, "2026-02-02", -20, "GENERAL_MERCHANDISE", name="Cafe")
    f.make_rule(db, "cafe", flow_class="earning")
    rebuild_classification(db, TODAY)
    assert scoped(db, "t", "mine") == ("spending", "plaid:FOOD_AND_DRINK")
    assert scoped(db, "in", "mine") == ("earning", "plaid:INCOME_OTHER")


@pytest.mark.parametrize("flow", ["spending", "internal", "ignored"])
def test_rule_flows_apply_to_inflows_too(db: Connection, world: World, flow: str) -> None:
    world.tx("in", world.chk, "2026-02-02", -20, "TRANSFER_IN", name="Cafe")
    f.make_rule(db, "cafe", flow_class=flow)
    rebuild_classification(db, TODAY)
    got = scoped(db, "in", "mine")
    assert got is not None and got[0] == flow
    if flow == "spending":
        assert got[1] == "plaid:OTHER"
    else:
        assert got[1] == "sys:transfer"


def test_rule_category_applies_only_when_it_fits_final_flow(db: Connection, world: World) -> None:
    _k(world)
    f.make_rule(db, "cafe", category_id="plaid:INCOME_SALARY")
    rebuild_classification(db, TODAY)
    assert scoped(db, "t", "mine") == ("spending", "plaid:FOOD_AND_DRINK")


def test_rule_category_applies_when_it_fits(db: Connection, world: World) -> None:
    _k(world)
    f.make_rule(db, "cafe", category_id="plaid:TRAVEL")
    rebuild_classification(db, TODAY)
    assert scoped(db, "t", "mine") == ("spending", "plaid:TRAVEL")


def test_rule_category_kept_on_internal_flow(db: Connection, world: World) -> None:
    _k(world)
    f.make_rule(db, "cafe", category_id="plaid:INCOME_SALARY", flow_class="internal")
    rebuild_classification(db, TODAY)
    assert scoped(db, "t", "mine") == ("internal", "plaid:INCOME_SALARY")


def test_user_category_kept_even_if_applies_to_mismatches(db: Connection, world: World) -> None:
    _k(world)
    f.make_override(db, "t", category_id="plaid:INCOME_SALARY")
    rebuild_classification(db, TODAY)
    assert scoped(db, "t", "mine") == ("spending", "plaid:INCOME_SALARY")


def test_auto_category_replaced_when_it_does_not_fit_final_flow(
    db: Connection, world: World
) -> None:
    _k(world)
    world.tx("pay", world.chk, "2026-02-01", -3000, "INCOME", "INCOME_SALARY")
    f.make_override(db, "t", flow_class="earning")
    f.make_override(db, "pay", flow_class="spending")
    rebuild_classification(db, TODAY)
    assert scoped(db, "t", "mine") == ("earning", "plaid:INCOME_OTHER")
    assert scoped(db, "pay", "mine") == ("spending", "plaid:OTHER")


def test_internal_and_ignored_get_transfer_category_unless_user_set(
    db: Connection, world: World
) -> None:
    _k(world)
    world.tx("u", world.chk, "2026-02-03", 9, "FOOD_AND_DRINK", name="Other")
    f.make_override(db, "t", flow_class="ignored")
    f.make_override(db, "u", flow_class="internal", category_id="plaid:TRAVEL")
    rebuild_classification(db, TODAY)
    assert scoped(db, "t", "mine") == ("ignored", "sys:transfer")
    assert scoped(db, "u", "mine") == ("internal", "plaid:TRAVEL")


def test_merchant_key_and_display_most_recent(db: Connection, world: World) -> None:
    world.tx("a", world.chk, "2026-01-01", 5, name="SQ *Blue Bottle 12")
    world.tx("b", world.chk, "2026-02-01", 5, name="Blue Bottle #77", merchant_name="Blue Bottle")
    world.tx(
        "c", world.chk, "2026-02-01", 5, name="SQ *BLUE BOTTLE 99", merchant_name="blue bottle"
    )
    rebuild_classification(db, TODAY)
    keys = {t: classification(db, t)["merchant_key"] for t in "abc"}
    assert set(keys.values()) == {"blue bottle"}
    assert classification(db, "a")["merchant_display"] == "blue bottle"


# ---- exclusions ----------------------------------------------------------------------------


def test_superseded_account_exclusion(db: Connection, world: World) -> None:
    f.make_account(db, "old", "item-bank", superseded_by="new")
    f.make_account(db, "new", "item-bank")
    world.tx("o1", "old", "2026-02-28", 5)
    world.tx("o2", "old", "2026-03-01", 5)
    world.tx("o3", "old", "2026-03-05", 5)
    world.tx("n1", "new", "2026-03-01", 5)
    rebuild_classification(db, TODAY)
    rows = {r[0] for r in db.execute("SELECT transaction_id FROM tx_classification")}
    assert rows == {"o1", "n1"}


def test_superseded_without_new_transactions_excludes_nothing(db: Connection, world: World) -> None:
    f.make_account(db, "old", "item-bank", superseded_by="new")
    f.make_account(db, "new", "item-bank")
    world.tx("o1", "old", "2026-03-05", 5)
    rebuild_classification(db, TODAY)
    assert {r[0] for r in db.execute("SELECT transaction_id FROM tx_classification")} == {"o1"}


def test_excluded_and_duplicate_accounts_not_classified(db: Connection, world: World) -> None:
    f.make_account(db, "off", "item-bank", included=0)
    f.make_account(db, "dup", "item-bank", duplicate_of_account="chk")
    world.tx("x1", "off", "2026-03-01", 5)
    world.tx("x2", "dup", "2026-03-01", 5)
    world.tx("ok", world.chk, "2026-03-01", 5)
    rebuild_classification(db, TODAY)
    assert {r[0] for r in db.execute("SELECT transaction_id FROM tx_classification")} == {"ok"}


def test_removed_item_accounts_stay_in(db: Connection, world: World) -> None:
    f.make_item(db, "gone-item", removed_at="2026-05-01T00:00:00Z")
    f.make_account(db, "gone-acc", "gone-item")
    world.tx("g", "gone-acc", "2026-03-01", 5)
    rebuild_classification(db, TODAY)
    assert scoped(db, "g", "mine") is not None


def test_excluded_transfer_counterpart_does_not_pair(db: Connection, world: World) -> None:
    f.make_account(db, "off", "item-bank", included=0)
    world.tx("o", world.chk, "2026-03-01", 100, "TRANSFER_OUT")
    world.tx("i", "off", "2026-03-01", -100, "TRANSFER_IN")
    rebuild_classification(db, TODAY)
    assert classification(db, "o")["pair_transaction_id"] is None
    assert scoped(db, "o", "mine") == ("spending", "sys:transfers_out")


# ---- quality -------------------------------------------------------------------------------


def test_suspect_card_payments(db: Connection, world: World) -> None:
    world.tx("s1", world.chk, "2026-02-01", 100, "LOAN_PAYMENTS", CC, name="CARD CO AUTOPAY")
    world.tx(
        "s2", world.chk, "2026-02-02", 100, "LOAN_PAYMENTS", CC, name="X", merchant_name="Card Co"
    )
    world.tx("n1", world.chk, "2026-02-03", 100, "LOAN_PAYMENTS", CC, name="Unlinked Bank Payment")
    world.tx("p1", world.chk, "2026-02-04", 70, "LOAN_PAYMENTS", CC, name="CARD CO PAYMENT")
    world.tx("p2", world.cc, "2026-02-04", -70, "LOAN_PAYMENTS", CC, name="PAYMENT THANK YOU")
    world.tx("k", world.chk, "2026-02-05", 10, "FOOD_AND_DRINK", name="Card Co Cafe")
    rebuild_classification(db, TODAY)
    q = compute_quality(db)["suspect_card_payments"]
    assert q == {"count": 2, "transaction_ids": ["s1", "s2"]}
    assert scoped(db, "s1", "mine") == ("spending", "sys:cc_unlinked")


def test_suspect_requires_linked_credit_account(db: Connection, world: World) -> None:
    db.execute("DELETE FROM accounts WHERE account_id = 'cc'")
    world.tx("s1", world.chk, "2026-02-01", 100, "LOAN_PAYMENTS", CC, name="CARD CO AUTOPAY")
    rebuild_classification(db, TODAY)
    assert compute_quality(db)["suspect_card_payments"]["count"] == 0


def test_quality_duplicates_and_superseded(db: Connection, world: World) -> None:
    f.make_account(db, "dup", "item-bank", duplicate_of_account="chk")
    f.make_account(db, "old", "item-bank", superseded_by="new")
    f.make_account(db, "new", "item-bank")
    f.make_account(db, "old2", "item-bank", superseded_by="empty")
    f.make_account(db, "empty", "item-bank")
    world.tx("n1", "new", "2026-03-01", 5)
    q = compute_quality(db)
    assert q["duplicate_accounts"] == ["dup"]
    assert q["superseded_accounts"] == [
        {"account_id": "old", "superseded_by": "new", "from_date": "2026-03-01"},
        {"account_id": "old2", "superseded_by": "empty", "from_date": None},
    ]


# ---- rebuild mechanics ---------------------------------------------------------------------


def test_rebuild_bumps_data_version_and_is_idempotent(db: Connection, world: World) -> None:
    world.tx("t", world.chk, "2026-02-01", 5, "FOOD_AND_DRINK")
    v1 = rebuild_classification(db, TODAY)
    snapshot = [tuple(r) for r in db.execute("SELECT * FROM tx_classification")]
    v2 = rebuild_classification(db, TODAY)
    assert v2 == v1 + 1 == int(get_meta(db, "data_version") or 0)
    assert [tuple(r) for r in db.execute("SELECT * FROM tx_classification")] == snapshot


def test_rebuild_failure_rolls_back(
    db: Connection, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.tx("t", world.chk, "2026-02-01", 5)
    rebuild_classification(db, TODAY)
    before = get_meta(db, "data_version")

    def boom(_: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(classify_module, "match_transfers", boom)
    with pytest.raises(RuntimeError):
        rebuild_classification(db, TODAY)
    assert get_meta(db, "data_version") == before
    assert db.execute("SELECT COUNT(*) FROM tx_classification").fetchone()[0] == 1


def test_lost_update_protection(
    app_ctx: AppContext, db: Connection, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bizviz.analytics import edits

    world.tx("t", world.chk, "2026-02-01", 5, "FOOD_AND_DRINK")
    rebuild_classification(db, TODAY)

    real = classify_module.match_transfers
    entered, release = threading.Event(), threading.Event()
    state = {"first": True}

    def slow(sides: Any) -> Any:
        if state["first"]:
            state["first"] = False
            entered.set()
            assert release.wait(10)
        return real(sides)

    monkeypatch.setattr(classify_module, "match_transfers", slow)
    errors: list[BaseException] = []

    def run(fn: Callable[[Connection], object]) -> None:
        conn = app_ctx.connect()
        try:
            fn(conn)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            conn.close()

    rebuilder = threading.Thread(target=run, args=(lambda c: rebuild_classification(c, TODAY),))
    rebuilder.start()
    assert entered.wait(10)
    editor = threading.Thread(
        target=run,
        args=(lambda c: edits.set_override(c, "t", {"category_id": "plaid:TRAVEL"}, TODAY),),
    )
    editor.start()
    editor.join(0.3)
    assert editor.is_alive()  # blocked behind the rebuild's write unit
    release.set()
    rebuilder.join(10)
    editor.join(10)
    assert not errors
    assert scoped(db, "t", "mine") == ("spending", "plaid:TRAVEL")
