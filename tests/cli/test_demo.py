from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bizviz import demo
from bizviz.analytics.dataset import build_dataset
from bizviz.api.app import create_app
from bizviz.service import Runtime


def build() -> Runtime:
    """build_demo_runtime repoints BIZVIZ_HOME/BIZVIZ_PLATFORM; undo that for other tests."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("BIZVIZ_HOME", "unused")
        mp.setenv("BIZVIZ_PLATFORM", "fake")
        return demo.build_demo_runtime()


@pytest.fixture(scope="module")
def runtime() -> Runtime:
    return build()


@pytest.fixture(scope="module")
def data(runtime: Runtime) -> dict[str, Any]:
    conn = runtime.ctx.connect()
    try:
        return dict(build_dataset(conn, runtime.ctx.today()))
    finally:
        conn.close()


def flows(data: dict[str, Any], view: str = "mine") -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for tx in data["transactions"]:
        if tx[view] is not None:
            out.setdefault(tx[view]["flow"], []).append(tx)
    return out


def test_demo_uses_the_demo_port_and_a_throwaway_home(runtime: Runtime) -> None:
    assert runtime.ctx.cfg.port == 8444
    assert runtime.ctx.cfg.home.exists()


def test_demo_has_earnings_spending_and_internal_flows(data: dict[str, Any]) -> None:
    by_flow = flows(data)
    assert len(by_flow["earning"]) > 50
    assert len(by_flow["spending"]) > 500
    assert len(by_flow["internal"]) > 50
    assert flows(data, "household")["earning"]


def test_demo_has_recurring_series(data: dict[str, Any]) -> None:
    names = {r["merchant_key"] for r in data["recurring"]["mine"]}
    assert {"netflix", "spotify"} <= names
    assert all(r["count"] >= 3 for r in data["recurring"]["mine"])


def test_demo_net_worth_covers_assets_and_debts(data: dict[str, Any]) -> None:
    series = data["net_worth"]["series"]
    assert len(series) >= 30
    assert series[-1]["mine"]["assets"] > 0
    assert series[-1]["household"]["debts"] > series[-1]["mine"]["debts"] > 0


def test_demo_accounts_cover_every_type(data: dict[str, Any]) -> None:
    kinds = {(a["type"], a["ownership"]) for a in data["accounts"]}
    assert ("depository", "mine") in kinds
    assert ("depository", "joint") in kinds
    assert ("credit", "mine") in kinds
    assert ("loan", "mine") in kinds or ("loan", "joint") in kinds
    assert ("investment", "mine") in kinds
    assert all(a["reviewed"] for a in data["accounts"])


def test_demo_investment_actual_and_estimated_with_split_and_excluded_fund(
    data: dict[str, Any],
) -> None:
    (inv,) = data["investments"]
    assert inv["actual"] and inv["actual"][-1][1] > 100_000
    estimated = inv["estimated"]
    assert len(estimated) > 365
    assert inv["estimate_meta"]["status"] == "ok"
    assert inv["estimate_meta"]["excluded_count"] == 1
    assert inv["estimate_meta"]["excluded_value"] > 0
    values = [v for _, v in estimated]
    jumps = [abs(b / a - 1) for a, b in zip(values, values[1:], strict=False)]
    assert max(jumps) < 0.2, "a 2:1 split must not show up as a jump in the estimate"


def test_synthetic_prices_are_split_adjusted() -> None:
    today = date(2026, 10, 19)
    source = demo.SyntheticPriceSource(today)
    split_day = demo.split_date_for(today)
    ((day, ratio),) = source.splits("VTI")
    assert day == split_day
    assert ratio == demo.SPLIT_RATIO
    before = date.fromordinal(split_day.toordinal() - 3)
    assert source.raw_close("VTI", before) == pytest.approx(
        source.adjusted_close("VTI", before) * demo.SPLIT_RATIO
    )
    assert source.raw_close("VTI", split_day) == pytest.approx(
        source.adjusted_close("VTI", split_day)
    )


def test_demo_is_deterministic(data: dict[str, Any]) -> None:
    again = build()
    conn = again.ctx.connect()
    try:
        second = dict(build_dataset(conn, again.ctx.today()))
    finally:
        conn.close()

    def digest(d: dict[str, Any]) -> list[tuple[Any, ...]]:
        return [
            (t["date"], t["amount"], t["name"], t["mine"] and t["mine"]["flow"])
            for t in d["transactions"]
        ]

    assert digest(second) == digest(data)
    assert second["investments"][0]["actual"] == data["investments"][0]["actual"]


def test_demo_runtime_serves_the_app(runtime: Runtime) -> None:
    client = TestClient(
        create_app(runtime),
        base_url="https://localhost:8444",
        client=("127.0.0.1", 50000),
        headers={"X-BizViz": "1"},
    )
    res = client.post("/api/auth/login", json={"password": demo.DEMO_PASSWORD})
    assert res.status_code == 204
    payload = client.get("/api/data")
    assert payload.status_code == 200
    assert len(payload.json()["transactions"]) > 500
