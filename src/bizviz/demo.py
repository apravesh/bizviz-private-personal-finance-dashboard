"""Synthetic demo dataset: a fake household with 30 months of history.

Everything here is invented and deterministic for a given `today`. Demo mode never calls Plaid
or Yahoo and never touches the Keychain.
"""

import math
import os
import random
import secrets
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from bizviz import certs
from bizviz.analytics.classify import rebuild_classification
from bizviz.analytics.investment_history import rebuild_estimates
from bizviz.auth import set_dashboard_password
from bizviz.config import PORT_DEMO, load_config, replace_config
from bizviz.context import AppContext
from bizviz.db.connection import Connection, set_setting, write_unit
from bizviz.db.migrate import migrate
from bizviz.logging_setup import configure_logging
from bizviz.platform.base import Platform
from bizviz.platform.fake import (
    FakeSecretStore,
    FakeServiceManager,
    FakeSystemStatus,
    FakeTrustStore,
)
from bizviz.prices import refresh_prices

if TYPE_CHECKING:
    from bizviz.service import Runtime

DEMO_PASSWORD = "demo-password"
HISTORY_MONTHS = 30
SNAPSHOT_DAYS = 60
SPLIT_RATIO = 2.0
_PRICE_EPOCH_DAYS = 1300
_SPLIT_AVOID_DAYS = frozenset({3, 5, 6, 7, 27, 28, 29})

ITEM_PRODUCTS = '["transactions","investments"]'

CHECKING = "demo-checking"
JOINT = "demo-joint"
SAVINGS = "demo-savings"
CARD_1 = "demo-card-1"
CARD_2 = "demo-card-2"
MORTGAGE = "demo-mortgage"
BROKERAGE = "demo-brokerage"
RETIRE_401K = "demo-401k"
RETIRE_ROTH = "demo-roth-ira"

SEC_VTI = "demo-sec-vti"
SEC_VTSAX = "demo-sec-vtsax"
SEC_SWEEP = "demo-sec-sweep"
SEC_PRIVATE = "demo-sec-private"


def _add_months(d: date, months: int) -> date:
    index = d.year * 12 + (d.month - 1) + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(d.day, 28))


