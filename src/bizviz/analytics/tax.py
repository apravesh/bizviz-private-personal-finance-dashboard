"""Tax-advantaged accounts, from the Plaid account subtype."""

PRE_TAX = "pre-tax"
ROTH = "roth"
HEALTH = "health"
EDUCATION = "education"
OTHER = "other"

# Plaid account subtypes (lowercased) with a tax advantage. Plans whose treatment the subtype
# does not say (a generic "retirement" account, pensions) are "other".
SUBTYPE_GROUPS: dict[str, str] = {
    "401a": PRE_TAX,
    "401k": PRE_TAX,
    "403b": PRE_TAX,
    "457b": PRE_TAX,
    "ira": PRE_TAX,
    "sep ira": PRE_TAX,
    "sarsep": PRE_TAX,
    "simple ira": PRE_TAX,
    "keogh": PRE_TAX,
    "thrift savings plan": PRE_TAX,
    "rrsp": PRE_TAX,
    "rrif": PRE_TAX,
    "lira": PRE_TAX,
    "lrsp": PRE_TAX,
    "lif": PRE_TAX,
    "lrif": PRE_TAX,
    "rlif": PRE_TAX,
    "prif": PRE_TAX,
    "sipp": PRE_TAX,
    "roth": ROTH,
    "roth 401k": ROTH,
    "tfsa": ROTH,
    "isa": ROTH,
    "cash isa": ROTH,
    "hsa": HEALTH,
    "529": EDUCATION,
    "education savings account": EDUCATION,
    "resp": EDUCATION,
    "pension": OTHER,
    "retirement": OTHER,
    "profit sharing plan": OTHER,
    "rdsp": OTHER,
}


def tax_group(subtype: str | None) -> str | None:
    """The tax-advantaged group of an account subtype, or None for taxable accounts."""
    return SUBTYPE_GROUPS.get((subtype or "").strip().lower())
