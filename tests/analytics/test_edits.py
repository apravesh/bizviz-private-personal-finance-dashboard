import pytest

from bizviz.analytics import edits
from bizviz.analytics.classify import rebuild_classification
from bizviz.analytics.edits import DuplicateRuleError
from bizviz.db.connection import Connection, get_meta
from bizviz.errors import InvalidInputError, NotFoundError
from tests import factories as f
from tests.analytics.conftest import TODAY, World, scoped


def version(db: Connection) -> int:
    return int(get_meta(db, "data_version") or 0)


def override_row(db: Connection, tid: str) -> dict[str, object] | None:
    row = db.execute("SELECT * FROM tx_overrides WHERE transaction_id = ?", (tid,)).fetchone()
    return None if row is None else dict(row)


@pytest.fixture
def cafe(db: Connection, world: World) -> str:
    world.tx("t", world.chk, "2026-10-01", 20, "FOOD_AND_DRINK", name="Cafe 12")
    rebuild_classification(db, TODAY)
    return "t"


# ---- overrides -----------------------------------------------------------------------------


def test_set_override_sets_clears_and_leaves_absent(db: Connection, cafe: str) -> None:
    v0 = version(db)
    v1 = edits.set_override(
        db, cafe, {"category_id": "plaid:TRAVEL", "flow_class": "spending"}, TODAY
    )
    assert v1 > v0
    assert version(db) == v1
    assert scoped(db, cafe, "mine") == ("spending", "plaid:TRAVEL")
    edits.set_override(db, cafe, {"recurring": True}, TODAY)
    row = override_row(db, cafe)
    assert row is not None
    assert (row["category_id"], row["flow_class"], row["recurring"]) == (
        "plaid:TRAVEL",
        "spending",
        1,
    )
    edits.set_override(db, cafe, {"category_id": None}, TODAY)
    row = override_row(db, cafe)
    assert row is not None
    assert (row["category_id"], row["flow_class"], row["recurring"]) == (None, "spending", 1)
    assert scoped(db, cafe, "mine") == ("spending", "plaid:FOOD_AND_DRINK")


def test_set_override_all_null_deletes_row_and_bumps_version(db: Connection, cafe: str) -> None:
    edits.set_override(db, cafe, {"flow_class": "ignored"}, TODAY)
    assert scoped(db, cafe, "mine") == ("ignored", "sys:transfer")
    before = version(db)
    edits.set_override(db, cafe, {"flow_class": None}, TODAY)
    assert override_row(db, cafe) is None
    assert version(db) > before
    assert scoped(db, cafe, "mine") == ("spending", "plaid:FOOD_AND_DRINK")
    edits.set_override(db, cafe, {}, TODAY)  # no-op on a missing row
    assert override_row(db, cafe) is None


def test_set_override_recurring_false_is_stored(db: Connection, cafe: str) -> None:
    edits.set_override(db, cafe, {"recurring": False}, TODAY)
    row = override_row(db, cafe)
    assert row is not None and row["recurring"] == 0


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"category_id": "nope"}, "unknown_category"),
        ({"flow_class": "bogus"}, "invalid_flow"),
        ({"recurring": "yes"}, "invalid_recurring"),
        ({"recurring": 1}, "invalid_recurring"),
    ],
)
def test_set_override_validation(
    db: Connection, cafe: str, changes: dict[str, object], code: str
) -> None:
    before = version(db)
    with pytest.raises(InvalidInputError) as exc:
        edits.set_override(db, cafe, changes, TODAY)
    assert exc.value.code == code
    assert override_row(db, cafe) is None and version(db) == before


def test_set_override_unknown_transaction(db: Connection, world: World) -> None:
    with pytest.raises(NotFoundError):
        edits.set_override(db, "missing", {"flow_class": "ignored"}, TODAY)


def test_set_override_pending_allowed(db: Connection, world: World) -> None:
    world.tx("p", world.chk, "2026-10-01", 5, pending=True)
    edits.set_override(db, "p", {"category_id": "plaid:TRAVEL"}, TODAY)
    assert scoped(db, "p", "mine") == ("spending", "plaid:TRAVEL")


# ---- apply to merchant ---------------------------------------------------------------------


