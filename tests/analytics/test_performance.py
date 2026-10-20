import random
import time
from datetime import date, timedelta

from bizviz.analytics.classify import rebuild_classification
from bizviz.analytics.dataset import build_dataset
from bizviz.db.connection import Connection, write_unit
from tests import factories as f
from tests.analytics.conftest import TODAY, World

N = 20_000
PFC = [
    ("FOOD_AND_DRINK", "FOOD_AND_DRINK_RESTAURANT"),
    ("GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_OTHER"),
    ("TRANSPORTATION", None),
    ("TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER"),
    ("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
    (None, None),
]


def generate(db: Connection, w: World) -> None:
    rng = random.Random(7)
    accounts = [w.chk, w.sav, w.joint, w.cc]
    start = TODAY - timedelta(days=5 * 365)
    rows = []
    for n in range(N):
        primary, detailed = rng.choice(PFC)
        amount = round(rng.choice([1, 1, 1, -1, 1]) * rng.uniform(1, 400), 2)
        if rng.random() < 0.05:
            amount = -abs(round(rng.uniform(500, 4000), 2))
            primary, detailed = "INCOME", "INCOME_SALARY"
        rows.append(
            (
                f"tx{n}",
                rng.choice(accounts),
                (start + timedelta(days=rng.randrange(5 * 365))).isoformat(),
                amount,
                f"Merchant {rng.randrange(300)} #{n}",
                primary,
                detailed,
                "2026-01-01T00:00:00Z",
            )
        )
    with write_unit(db):
        db.executemany(
            "INSERT INTO transactions(transaction_id, account_id, date, amount, name,"
            " pfc_primary, pfc_detailed, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        snaps = [
            (acc, (date(2021, 10, 15) + timedelta(days=d)).isoformat(), 1000.0 + d, "x")
            for acc in accounts + [w.brokerage]
            for d in range(0, 5 * 365, 1)
        ]
        db.executemany(
            "INSERT INTO balance_snapshots(account_id, date, current, captured_at)"
            " VALUES (?, ?, ?, ?)",
            snaps,
        )


def test_rebuild_and_dataset_performance(db: Connection, world: World) -> None:
    generate(db, world)
    f.make_rule(db, "merchant 1", recurring=True)

    started = time.perf_counter()
    rebuild_classification(db, TODAY)
    rebuild_seconds = time.perf_counter() - started

    started = time.perf_counter()
    payload = build_dataset(db, TODAY)
    dataset_seconds = time.perf_counter() - started

    assert len(payload["transactions"]) == N
    assert len(payload["net_worth"]["series"]) > 1000
    assert rebuild_seconds < 2, f"rebuild took {rebuild_seconds:.2f}s"
    assert dataset_seconds < 1, f"build_dataset took {dataset_seconds:.2f}s"
