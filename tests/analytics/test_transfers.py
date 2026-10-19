from bizviz.analytics.transfers import CC_PAYMENT, Side, match_transfers


def side(
    id: str,
    account: str,
    day: str,
    amount: float,
    primary: str | None = None,
    detailed: str | None = None,
    type: str = "depository",
) -> Side:
    return Side(id, account, type, day, amount, primary, detailed)


def test_basic_pair_both_directions() -> None:
    out = match_transfers(
        [
            side("o", "a", "2026-01-01", 100, "TRANSFER_OUT"),
            side("i", "b", "2026-01-03", -100, "TRANSFER_IN"),
        ]
    )
    assert out == {"o": ("i", "cash"), "i": ("o", "cash")}


def test_never_pairs_income_side() -> None:
    out = match_transfers(
        [
            side("o", "a", "2026-01-01", 100, "TRANSFER_OUT"),
            side("i", "b", "2026-01-01", -100, "INCOME", "INCOME_SALARY"),
        ]
    )
    assert out == {}
    out = match_transfers(
        [
            side("o", "a", "2026-01-01", 100, "INCOME"),
            side("i", "b", "2026-01-01", -100, "TRANSFER_IN"),
        ]
    )
    assert out == {}


def test_requires_transfer_typed_side_amount_accounts_and_window() -> None:
    base = [side("o", "a", "2026-01-01", 100, "FOOD_AND_DRINK")]
    assert match_transfers([*base, side("i", "b", "2026-01-01", -100, "GENERAL_MERCHANDISE")]) == {}
    assert match_transfers([*base, side("i", "b", "2026-01-01", -100, "TRANSFER_IN")]) != {}
    xfer = [side("o", "a", "2026-01-01", 100, "TRANSFER_OUT")]
    assert match_transfers([*xfer, side("i", "a", "2026-01-01", -100, "TRANSFER_IN")]) == {}
    assert match_transfers([*xfer, side("i", "b", "2026-01-01", -100.01, "TRANSFER_IN")]) == {}
    assert match_transfers([*xfer, side("i", "b", "2026-01-06", -100, "TRANSFER_IN")]) != {}
    assert match_transfers([*xfer, side("i", "b", "2026-01-07", -100, "TRANSFER_IN")]) == {}


def test_card_payment_pairs_only_with_credit_account() -> None:
    o = side("o", "chk", "2026-01-01", 250, "LOAN_PAYMENTS", CC_PAYMENT)
    on_savings = side("i1", "sav", "2026-01-01", -250, "TRANSFER_IN")
    on_card = side(
        "i2",
        "cc",
        "2026-01-04",
        -250,
        "LOAN_PAYMENTS",
        "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
        "credit",
    )
    assert match_transfers([o, on_savings]) == {}
    assert match_transfers([o, on_savings, on_card]) == {"o": ("i2", "cash"), "i2": ("o", "cash")}


def test_other_loan_payment_outflows_never_pair() -> None:
    o = side("o", "chk", "2026-01-01", 900, "LOAN_PAYMENTS", "LOAN_PAYMENTS_MORTGAGE_PAYMENT")
    i = side("i", "sav", "2026-01-01", -900, "TRANSFER_IN")
    assert match_transfers([o, i]) == {}


def test_refund_does_not_steal_transfer_pair() -> None:
    out_tx = side("o", "chk", "2026-01-10", 100, "TRANSFER_OUT")
    refund = side("refund", "cc", "2026-01-10", -100, "GENERAL_MERCHANDISE", type="credit")
    transfer_in = side("i", "sav", "2026-01-13", -100, "TRANSFER_IN")
    out = match_transfers([out_tx, refund, transfer_in])
    assert out["o"] == ("i", "cash")
    assert "refund" not in out


def test_ties_broken_deterministically() -> None:
    sides = [
        side("o1", "a", "2026-01-01", 50, "TRANSFER_OUT"),
        side("o2", "a", "2026-01-01", 50, "TRANSFER_OUT"),
        side("i2", "b", "2026-01-01", -50, "TRANSFER_IN"),
        side("i1", "b", "2026-01-01", -50, "TRANSFER_IN"),
    ]
    expected = {
        "o1": ("i1", "cash"),
        "i1": ("o1", "cash"),
        "o2": ("i2", "cash"),
        "i2": ("o2", "cash"),
    }
    assert match_transfers(sides) == expected
    assert match_transfers(list(reversed(sides))) == expected


def test_closer_date_preferred_among_equal_priority() -> None:
    sides = [
        side("o", "a", "2026-01-05", 75, "TRANSFER_OUT"),
        side("far", "b", "2026-01-01", -75, "TRANSFER_IN"),
        side("near", "c", "2026-01-04", -75, "TRANSFER_IN"),
    ]
    assert match_transfers(sides)["o"] == ("near", "cash")


def test_each_side_in_at_most_one_pair() -> None:
    sides = [
        side("o1", "a", "2026-01-01", 20, "TRANSFER_OUT"),
        side("o2", "a", "2026-01-02", 20, "TRANSFER_OUT"),
        side("i", "b", "2026-01-01", -20, "TRANSFER_IN"),
    ]
    out = match_transfers(sides)
    assert len(out) == 2


def test_investment_to_investment_never_pairs() -> None:
    out = match_transfers(
        [
            Side("inv:1", "brk1", "investment", "2026-01-01", 10, None, None),
            Side("inv:2", "brk2", "investment", "2026-01-01", -10, None, None),
        ]
    )
    assert out == {}
