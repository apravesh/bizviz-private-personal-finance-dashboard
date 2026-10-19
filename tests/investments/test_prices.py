import sys
from datetime import date
from types import ModuleType, SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from bizviz.price_types import PriceSourceError
from bizviz.prices import (
    YahooPriceSource,
    is_cash_like,
    yahoo_symbol,
)


def _security(ticker: str | None, type: str = "equity", cash_eq: int = 0) -> dict[str, Any]:
    return {"ticker_symbol": ticker, "type": type, "is_cash_equivalent": cash_eq}


# --- symbol mapping -------------------------------------------------------------------------


def test_symbol_mapping() -> None:
    assert yahoo_symbol(_security("BRK.B")) == "BRK-B"
    assert yahoo_symbol(_security("vti", "etf")) == "VTI"
    assert yahoo_symbol(_security("VFIAX", "mutual fund")) == "VFIAX"
    assert yahoo_symbol(_security("BTC", "cryptocurrency")) == "BTC-USD"


def test_cash_like_securities_are_never_priced() -> None:
    assert yahoo_symbol(_security("USD", "cash")) is None
    assert yahoo_symbol(_security("SPAXX", "mutual fund", cash_eq=1)) is None
    assert is_cash_like(_security("USD", "cash"))
    assert is_cash_like(_security("SPAXX", "mutual fund", cash_eq=1))
    assert not is_cash_like(_security("AAA"))


@pytest.mark.parametrize("type", ["derivative", "fixed income", "loan", "other", None])
def test_unpriceable_types(type: str | None) -> None:
    assert yahoo_symbol(_security("XYZ", type or "")) is None


def test_no_ticker_is_unpriceable() -> None:
    assert yahoo_symbol(_security(None)) is None
    assert yahoo_symbol(_security("  ")) is None


# --- YahooPriceSource (yfinance is faked; the real library is never imported) ---------------


@pytest.fixture
def fake_yf(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(frame=None, raise_exc=None, kwargs=None, splits=None)
    module = ModuleType("yfinance")

    def download(symbols: Any, **kwargs: Any) -> Any:
        state.kwargs = {"symbols": symbols, **kwargs}
        if state.raise_exc:
            raise state.raise_exc
        return state.frame

    class Ticker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def splits(self) -> Any:
            if state.raise_exc:
                raise state.raise_exc
            return state.splits

    module.download = download  # type: ignore[attr-defined]
    module.Ticker = Ticker  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yfinance", module)
    return state


def test_yahoo_download_multi_symbol(fake_yf: SimpleNamespace) -> None:
    idx = pd.to_datetime(["2024-06-06", "2024-06-07"])
    cols = pd.MultiIndex.from_product([["AAA", "BBB"], ["Close", "Volume"]])
    frame = pd.DataFrame(
        [[1.5, 10, 2.5, 20], [float("nan"), 11, 2.75, 21]], index=idx, columns=cols
    )
    fake_yf.frame = frame
    out = YahooPriceSource().download_closes(
        ["AAA", "BBB", "CCC"], date(2024, 6, 1), date(2024, 6, 8)
    )
    assert out == {
        "AAA": [(date(2024, 6, 6), 1.5)],
        "BBB": [(date(2024, 6, 6), 2.5), (date(2024, 6, 7), 2.75)],
        "CCC": [],
    }
    assert fake_yf.kwargs == {
        "symbols": ["AAA", "BBB", "CCC"],
        "start": "2024-06-01",
        "end": "2024-06-08",
        "auto_adjust": False,
        "actions": True,
        "group_by": "ticker",
        "progress": False,
        "threads": False,
    }


def test_yahoo_download_single_symbol_flat_and_grouped(fake_yf: SimpleNamespace) -> None:
    idx = pd.to_datetime(["2024-06-06"])
    fake_yf.frame = pd.DataFrame({"Close": [3.0], "Volume": [1]}, index=idx)
    src = YahooPriceSource()
    assert src.download_closes(["AAA"], date(2024, 6, 1), date(2024, 6, 8)) == {
        "AAA": [(date(2024, 6, 6), 3.0)]
    }
    fake_yf.frame = pd.DataFrame(
        [[3.0, 1]], index=idx, columns=pd.MultiIndex.from_product([["AAA"], ["Close", "Volume"]])
    )
    assert src.download_closes(["AAA"], date(2024, 6, 1), date(2024, 6, 8)) == {
        "AAA": [(date(2024, 6, 6), 3.0)]
    }


def test_yahoo_download_empty_frame(fake_yf: SimpleNamespace) -> None:
    fake_yf.frame = pd.DataFrame()
    assert YahooPriceSource().download_closes(["AAA"], date(2024, 6, 1), date(2024, 6, 8)) == {
        "AAA": []
    }


def test_yahoo_splits(fake_yf: SimpleNamespace) -> None:
    fake_yf.splits = pd.Series(
        [10.0, 2.0],
        index=pd.to_datetime(["2024-06-10", "2020-08-31"]).tz_localize("America/New_York"),
    )
    assert YahooPriceSource().splits("NVDA") == [
        (date(2024, 6, 10), 10.0),
        (date(2020, 8, 31), 2.0),
    ]


def test_yahoo_errors_become_price_source_error(fake_yf: SimpleNamespace) -> None:
    fake_yf.raise_exc = RuntimeError("YFRateLimitError: Too Many Requests")
    src = YahooPriceSource()
    with pytest.raises(PriceSourceError):
        src.download_closes(["AAA"], date(2024, 6, 1), date(2024, 6, 8))
    with pytest.raises(PriceSourceError):
        src.splits("AAA")