def _weekday_on_or_after(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def deposit_day(month: date) -> date:
    return _weekday_on_or_after(date(month.year, month.month, 3))


def split_date_for(today: date) -> date:
    candidate = _add_months(today, -8)
    while candidate.day in _SPLIT_AVOID_DAYS or candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


class SyntheticPriceSource:
    """Deterministic random-walk closes. The adjusted series is continuous across the split."""

    _PARAMS = {"VTI": (130.0, 0.00045, 0.011, 7), "VTSAX": (95.0, 0.00040, 0.0105, 11)}

    def __init__(self, today: date) -> None:
        self.today = today
        self.split_date = split_date_for(today)
        self._series: dict[str, dict[date, float]] = {}

    def _walk(self, symbol: str) -> dict[date, float]:
        cached = self._series.get(symbol)
        if cached is not None:
            return cached
        start_price, drift, vol, seed = self._PARAMS[symbol]
        rng = random.Random(seed)
        price = start_price
        out: dict[date, float] = {}
        day = self.today - timedelta(days=_PRICE_EPOCH_DAYS)
        while day <= self.today:
            if day.weekday() < 5:
                price *= math.exp(drift + vol * rng.gauss(0.0, 1.0))
                out[day] = round(price, 4)
            day += timedelta(days=1)
        self._series[symbol] = out
        return out

    def adjusted_close(self, symbol: str, day: date) -> float | None:
        series = self._walk(symbol)
        for back in range(8):
            value = series.get(day - timedelta(days=back))
            if value is not None:
                return value
        return None

    def raw_close(self, symbol: str, day: date) -> float:
        """What the market actually printed on `day` (before the split it is twice the adjusted)."""
        value = self.adjusted_close(symbol, day)
        assert value is not None
        if symbol == "VTI" and day < self.split_date:
            value *= SPLIT_RATIO
        return round(value, 4)

    def download_closes(
        self, symbols: list[str], start: date, end_exclusive: date
    ) -> dict[str, list[tuple[date, float]]]:
        out: dict[str, list[tuple[date, float]]] = {}
        for symbol in symbols:
            if symbol not in self._PARAMS:
                out[symbol] = []
                continue
            series = self._walk(symbol)
            out[symbol] = [(d, v) for d, v in sorted(series.items()) if start <= d < end_exclusive]
        return out

    def splits(self, symbol: str) -> list[tuple[date, float]]:
        return [(self.split_date, SPLIT_RATIO)] if symbol == "VTI" else []


@dataclass
class _Txn:
    id: str
    account_id: str
    date: date
    amount: float
    name: str
    merchant_name: str | None
    pfc_primary: str | None
    pfc_detailed: str | None
    pending: bool = False


class _Ledger:
    def __init__(self) -> None:
        self.txns: list[_Txn] = []
        self._next = 1

    def add(
        self,
        account_id: str,
        day: date,
        amount: float,
        name: str,
        primary: str | None,
        detailed: str | None,
        merchant: str | None = None,
        pending: bool = False,
    ) -> None:
        self.txns.append(
            _Txn(
                f"demo-tx-{self._next:05d}",
                account_id,
                day,
                round(amount, 2),
                name,
                merchant,
                primary,
                detailed,
                pending,
            )
        )
        self._next += 1

    def transfer(
        self,
        out_account: str,
        in_account: str,
        day: date,
        amount: float,
        out_pfc: tuple[str, str],
        in_pfc: tuple[str, str],
        out_name: str,
        in_name: str,
        in_day: date | None = None,
    ) -> None:
        self.add(out_account, day, amount, out_name, *out_pfc)
        self.add(in_account, in_day or day, -amount, in_name, *in_pfc)


def _months(start: date, today: date) -> Iterable[date]:
    month = date(start.year, start.month, 1)
    while month <= today:
        yield month
        month = _add_months(month, 1)


def _spending(ledger: _Ledger, rng: random.Random, start: date, today: date) -> None:
    def on(month: date, dom: int) -> date:
        return date(month.year, month.month, dom)

    def ok(d: date) -> bool:
        return start <= d <= today

    first_friday = start + timedelta(days=(4 - start.weekday()) % 7)
    pay = first_friday
    raise_day = _add_months(today, -14)
    while pay <= today:
        amount = 3150.00 if pay < raise_day else 3300.00
        ledger.add(CHECKING, pay, -amount, "ACME CORP PAYROLL", "INCOME", "INCOME_SALARY")
        pay += timedelta(days=14)
    partner = first_friday + timedelta(days=7)
    while partner <= today:
        ledger.add(JOINT, partner, -900.00, "NORTHWIND LLC DIRECT DEP", "INCOME", "INCOME_SALARY")
        partner += timedelta(days=14)

    for month in _months(start, today):
        mortgage_day = on(month, 1)
        if ok(mortgage_day):
            ledger.add(
                JOINT,
                mortgage_day,
                2150.00,
                "DEMO MORTGAGE SERVICER PMT",
                "LOAN_PAYMENTS",
                "LOAN_PAYMENTS_MORTGAGE_PAYMENT",
                "Demo Mortgage Servicer",
            )
        two = on(month, 2)
        if ok(two):
            ledger.transfer(
                CHECKING,
                SAVINGS,
                two,
                1200.00,
                ("TRANSFER_OUT", "TRANSFER_OUT_SAVINGS"),
                ("TRANSFER_IN", "TRANSFER_IN_SAVINGS"),
                "Online transfer to savings",
                "Online transfer from checking",
            )
            ledger.transfer(
                CHECKING,
                JOINT,
                two,
                1500.00,
                ("TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER"),
                ("TRANSFER_IN", "TRANSFER_IN_ACCOUNT_TRANSFER"),
                "Transfer to Joint Checking",
                "Transfer from Everyday Checking",
                in_day=two + timedelta(days=1) if two + timedelta(days=1) <= today else two,
            )
        if ok(two) and start <= deposit_day(month) < today:
            ledger.add(
                CHECKING,
                two,
                1750.00,
                "Transfer to Demo Brokerage",
                "TRANSFER_OUT",
                "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS",
            )
        for dom, account, amount, name, merchant, primary, detailed, raise_after in (
            (
                14,
                CARD_1,
                15.49,
                "NETFLIX.COM",
                "Netflix",
                "ENTERTAINMENT",
                "ENTERTAINMENT_TV_AND_MOVIES",
                17.99,
            ),
            (
                17,
                CARD_1,
                11.99,
                "SPOTIFY USA",
                "Spotify",
                "ENTERTAINMENT",
                "ENTERTAINMENT_MUSIC_AND_AUDIO",
                None,
            ),
            (
                3,
                CARD_2,
                45.00,
                "IRON WORKS GYM",
                "Iron Works Gym",
                "PERSONAL_CARE",
                "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS",
                None,
            ),
            (
                21,
                CARD_2,
                2.99,
                "APPLE.COM/BILL",
                "Apple",
                "GENERAL_SERVICES",
                "GENERAL_SERVICES_OTHER_GENERAL_SERVICES",
                None,
            ),
            (
                12,
                JOINT,
                69.99,
                "FIBERNET INTERNET",
                "FiberNet",
                "RENT_AND_UTILITIES",
                "RENT_AND_UTILITIES_INTERNET_AND_CABLE",
                None,
            ),
        ):
            d = on(month, dom)
            if ok(d):
                price = raise_after if raise_after and d > _add_months(today, -12) else amount
                ledger.add(account, d, price, name, primary, detailed, merchant)
        d = on(month, 8)
        if ok(d):
            ledger.add(
                JOINT,
                d,
                round(rng.uniform(95, 125), 2),
                "CITY POWER & LIGHT",
                "RENT_AND_UTILITIES",
                "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY",
                "City Power & Light",
            )
        d = on(month, 4)
        if ok(d):
            ledger.add(
                SAVINGS,
                d,
                -round(rng.uniform(8, 25), 2),
                "INTEREST PAYMENT",
                "INCOME",
                "INCOME_INTEREST_EARNED",
            )
        d = on(month, 15)
        if ok(d):
            ledger.add(
                JOINT,
                d,
                round(rng.uniform(240, 380), 2),
                "COSTCO WHSE #123",
                "GENERAL_MERCHANDISE",
                "GENERAL_MERCHANDISE_SUPERSTORES",
                "Costco",
            )
        if month.month in (4, 10):
            d = on(month, 10)
            if ok(d):
                ledger.add(
                    JOINT,
                    d,
                    1850.00,
                    "COUNTY TAX COLLECTOR",
                    "GOVERNMENT_AND_NON_PROFIT",
                    "GOVERNMENT_AND_NON_PROFIT_TAX_PAYMENT",
                    "County Tax Collector",
                )
        if month.month in (3, 9):
            d = on(month, 18)
            if ok(d):
                ledger.add(
                    CARD_2,
                    d,
                    round(rng.uniform(480, 720), 2),
                    "DEMO AIRLINES",
                    "TRAVEL",
                    "TRAVEL_FLIGHTS",
                    "Demo Airlines",
                )
        if rng.random() < 0.35:
            d = on(month, rng.randint(1, 28))
            if ok(d):
                ledger.add(
                    CHECKING,
                    d,
                    round(rng.uniform(40, 150), 2),
                    "METRO CLINIC",
                    "MEDICAL",
                    "MEDICAL_PRIMARY_CARE",
                    "Metro Clinic",
                )
        if rng.random() < 0.4:
            d = on(month, rng.randint(1, 28))
            if ok(d):
                ledger.add(
                    JOINT,
                    d,
                    round(rng.uniform(30, 260), 2),
                    "THE HOME DEPOT",
                    "HOME_IMPROVEMENT",
                    "HOME_IMPROVEMENT_HARDWARE",
                    "The Home Depot",
                )
        for _ in range(rng.randint(1, 2)):
            d = on(month, rng.randint(1, 28))
            if ok(d):
                ledger.add(
                    CARD_1,
                    d,
                    round(rng.uniform(20, 180), 2),
                    "AMAZON MKTPLACE PMTS",
                    "GENERAL_MERCHANDISE",
                    "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES",
                    "Amazon",
                )
        if rng.random() < 0.15:
            d = on(month, rng.randint(1, 28))
            if ok(d):
                ledger.add(
                    CARD_1,
                    d,
                    -round(rng.uniform(15, 90), 2),
                    "AMAZON REFUND",
                    "GENERAL_MERCHANDISE",
                    "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES",
                    "Amazon",
                )

    day = start
    while day <= today:
        if day.weekday() in (1, 5) and rng.random() < 0.9:
            shop = rng.choice(("Whole Foods", "Trader Joe's"))
            ledger.add(
                CARD_1,
                day,
                round(rng.uniform(40, 140), 2),
                shop.upper(),
                "FOOD_AND_DRINK",
                "FOOD_AND_DRINK_GROCERIES",
                shop,
            )
        if day.weekday() in (2, 4, 6) and rng.random() < 0.55:
            place = rng.choice(("Chipotle", "Local Bistro", "Thai Garden"))
            ledger.add(
                CARD_2,
                day,
                round(rng.uniform(12, 65), 2),
                place.upper(),
                "FOOD_AND_DRINK",
                "FOOD_AND_DRINK_RESTAURANT",
                place,
            )
        if day.weekday() < 5 and rng.random() < 0.5:
            ledger.add(
                CHECKING,
                day,
                round(rng.uniform(4, 7), 2),
                "BLUE BOTTLE COFFEE",
                "FOOD_AND_DRINK",
                "FOOD_AND_DRINK_COFFEE",
                "Blue Bottle Coffee",
            )
        if day.day % 10 == 0 and rng.random() < 0.8:
            ledger.add(
                CARD_2,
                day,
                round(rng.uniform(35, 55), 2),
                "SHELL OIL 5744",
                "TRANSPORTATION",
                "TRANSPORTATION_GAS",
                "Shell",
            )
        day += timedelta(days=1)

    for pending_day, account, amount, name in (
        (today, CARD_2, 23.40, "CHIPOTLE"),
        (today - timedelta(days=1), CARD_1, 64.12, "WHOLE FOODS"),
    ):
        ledger.add(account, pending_day, amount, name, "FOOD_AND_DRINK", None, name.title(), True)


def _card_payments(ledger: _Ledger, today: date) -> None:
    charged: dict[tuple[str, int, int], float] = defaultdict(float)
    for t in ledger.txns:
        if t.account_id in (CARD_1, CARD_2) and not t.pending:
            charged[(t.account_id, t.date.year, t.date.month)] += t.amount
    for (account, year, month), total in sorted(charged.items()):
        pay_day = _add_months(date(year, month, 1), 1).replace(day=20)
        if pay_day + timedelta(days=1) > today or total <= 0:
            continue
        ledger.transfer(
            CHECKING,
            account,
            pay_day,
            round(total, 2),
            ("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
            ("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
            "AUTOPAY CREDIT CARD PAYMENT",
            "PAYMENT THANK YOU",
            in_day=pay_day + timedelta(days=1),
        )


@dataclass
class _InvTxn:
    id: str
    day: date
    security_id: str | None
    name: str
    type: str
    subtype: str | None
    quantity: float | None
    amount: float
    price: float | None


@dataclass
class _Brokerage:
    txns: list[_InvTxn]
    qty: dict[str, float]
    sweep_value: float
    balance_by_day: dict[date, float]


def _retirement(
    account_id: str,
    start: date,
    today: date,
    src: SyntheticPriceSource,
    opening_shares: float,
    monthly: float,
    payday: int,
) -> _Brokerage:
    """A retirement account that buys one index fund with each month's contribution."""
    qty = opening_shares
    txns: list[_InvTxn] = []
    qty_after: dict[date, float] = {}
    for n, month in enumerate(_months(start, today - timedelta(days=1)), start=1):
        d = _weekday_on_or_after(date(month.year, month.month, payday))
        if not start <= d < today:
            continue
        price = src.raw_close("VTSAX", d)
        shares = round(monthly / price, 4)
        amount = round(shares * price, 2)
        txns.append(
            _InvTxn(
                f"{account_id}-c{n:03d}",
                d,
                None,
                "Contribution",
                "cash",
                "contribution",
                None,
                -amount,
                None,
            )
        )
        txns.append(
            _InvTxn(
                f"{account_id}-b{n:03d}",
                d,
                SEC_VTSAX,
                "Buy VTSAX",
                "buy",
                "buy",
                shares,
                amount,
                price,
            )
        )
        qty += shares
        qty_after[d] = qty
    balance_by_day: dict[date, float] = {}
    held = opening_shares
    day = start
    while day <= today:
        held = qty_after.get(day, held)
        if day >= today - timedelta(days=SNAPSHOT_DAYS - 1):
            balance_by_day[day] = round(held * src.raw_close("VTSAX", day), 2)
        day += timedelta(days=1)
    return _Brokerage(txns, {SEC_VTSAX: qty}, 0.0, balance_by_day)


def _brokerage(start: date, today: date, src: SyntheticPriceSource) -> _Brokerage:
    split_day = src.split_date
    events: list[tuple[date, int, str]] = []
    for month in _months(start, today - timedelta(days=1)):
        for kind, dom in (("deposit", 3), ("buy", 5), ("dividend", 27)):
            if kind == "dividend" and month.month not in (3, 6, 9, 12):
                continue
            d = (
                deposit_day(month)
                if kind == "deposit"
                else _weekday_on_or_after(date(month.year, month.month, dom))
            )
            if start <= d < today:
                events.append((d, 0, kind))
    events.append((split_day, -1, "split"))
    events.sort()

    qty = {SEC_VTI: 60.0, SEC_VTSAX: 150.0}
    txns: list[_InvTxn] = []
    cash_change: dict[date, float] = defaultdict(float)
    qty_after: dict[date, dict[str, float]] = {}
    counter = 1

    def new_id() -> str:
        nonlocal counter
        counter += 1
        return f"demo-invtx-{counter:04d}"

    for d, _, kind in events:
        if kind == "split":
            added = qty[SEC_VTI] * (SPLIT_RATIO - 1)
            txns.append(
                _InvTxn(
                    new_id(), d, SEC_VTI, "VTI 2-for-1 split", "transfer", "split", added, 0.0, None
                )
            )
            qty[SEC_VTI] *= SPLIT_RATIO
        elif kind == "deposit":
            txns.append(
                _InvTxn(
                    new_id(),
                    d,
                    None,
                    "Deposit from checking",
                    "cash",
                    "deposit",
                    None,
                    -1750.0,
                    None,
                )
            )
            cash_change[d] += 1750.0
        elif kind == "buy":
            for sid, symbol, dollars in ((SEC_VTI, "VTI", 1050.0), (SEC_VTSAX, "VTSAX", 700.0)):
                price = src.raw_close(symbol, d)
                shares = round(dollars / price, 4)
                amount = round(shares * price, 2)
                txns.append(
                    _InvTxn(new_id(), d, sid, f"Buy {symbol}", "buy", "buy", shares, amount, price)
                )
                qty[sid] += shares
                cash_change[d] -= amount
        else:
            for sid, symbol, per_share in ((SEC_VTI, "VTI", 0.75), (SEC_VTSAX, "VTSAX", 1.05)):
                per = per_share * (SPLIT_RATIO if symbol == "VTI" and d < split_day else 1.0)
                amount = -round(qty[sid] * per, 2)
                txns.append(
                    _InvTxn(
                        new_id(),
                        d,
                        sid,
                        f"{symbol} dividend",
                        "cash",
                        "dividend",
                        None,
                        amount,
                        None,
                    )
                )
                cash_change[d] -= amount
        qty_after[d] = dict(qty)

    total_change = sum(cash_change.values())
    running = 0.0
    worst_suffix = 0.0
    for d in sorted(cash_change):
        running += cash_change[d]
        worst_suffix = max(worst_suffix, total_change - running)
    sweep_value = round(2000.0 + worst_suffix, 2)
    opening_cash = sweep_value - total_change

    balance_by_day: dict[date, float] = {}
    state = {SEC_VTI: 60.0, SEC_VTSAX: 150.0}
    cash = opening_cash
    day = start
    while day <= today:
        if day in qty_after:
            state = qty_after[day]
        cash += cash_change.get(day, 0.0)
        if day >= today - timedelta(days=SNAPSHOT_DAYS - 1):
            value = cash + 120 * 42.5
            value += state[SEC_VTI] * src.raw_close("VTI", day)
            value += state[SEC_VTSAX] * src.raw_close("VTSAX", day)
            balance_by_day[day] = round(value, 2)
        day += timedelta(days=1)
    return _Brokerage(txns, dict(qty), sweep_value, balance_by_day)


def _balances(
    txns: list[_Txn], account: str, start: date, today: date, *, debt: bool
) -> dict[date, float]:
    by_day: dict[date, float] = defaultdict(float)
    for t in txns:
        if t.account_id == account:
            by_day[t.date] += t.amount
    cumulative = 0.0
    series: dict[date, float] = {}
    day = start - timedelta(days=1)
    while day <= today:
        cumulative += by_day.get(day, 0.0) * (1 if debt else -1)
        series[day] = cumulative
        day += timedelta(days=1)
    offset = 0.0 if debt else max(500.0, 500.0 - min(series.values()))
    return {d: round(v + offset, 2) for d, v in series.items()}


def populate(conn: Connection, today: date) -> None:
    rng = random.Random(42)
    start = _add_months(date(today.year, today.month, 1), -HISTORY_MONTHS)
    src = SyntheticPriceSource(today)
    stamp = f"{today.isoformat()}T06:00:00Z"
    created = f"{start.isoformat()}T00:00:00Z"

    ledger = _Ledger()
    _spending(ledger, rng, start, today)
    _card_payments(ledger, today)
    ledger.txns.sort(key=lambda t: (t.date, t.id))
    brokerage = _brokerage(start, today, src)
    retirement = {
        RETIRE_401K: _retirement(RETIRE_401K, start, today, src, 520.0, 1600.0, 15),
        RETIRE_ROTH: _retirement(RETIRE_ROTH, start, today, src, 210.0, 580.0, 2),
    }

    snapshot_days = [today - timedelta(days=i) for i in range(SNAPSHOT_DAYS - 1, -1, -1)]
    balances: dict[str, dict[date, float]] = {
        CHECKING: _balances(ledger.txns, CHECKING, start, today, debt=False),
        JOINT: _balances(ledger.txns, JOINT, start, today, debt=False),
        SAVINGS: _balances(ledger.txns, SAVINGS, start, today, debt=False),
        CARD_1: _balances(ledger.txns, CARD_1, start, today, debt=True),
        CARD_2: _balances(ledger.txns, CARD_2, start, today, debt=True),
        BROKERAGE: brokerage.balance_by_day,
        **{aid: r.balance_by_day for aid, r in retirement.items()},
    }
    balances[MORTGAGE] = {
        d: round(312450.17 + 540.0 * ((today.year * 12 + today.month) - (d.year * 12 + d.month)), 2)
        for d in snapshot_days
    }

    items = (
        ("demo-item-bank", "demo_bank", "Demo Bank", "bank", 0),
        ("demo-item-cards", "demo_cards", "Demo Card Co", "bank", 0),
        ("demo-item-mortgage", "demo_mortgage", "Demo Mortgage", "loan", 0),
        ("demo-item-brokerage", "demo_brokerage", "Demo Brokerage", "investment", 1),
        ("demo-item-retirement", "demo_retirement", "Demo Retirement", "investment", 1),
    )
    accounts: tuple[tuple[str, str, str, str, str | None, str, str], ...] = (
        (CHECKING, "demo-item-bank", "Everyday Checking", "depository", "checking", "mine", "1001"),
        (JOINT, "demo-item-bank", "Joint Checking", "depository", "checking", "joint", "2002"),
        (SAVINGS, "demo-item-bank", "Rainy Day Savings", "depository", "savings", "mine", "3003"),
        (CARD_1, "demo-item-cards", "Rewards Card", "credit", "credit card", "mine", "4004"),
        (CARD_2, "demo-item-cards", "Travel Card", "credit", "credit card", "mine", "5005"),
        (MORTGAGE, "demo-item-mortgage", "Home Mortgage", "loan", "mortgage", "joint", "6006"),
        (
            BROKERAGE,
            "demo-item-brokerage",
            "Demo Brokerage",
            "investment",
            "brokerage",
            "mine",
            "7007",
        ),
        (
            RETIRE_401K,
            "demo-item-retirement",
            "Employer 401(k)",
            "investment",
            "401k",
            "mine",
            "8008",
        ),
        (RETIRE_ROTH, "demo-item-retirement", "Roth IRA", "investment", "roth", "mine", "9009"),
    )

    holdings = (
        (SEC_VTI, brokerage.qty[SEC_VTI], src.raw_close("VTI", today)),
        (SEC_VTSAX, brokerage.qty[SEC_VTSAX], src.raw_close("VTSAX", today)),
        (SEC_SWEEP, brokerage.sweep_value, 1.0),
        (SEC_PRIVATE, 120.0, 42.5),
    )
    balances[BROKERAGE][today] = round(sum(round(q * p, 2) for _, q, p in holdings), 2)

    with write_unit(conn):
        for item_id, inst_id, inst_name, mode, inv_complete in items:
            conn.execute(
                "INSERT INTO items(item_id, institution_id, institution_name, access_token,"
                " link_mode, status, historical_complete, investments_history_complete, products,"
                " last_attempt_at, last_success_at, last_daily_at, last_daily_attempt_at,"
                " last_holdings_at, last_inv_tx_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, 'ok', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    inst_id,
                    inst_name,
                    f"demo-access-{item_id}",
                    mode,
                    inv_complete,
                    ITEM_PRODUCTS,
                    stamp,
                    stamp,
                    today.isoformat(),
                    stamp,
                    today.isoformat(),
                    today.isoformat(),
                    created,
                ),
            )
        for account_id, item_id, name, a_type, subtype, ownership, mask in accounts:
            current = balances[account_id][today]
            available: float | None = current if a_type == "depository" else None
            if a_type == "credit":
                available = round(15000.0 - current, 2)
            conn.execute(
                "INSERT INTO accounts(account_id, item_id, name, mask, type, subtype, ownership,"
                " included, current_balance, available_balance, currency, balance_as_of, reviewed)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'USD', ?, 1)",
                (
                    account_id,
                    item_id,
                    name,
                    mask,
                    a_type,
                    subtype,
                    ownership,
                    current,
                    available,
                    stamp,
                ),
            )
            conn.executemany(
                "INSERT INTO balance_snapshots(account_id, date, current, available, captured_at)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (account_id, d.isoformat(), balances[account_id][d], None, f"{d}T06:00:00Z")
                    for d in snapshot_days
                ],
            )
        conn.executemany(
            "INSERT INTO transactions(transaction_id, account_id, date, amount, currency, name,"
            " merchant_name, pfc_primary, pfc_detailed, payment_channel, pending, updated_at)"
            " VALUES (?, ?, ?, ?, 'USD', ?, ?, ?, ?, 'other', ?, ?)",
            [
                (
                    t.id,
                    t.account_id,
                    t.date.isoformat(),
                    t.amount,
                    t.name,
                    t.merchant_name,
                    t.pfc_primary,
                    t.pfc_detailed,
                    int(t.pending),
                    stamp,
                )
                for t in ledger.txns
            ],
        )

        conn.executemany(
            "INSERT INTO securities(security_id, ticker_symbol, name, type, is_cash_equivalent,"
            " close_price, close_price_as_of, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    SEC_VTI,
                    "VTI",
                    "Total Stock Market ETF",
                    "etf",
                    0,
                    src.raw_close("VTI", today),
                    today.isoformat(),
                    stamp,
                ),
                (
                    SEC_VTSAX,
                    "VTSAX",
                    "Total Stock Market Index Fund",
                    "mutual fund",
                    0,
                    src.raw_close("VTSAX", today),
                    today.isoformat(),
                    stamp,
                ),
                (SEC_SWEEP, None, "Money Market Sweep", "cash", 1, 1.0, today.isoformat(), stamp),
                (
                    SEC_PRIVATE,
                    None,
                    "Acme Growth Fund (no ticker)",
                    "mutual fund",
                    0,
                    42.5,
                    today.isoformat(),
                    stamp,
                ),
            ],
        )
        conn.executemany(
            "INSERT INTO holdings_snapshots(account_id, security_id, date, quantity,"
            " institution_price, institution_price_as_of, institution_value, captured_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    account_id,
                    sid,
                    today.isoformat(),
                    quantity,
                    price,
                    today.isoformat(),
                    round(quantity * price, 2),
                    stamp,
                )
                for account_id, sid, quantity, price in [
                    *((BROKERAGE, sid, q, pr) for sid, q, pr in holdings),
                    *(
                        (aid, SEC_VTSAX, r.qty[SEC_VTSAX], src.raw_close("VTSAX", today))
                        for aid, r in retirement.items()
                    ),
                ]
            ],
        )
        conn.executemany(
            "INSERT INTO investment_transactions(investment_transaction_id, account_id,"
            " security_id, date, name, type, subtype, quantity, amount, price, fees)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            [
                (
                    t.id,
                    account_id,
                    t.security_id,
                    t.day.isoformat(),
                    t.name,
                    t.type,
                    t.subtype,
                    t.quantity,
                    t.amount,
                    t.price,
                )
                for account_id, source in ((BROKERAGE, brokerage), *retirement.items())
                for t in source.txns
            ],
        )