def test_apply_to_merchant_creates_rule_and_clears_override_fields(
    db: Connection, cafe: str
) -> None:
    world_tx = f.make_txn(
        db, "other", "chk", "2026-10-02", 8, name="CAFE 99", pfc_primary="FOOD_AND_DRINK"
    )
    f.make_override(db, "other", category_id="plaid:MEDICAL")
    edits.set_override(db, cafe, {"category_id": "plaid:TRAVEL", "flow_class": "spending"}, TODAY)
    rule_id, v = edits.apply_to_merchant(db, cafe, {"category_id": "plaid:TRAVEL"}, TODAY)
    assert v == version(db)
    rule = db.execute("SELECT * FROM merchant_rules WHERE rule_id = ?", (rule_id,)).fetchone()
    assert (rule["merchant_key"], rule["category_id"], rule["flow_class"]) == (
        "cafe",
        "plaid:TRAVEL",
        None,
    )
    row = override_row(db, cafe)
    assert row is not None and (row["category_id"], row["flow_class"]) == (None, "spending")
    override = override_row(db, world_tx)
    assert override is not None and override["category_id"] == "plaid:MEDICAL"
    assert scoped(db, cafe, "mine") == ("spending", "plaid:TRAVEL")


def test_apply_to_merchant_updates_existing_rule_and_deletes_empty_override(
    db: Connection, cafe: str
) -> None:
    rule_id = f.make_rule(db, "cafe", category_id="plaid:TRAVEL", recurring=True)
    f.make_override(db, cafe, flow_class="internal")
    rid, _ = edits.apply_to_merchant(db, cafe, {"flow_class": "ignored", "recurring": None}, TODAY)
    assert rid == rule_id
    rule = db.execute("SELECT * FROM merchant_rules").fetchone()
    assert (rule["category_id"], rule["flow_class"], rule["recurring"]) == (
        "plaid:TRAVEL",
        "ignored",
        None,
    )
    assert override_row(db, cafe) is None
    assert db.execute("SELECT COUNT(*) FROM merchant_rules").fetchone()[0] == 1
    assert scoped(db, cafe, "mine") == ("ignored", "plaid:TRAVEL")


def test_apply_to_merchant_pending_refused(db: Connection, world: World) -> None:
    world.tx("p", world.chk, "2026-10-01", 5, pending=True)
    before = version(db)
    with pytest.raises(InvalidInputError) as exc:
        edits.apply_to_merchant(db, "p", {"flow_class": "ignored"}, TODAY)
    assert exc.value.code == "pending"
    assert db.execute("SELECT COUNT(*) FROM merchant_rules").fetchone()[0] == 0
    assert version(db) == before


def test_apply_to_merchant_errors(db: Connection, cafe: str) -> None:
    with pytest.raises(NotFoundError):
        edits.apply_to_merchant(db, "missing", {"flow_class": "ignored"}, TODAY)
    with pytest.raises(InvalidInputError) as exc:
        edits.apply_to_merchant(db, cafe, {"category_id": "nope"}, TODAY)
    assert exc.value.code == "unknown_category"
    assert db.execute("SELECT COUNT(*) FROM merchant_rules").fetchone()[0] == 0


# ---- rules ---------------------------------------------------------------------------------


def test_rules_crud_rebuilds(db: Connection, cafe: str) -> None:
    v0 = version(db)
    rule = edits.create_rule(db, "Cafe", {"flow_class": "ignored", "recurring": True}, TODAY)
    assert (
        rule["merchant_key"] == "cafe" and rule["recurring"] is True and rule["category_id"] is None
    )
    assert version(db) > v0
    assert scoped(db, cafe, "mine") == ("ignored", "sys:transfer")
    assert edits.list_rules(db) == [rule]

    updated = edits.update_rule(
        db, rule["rule_id"], {"flow_class": None, "category_id": "plaid:TRAVEL"}, TODAY
    )
    assert (updated["flow_class"], updated["category_id"], updated["recurring"]) == (
        None,
        "plaid:TRAVEL",
        True,
    )
    assert scoped(db, cafe, "mine") == ("spending", "plaid:TRAVEL")

    v1 = version(db)
    edits.delete_rule(db, rule["rule_id"], TODAY)
    assert version(db) > v1
    assert edits.list_rules(db) == []
    assert scoped(db, cafe, "mine") == ("spending", "plaid:FOOD_AND_DRINK")


