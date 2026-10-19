import pytest

from bizviz.analytics.categories import default_category
from bizviz.analytics.merchant import merchant_key, raw_display


@pytest.mark.parametrize(
    ("merchant_name", "name", "expected"),
    [
        ("  Starbucks   Coffee ", "STARBUCKS #123", "starbucks coffee"),
        (None, "SQ *Blue Bottle 1234", "blue bottle"),
        (None, "TST* Taco Place #55", "taco place"),
        (None, "TST*Taco Place", "taco place"),
        (None, "PP*Etsy Seller", "etsy seller"),
        (None, "PAYPAL *SPOTIFY", "spotify"),
        (None, "Shell Oil 57444", "shell oil"),
        ("", "AMAZON.COM*2K4  AMZN", "amazon.comk amzn"),
        (None, "12345 ###", "unknown"),
        (None, None, "unknown"),
        ("   ", "Netflix", "netflix"),
    ],
)
def test_merchant_key(merchant_name: str | None, name: str | None, expected: str) -> None:
    assert merchant_key(merchant_name, name) == expected


def test_raw_display_prefers_merchant_name() -> None:
    assert raw_display("Netflix", "NETFLIX.COM 123") == "Netflix"
    assert raw_display(None, "NETFLIX.COM 123") == "NETFLIX.COM 123"
    assert raw_display("", "") is None


APPLIES = {
    "plaid:FOOD_AND_DRINK": "spending",
    "plaid:INCOME_SALARY": "earning",
    "plaid:INCOME_OTHER": "earning",
    "plaid:OTHER": "spending",
}


@pytest.mark.parametrize(
    ("primary", "detailed", "expected"),
    [
        ("INCOME", "INCOME_SALARY", "plaid:INCOME_SALARY"),
        ("INCOME", "INCOME_WAGES_NEW", "plaid:INCOME_OTHER"),
        ("INCOME", None, "plaid:INCOME_OTHER"),
        (None, "INCOME_SALARY", "plaid:INCOME_SALARY"),
        ("FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE", "plaid:FOOD_AND_DRINK"),
        ("SOMETHING_NEW", None, "plaid:OTHER"),
        ("TRANSFER_OUT", None, "plaid:OTHER"),
        (None, None, "sys:uncategorized"),
    ],
)
def test_default_category(primary: str | None, detailed: str | None, expected: str) -> None:
    assert default_category(primary, detailed, APPLIES) == expected
