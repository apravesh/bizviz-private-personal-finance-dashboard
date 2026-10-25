import pytest

from bizviz.analytics.tax import tax_group


@pytest.mark.parametrize(
    ("subtype", "group"),
    [
        ("401k", "pre-tax"),
        ("403B", "pre-tax"),
        (" IRA ", "pre-tax"),
        ("sep ira", "pre-tax"),
        ("roth", "roth"),
        ("Roth 401k", "roth"),
        ("hsa", "health"),
        ("529", "education"),
        ("pension", "other"),
        ("brokerage", None),
        ("crypto exchange", None),
        ("stock plan", None),
        ("checking", None),
        ("", None),
        (None, None),
    ],
)
def test_tax_group(subtype: str | None, group: str | None) -> None:
    assert tax_group(subtype) == group
