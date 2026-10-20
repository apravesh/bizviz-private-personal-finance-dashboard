from bizviz.db.connection import Connection

INCOME_OTHER = "plaid:INCOME_OTHER"
SPENDING_OTHER = "plaid:OTHER"
TRANSFER = "sys:transfer"
UNCATEGORIZED = "sys:uncategorized"


def applies_to_map(conn: Connection) -> dict[str, str]:
    rows = conn.execute("SELECT category_id, applies_to FROM categories").fetchall()
    return {r[0]: r[1] for r in rows}


def default_category(
    pfc_primary: str | None, pfc_detailed: str | None, applies_to: dict[str, str]
) -> str:
    """Default category from Plaid's PFC; `applies_to` is the categories table (applies_to_map)."""
    if pfc_detailed and pfc_detailed.startswith("INCOME_"):
        candidate = f"plaid:{pfc_detailed}"
        return candidate if applies_to.get(candidate) == "earning" else INCOME_OTHER
    if pfc_primary == "INCOME":
        return INCOME_OTHER
    if pfc_primary:
        candidate = f"plaid:{pfc_primary}"
        return candidate if applies_to.get(candidate) == "spending" else SPENDING_OTHER
    return UNCATEGORIZED
