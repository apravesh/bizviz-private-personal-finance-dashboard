import json
from typing import Any, get_type_hints

from bizviz.analytics.classify import rebuild_classification
from bizviz.analytics.dataset import build_dataset, compute_status
from bizviz.api.schema import DataPayload
from bizviz.db.connection import Connection, get_meta
from tests import factories as f
from tests.analytics.conftest import TODAY, World


def populate(db: Connection, w: World) -> None:
    w.tx(
        "t1",
        w.chk,
        "2026-10-01",
        12.5,
        "FOOD_AND_DRINK",
        "FOOD_AND_DRINK_COFFEE",
        name="Cafe",
        merchant_name="Cafe",
    )
    w.tx("t2", w.joint, "2026-10-02", 40, "GENERAL_MERCHANDISE", name="Store", pending=True)
    w.tx("loan-tx", w.loan, "2026-10-02", 40, "LOAN_PAYMENTS")
    w.tx("brk-tx", w.brokerage, "2026-10-02", 1)
    f.make_override(db, "t1", category_id="plaid:TRAVEL", recurring=True)
    f.make_rule(db, "store", flow_class="spending", recurring=False)
    f.make_balance_snapshot(db, w.chk, "2026-10-14", 500)
    f.make_balance_snapshot(db, w.cc, "2026-10-14", 70)
    f.make_balance_snapshot(db, w.brokerage, "2026-10-01", 900)
    f.make_balance_snapshot(db, w.brokerage, "2026-10-14", 1000)
    f.make_security(db, "sec1", "ABC", name="ABC Corp")
    f.make_holding_snapshot(db, w.brokerage, "sec1", "2026-10-05", 1, 800, 800)
    f.make_holding_snapshot(db, w.brokerage, "sec1", "2026-10-14", 2, 500, 1000)
    db.execute(
        "INSERT INTO investment_estimates(account_id, date, value) VALUES (?, ?, ?)",
        (w.brokerage, "2026-09-01", 700.0),
    )
    db.execute(
        "INSERT INTO investment_estimate_meta(account_id, anchor_date, window_start,"
        " window_end, excluded_count, excluded_value, warnings, status)"
        " VALUES (?, '2026-10-05', '2026-09-01', '2026-10-05', 1, 12.5, ?, 'ok')",
        (w.brokerage, json.dumps(["w1"])),
    )
    db.execute(
        "UPDATE items SET last_success_at = '2026-10-14T10:00:00Z' WHERE item_id = 'item-bank'"
    )
    rebuild_classification(db, TODAY)


def check_type(value: Any, hint: Any, path: str) -> None:
    """Recursively check a value against a TypedDict-based annotation (keys exact, types right)."""
    import types
    import typing

    origin = typing.get_origin(hint)
    if origin in (typing.Union, types.UnionType):
        args = typing.get_args(hint)
        if value is None:
            assert type(None) in args, f"{path}: None not allowed"
            return
        (inner,) = [a for a in args if a is not type(None)]
        return check_type(value, inner, path)
    if typing.is_typeddict(hint):
        assert isinstance(value, dict), path
        hints = get_type_hints(hint)
        assert set(value) == set(hints), f"{path}: keys {set(value) ^ set(hints)}"
        for k, h in hints.items():
            check_type(value[k], h, f"{path}.{k}")
    elif origin is list:
        assert isinstance(value, list), path
        for i, v in enumerate(value):
            check_type(v, typing.get_args(hint)[0], f"{path}[{i}]")
    elif origin is tuple:
        assert isinstance(value, tuple) and len(value) == 2, path
        for v, h in zip(value, typing.get_args(hint), strict=True):
            check_type(v, h, path)
    elif hint is float:
        assert isinstance(value, int | float) and not isinstance(value, bool), path
    elif hint is int:
        assert isinstance(value, int) and not isinstance(value, bool), path
    else:
        assert isinstance(value, hint), f"{path}: {value!r} is not {hint}"


def test_payload_has_every_key_with_correct_types(db: Connection, world: World) -> None:
    populate(db, world)
    payload = build_dataset(db, TODAY)
    check_type(payload, DataPayload, "payload")
    json.dumps(payload)
    assert payload["generated_at"]


def test_empty_database_payload(db: Connection) -> None:
    payload = build_dataset(db, TODAY)
    check_type(payload, DataPayload, "payload")
    assert payload["transactions"] == [] and payload["data_version"] == 0
    assert payload["net_worth"] == {"starts_on": None, "series": []}
    assert len(payload["categories"]) > 30


def test_transactions_only_cash_flow_accounts(db: Connection, world: World) -> None:
    populate(db, world)
    by_id = {t["id"]: t for t in build_dataset(db, TODAY)["transactions"]}
    assert set(by_id) == {"t1", "t2"}
    t1, t2 = by_id["t1"], by_id["t2"]
    assert t1["mine"] == {"flow": "spending", "category_id": "plaid:TRAVEL", "recurring": True}
    assert t1["household"] is not None
    assert t1["override"] == {"category_id": "plaid:TRAVEL", "flow_class": None, "recurring": True}
    assert (
        t1["merchant_key"] == "cafe" and t1["merchant_display"] == "Cafe" and t1["pending"] is False
    )
    assert t2["mine"] is None and t2["household"]["flow"] == "spending"
    assert t2["override"] is None and t2["pending"] is True
    assert t1["pfc_detailed"] == "FOOD_AND_DRINK_COFFEE" and t2["pfc_detailed"] is None