def build_demo_runtime() -> "Runtime":
    from bizviz.service import make_runtime

    home = Path(tempfile.mkdtemp(prefix="bizviz-demo-")) / "data"
    os.environ["BIZVIZ_HOME"] = str(home)
    os.environ["BIZVIZ_PLATFORM"] = "fake"
    # Its own cookie name, so signing in to the demo does not sign you out of BizViz on localhost.
    cfg = replace_config(
        load_config("production"), port=PORT_DEMO, session_cookie="bv_session_demo"
    )
    configure_logging(cfg, to_stderr=True)
    platform = Platform(
        secrets=FakeSecretStore(None),
        service=FakeServiceManager(),
        system=FakeSystemStatus(),
        trust=FakeTrustStore(),
    )
    ctx = AppContext(cfg=cfg, platform=platform, db_key_hex=secrets.token_hex(32))
    today = ctx.today()
    ctx.price_source = SyntheticPriceSource(today)

    conn = ctx.connect()
    try:
        migrate(conn)
        populate(conn, today)
        set_dashboard_password(conn, DEMO_PASSWORD)
        with write_unit(conn):
            set_setting(conn, "setup_completed_at", ctx.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
        refresh_prices(ctx, sleep=lambda _seconds: None)
        rebuild_estimates(conn, today, ctx.tz())
        rebuild_classification(conn, today)
    finally:
        conn.close()
    certs.issue_leaf(cfg, platform.secrets, ctx.now())
    return make_runtime(ctx)
