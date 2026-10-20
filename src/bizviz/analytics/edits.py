import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from bizviz.analytics.classify import rebuild_classification
from bizviz.analytics.dataset import category_out, fetch_account, rule_out
from bizviz.analytics.merchant import merchant_key, normalize_key
from bizviz.api.schema import AccountOut, CategoryOut, RuleOut
from bizviz.db.connection import Connection, bump_data_version, now_iso, write_unit
from bizviz.errors import InvalidInputError, NotFoundError

FLOWS = ("earning", "spending", "internal", "ignored")
FIELDS = ("category_id", "flow_class", "recurring")
_CUSTOM_RE = re.compile(r"custom:(\d+)")


class DuplicateRuleError(Exception):
    """A merchant rule already exists for the merchant key. The API maps it to 409."""


def _validate_fields(conn: Connection, changes: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the known keys of `changes` and validate their values (None is always valid)."""
    fields = {k: changes[k] for k in FIELDS if k in changes}
    category = fields.get("category_id")
    if category is not None:
        row = conn.execute("SELECT 1 FROM categories WHERE category_id = ?", (category,)).fetchone()
        if row is None:
            raise InvalidInputError("unknown_category")
    flow = fields.get("flow_class")
    if flow is not None and flow not in FLOWS:
        raise InvalidInputError("invalid_flow")
    recurring = fields.get("recurring")
    if recurring is not None:
        if not isinstance(recurring, bool):
            raise InvalidInputError("invalid_recurring")
        fields["recurring"] = int(recurring)
    return fields


def _transaction(conn: Connection, transaction_id: str) -> Any:
    row = conn.execute(
        "SELECT transaction_id, name, merchant_name, pending FROM transactions"
        " WHERE transaction_id = ?",
        (transaction_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"transaction {transaction_id} not found")
    return row


def _write_override(conn: Connection, transaction_id: str, values: Mapping[str, Any]) -> None:
    if all(values[f] is None for f in FIELDS):
        conn.execute("DELETE FROM tx_overrides WHERE transaction_id = ?", (transaction_id,))
        return
    conn.execute(
        "INSERT INTO tx_overrides(transaction_id, category_id, flow_class, recurring, updated_at)"
        " VALUES (?, ?, ?, ?, ?) ON CONFLICT(transaction_id) DO UPDATE SET"
        " category_id = excluded.category_id, flow_class = excluded.flow_class,"
        " recurring = excluded.recurring, updated_at = excluded.updated_at",
        (
            transaction_id,
            values["category_id"],
            values["flow_class"],
            values["recurring"],
            now_iso(),
        ),
    )


def _current_override(conn: Connection, transaction_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT category_id, flow_class, recurring FROM tx_overrides WHERE transaction_id = ?",
        (transaction_id,),
    ).fetchone()
    return {f: None if row is None else row[f] for f in FIELDS}


def set_override(
    conn: Connection, transaction_id: str, changes: Mapping[str, Any], today: date
) -> int:
    _transaction(conn, transaction_id)
    fields = _validate_fields(conn, changes)
    with write_unit(conn):
        values = _current_override(conn, transaction_id)
        values.update(fields)
        _write_override(conn, transaction_id, values)
    return rebuild_classification(conn, today)


def _upsert_rule(conn: Connection, key: str, fields: Mapping[str, Any]) -> int:
    row = conn.execute(
        "SELECT rule_id, category_id, flow_class, recurring FROM merchant_rules"
        " WHERE merchant_key = ?",
        (key,),
    ).fetchone()
    now = now_iso()
    if row is None:
        values = {f: fields.get(f) for f in FIELDS}
        cur = conn.execute(
            "INSERT INTO merchant_rules(merchant_key, category_id, flow_class, recurring,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (key, values["category_id"], values["flow_class"], values["recurring"], now, now),
        )
        return int(cur.lastrowid)
    values = {f: row[f] for f in FIELDS}
    values.update(fields)
    conn.execute(
        "UPDATE merchant_rules SET category_id = ?, flow_class = ?, recurring = ?,"
        " updated_at = ? WHERE rule_id = ?",
        (values["category_id"], values["flow_class"], values["recurring"], now, row["rule_id"]),
    )
    return int(row["rule_id"])


def apply_to_merchant(
    conn: Connection, transaction_id: str, changes: Mapping[str, Any], today: date
) -> tuple[int, int]:
    tx = _transaction(conn, transaction_id)
    if tx["pending"]:
        raise InvalidInputError("pending")
    fields = _validate_fields(conn, changes)
    key = merchant_key(tx["merchant_name"], tx["name"])
    with write_unit(conn):
        rule_id = _upsert_rule(conn, key, fields)
        values = _current_override(conn, transaction_id)
        values.update({f: None for f in fields})
        _write_override(conn, transaction_id, values)
    return rule_id, rebuild_classification(conn, today)


def list_rules(conn: Connection) -> list[RuleOut]:
    return [
        rule_out(r)
        for r in conn.execute(
            "SELECT rule_id, merchant_key, category_id, flow_class, recurring FROM merchant_rules"
            " ORDER BY rule_id"
        )
    ]


def _get_rule(conn: Connection, rule_id: int) -> RuleOut:
    row = conn.execute(
        "SELECT rule_id, merchant_key, category_id, flow_class, recurring FROM merchant_rules"
        " WHERE rule_id = ?",
        (rule_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"rule {rule_id} not found")
    return rule_out(row)


def create_rule(
    conn: Connection, merchant_key: str, fields: Mapping[str, Any], today: date
) -> RuleOut:
    key = normalize_key(merchant_key)
    if not key:
        raise InvalidInputError("invalid_merchant_key")
    values = _validate_fields(conn, fields)
    with write_unit(conn):
        if conn.execute("SELECT 1 FROM merchant_rules WHERE merchant_key = ?", (key,)).fetchone():
            raise DuplicateRuleError(key)
        rule_id = _upsert_rule(conn, key, values)
    rebuild_classification(conn, today)
    return _get_rule(conn, rule_id)


def update_rule(conn: Connection, rule_id: int, fields: Mapping[str, Any], today: date) -> RuleOut:
    values = _validate_fields(conn, fields)
    with write_unit(conn):
        key = _get_rule(conn, rule_id)["merchant_key"]
        _upsert_rule(conn, key, values)
    rebuild_classification(conn, today)
    return _get_rule(conn, rule_id)


def delete_rule(conn: Connection, rule_id: int, today: date) -> None:
    with write_unit(conn):
        _get_rule(conn, rule_id)
        conn.execute("DELETE FROM merchant_rules WHERE rule_id = ?", (rule_id,))
    rebuild_classification(conn, today)


def _get_category(conn: Connection, category_id: str) -> CategoryOut:
    row = conn.execute(
        "SELECT category_id, name, kind, applies_to, hidden FROM categories WHERE category_id = ?",
        (category_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"category {category_id} not found")
    return category_out(row)


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise InvalidInputError("invalid_name")
    return cleaned


def create_category(conn: Connection, name: str, applies_to: str, today: date) -> CategoryOut:
    if applies_to not in ("spending", "earning", "both"):
        raise InvalidInputError("invalid_applies_to")
    cleaned = _clean_name(name)
    with write_unit(conn):
        numbers = [
            int(m.group(1))
            for (cid,) in conn.execute("SELECT category_id FROM categories WHERE kind = 'custom'")
            if (m := _CUSTOM_RE.fullmatch(cid))
        ]
        category_id = f"custom:{max(numbers, default=0) + 1}"
        conn.execute(
            "INSERT INTO categories(category_id, name, kind, applies_to)"
            " VALUES (?, ?, 'custom', ?)",
            (category_id, cleaned, applies_to),
        )
        bump_data_version(conn)
    return _get_category(conn, category_id)


def update_category(
    conn: Connection,
    category_id: str,
    name: str | None = None,
    hidden: bool | None = None,
    today: date | None = None,
) -> CategoryOut:
    with write_unit(conn):
        current = _get_category(conn, category_id)
        if name is not None:
            if current["kind"] != "custom":
                raise InvalidInputError("not_custom")
            conn.execute(
                "UPDATE categories SET name = ? WHERE category_id = ?",
                (_clean_name(name), category_id),
            )
        if hidden is not None:
            conn.execute(
                "UPDATE categories SET hidden = ? WHERE category_id = ?",
                (int(bool(hidden)), category_id),
            )
        bump_data_version(conn)
    return _get_category(conn, category_id)


def patch_account(
    conn: Connection,
    account_id: str,
    today: date,
    ownership: str | None = None,
    included: bool | None = None,
    reviewed: bool | None = None,
) -> AccountOut:
    if ownership is not None and ownership not in ("mine", "joint"):
        raise InvalidInputError("invalid_ownership")
    if fetch_account(conn, account_id) is None:
        raise NotFoundError(f"account {account_id} not found")
    updates = {
        "ownership": ownership,
        "included": None if included is None else int(included),
        "reviewed": None if reviewed is None else int(reviewed),
    }
    changed = {k: v for k, v in updates.items() if v is not None}
    with write_unit(conn):
        if changed:
            sets = ", ".join(f"{k} = ?" for k in changed)
            cur = conn.execute(
                f"UPDATE accounts SET {sets} WHERE account_id = ?", (*changed.values(), account_id)
            )
            if cur.rowcount == 0:
                raise NotFoundError(f"account {account_id} not found")
    rebuild_classification(conn, today)
    account = fetch_account(conn, account_id)
    assert account is not None
    return account