def test_accounts_rules_categories_status(db: Connection, world: World) -> None:
    populate(db, world)
    f.make_item(db, "item-removed", institution_name="Gone", removed_at="2026-01-01T00:00:00Z")
    f.make_account(db, "ra", "item-removed")
    payload = build_dataset(db, TODAY)
    accounts = {a["account_id"]: a for a in payload["accounts"]}
    assert accounts["chk"]["institution_name"] == "Bank One" and accounts["chk"]["removed"] is False
    assert accounts["ra"]["removed"] is True and accounts["chk"]["included"] is True
    assert payload["rules"] == [
        {
            "rule_id": 1,
            "merchant_key": "store",
            "category_id": None,
            "flow_class": "spending",
            "recurring": False,
        }
    ]
    items = {i["item_id"]: i for i in payload["status"]["items"]}
    assert items["item-removed"]["removed"] is True and items["item-bank"]["removed"] is False
    assert payload["status"]["newest_data_at"] == "2026-10-14T12:00:00Z"
    assert {c["category_id"] for c in payload["categories"]} >= {"sys:transfer", "plaid:OTHER"}


def test_investments_section(db: Connection, world: World) -> None:
    populate(db, world)
    (inv,) = build_dataset(db, TODAY)["investments"]
    assert inv["account_id"] == "brk" and inv["ownership"] == "mine"
    assert inv["actual"] == [("2026-10-14", 1000)]  # starts at the first holdings snapshot
    assert inv["estimated"] == [("2026-09-01", 700)]
    assert inv["estimate_meta"] == {
        "anchor_date": "2026-10-05",
        "window_start": "2026-09-01",
        "window_end": "2026-10-05",
        "excluded_count": 1,
        "excluded_value": 12.5,
        "warnings": ["w1"],
        "status": "ok",
    }
    assert inv["holdings"] == [
        {
            "security_name": "ABC Corp",
            "ticker": "ABC",
            "quantity": 2,
            "price": 500,
            "value": 1000,
            "price_as_of": None,
        }
    ]


def test_investment_flows_are_net_money_in_per_day(db: Connection, world: World) -> None:
    f.make_security(db, "sec-abc", "ABC")
    f.make_security(db, "sec-mm", "MMF", type="cash", is_cash_equivalent=True)
    txn = f.make_inv_txn
    txn(db, "d1", "brk", None, "2026-03-01", "cash", "deposit", None, -500)
    txn(db, "d2", "brk", None, "2026-03-01", "cash", "contribution", None, -100)
    txn(db, "w1", "brk", None, "2026-04-01", "cash", "withdrawal", None, 200)
    txn(db, "t1", "brk", "sec-mm", "2026-05-01", "transfer", "transfer", None, -50)
    # Not money moving between accounts: gains, trades, fees and in-kind share transfers.
    txn(db, "div", "brk", None, "2026-03-02", "cash", "dividend", None, -7)
    txn(db, "buy", "brk", "sec-abc", "2026-03-03", "buy", "buy", 1, 300)
    txn(db, "fee", "brk", None, "2026-03-04", "fee", "account fee", None, 5)
    txn(db, "kind", "brk", "sec-abc", "2026-03-05", "transfer", "transfer", 1, -300)
    (inv,) = build_dataset(db, TODAY)["investments"]
    assert inv["flows"] == [("2026-03-01", 600), ("2026-04-01", -200), ("2026-05-01", 50)]


def test_investment_actual_without_holdings_uses_all_snapshots_up_to_cutoff(
    db: Connection, world: World
) -> None:
    db.execute("UPDATE accounts SET gone_at = '2026-10-02T00:00:00Z' WHERE account_id = 'brk'")
    for d in ("2026-10-01", "2026-10-02", "2026-10-03"):
        f.make_balance_snapshot(db, "brk", d, 10)
    (inv,) = build_dataset(db, TODAY)["investments"]
    assert [d for d, _ in inv["actual"]] == ["2026-10-01", "2026-10-02"]
    assert inv["estimate_meta"] is None and inv["holdings"] == []


def test_recurring_ordering_active_first(db: Connection, world: World) -> None:
    from datetime import timedelta

    for name, last_gap in (("Old Sub", 200), ("New Sub", 5), ("Newer Sub", 1)):
        for i in range(4):
            d = TODAY - timedelta(days=last_gap + 30 * (3 - i))
            world.tx(f"{name}{i}", world.chk, d.isoformat(), 9.0, "ENTERTAINMENT", name=name)
    rebuild_classification(db, TODAY)
    groups = build_dataset(db, TODAY)["recurring"]["mine"]
    assert [(g["merchant_key"], g["active"]) for g in groups] == [
        ("newer sub", True),
        ("new sub", True),
        ("old sub", False),
    ]


def test_data_version_matches_meta_and_status(db: Connection, world: World) -> None:
    populate(db, world)
    payload = build_dataset(db, TODAY)
    assert payload["data_version"] == int(get_meta(db, "data_version") or 0)
    status = compute_status(db)
    assert status["data_version"] == payload["data_version"]
    assert status["newest_data_at"] == payload["status"]["newest_data_at"]
    assert status["items"] == payload["status"]["items"]
    assert not db.in_transaction


def test_compute_status_inside_open_transaction(db: Connection, world: World) -> None:
    from bizviz.db.connection import read_unit

    populate(db, world)
    with read_unit(db):
        status = compute_status(db)
    assert status["data_version"] == int(get_meta(db, "data_version") or 0)