def test_create_rule_duplicate(db: Connection, cafe: str) -> None:
    edits.create_rule(db, "cafe", {}, TODAY)
    with pytest.raises(DuplicateRuleError):
        edits.create_rule(db, " CAFE ", {"flow_class": "ignored"}, TODAY)


def test_rule_errors(db: Connection, cafe: str) -> None:
    with pytest.raises(InvalidInputError) as exc:
        edits.create_rule(db, "x", {"flow_class": "bogus"}, TODAY)
    assert exc.value.code == "invalid_flow"
    with pytest.raises(InvalidInputError):
        edits.create_rule(db, "  ", {}, TODAY)
    with pytest.raises(NotFoundError):
        edits.update_rule(db, 99, {}, TODAY)
    with pytest.raises(NotFoundError):
        edits.delete_rule(db, 99, TODAY)


# ---- categories ----------------------------------------------------------------------------


def test_create_custom_categories_numbered(db: Connection) -> None:
    v0 = version(db)
    a = edits.create_category(db, " Pets ", "spending", TODAY)
    b = edits.create_category(db, "Side gig", "earning", TODAY)
    assert a == {
        "category_id": "custom:1",
        "name": "Pets",
        "kind": "custom",
        "applies_to": "spending",
        "hidden": False,
    }
    assert b["category_id"] == "custom:2"
    assert version(db) == v0 + 2
    db.execute("DELETE FROM categories WHERE category_id = 'custom:1'")
    assert edits.create_category(db, "Again", "both", TODAY)["category_id"] == "custom:3"


@pytest.mark.parametrize(
    ("name", "applies_to", "code"),
    [
        ("X", "transfer", "invalid_applies_to"),
        ("X", "other", "invalid_applies_to"),
        ("  ", "both", "invalid_name"),
    ],
)
def test_create_category_validation(db: Connection, name: str, applies_to: str, code: str) -> None:
    with pytest.raises(InvalidInputError) as exc:
        edits.create_category(db, name, applies_to, TODAY)
    assert exc.value.code == code


def test_update_category(db: Connection) -> None:
    custom = edits.create_category(db, "Pets", "spending", TODAY)["category_id"]
    renamed = edits.update_category(db, custom, name="Animals", today=TODAY)
    assert renamed["name"] == "Animals"
    v0 = version(db)
    hidden = edits.update_category(db, "plaid:TRAVEL", hidden=True, today=TODAY)
    assert hidden["hidden"] is True and version(db) > v0
    assert edits.update_category(db, "plaid:TRAVEL", hidden=False, today=TODAY)["hidden"] is False
    with pytest.raises(InvalidInputError) as exc:
        edits.update_category(db, "plaid:TRAVEL", name="Trips", today=TODAY)
    assert exc.value.code == "not_custom"
    with pytest.raises(NotFoundError):
        edits.update_category(db, "custom:99", hidden=True, today=TODAY)


# ---- accounts ------------------------------------------------------------------------------


def test_patch_account_updates_and_rebuilds(db: Connection, world: World) -> None:
    world.tx("t", world.chk, "2026-10-01", 20, "FOOD_AND_DRINK")
    rebuild_classification(db, TODAY)
    v0 = version(db)
    out = edits.patch_account(db, world.chk, TODAY, ownership="joint", reviewed=True)
    assert out["ownership"] == "joint" and out["reviewed"] is True and out["included"] is True
    assert out["institution_name"] == "Bank One" and out["removed"] is False
    assert version(db) > v0
    assert scoped(db, "t", "mine") is None
    out = edits.patch_account(db, world.chk, TODAY, included=False)
    assert out["included"] is False
    assert db.execute("SELECT COUNT(*) FROM tx_classification").fetchone()[0] == 0


def test_patch_account_errors(db: Connection, world: World) -> None:
    with pytest.raises(InvalidInputError) as exc:
        edits.patch_account(db, world.chk, TODAY, ownership="shared")
    assert exc.value.code == "invalid_ownership"
    with pytest.raises(NotFoundError):
        edits.patch_account(db, "missing", TODAY, reviewed=True)
