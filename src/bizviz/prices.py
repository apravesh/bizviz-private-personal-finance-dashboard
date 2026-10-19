"""Yahoo Finance price source.

Privacy rule: log only counts, never ticker names.
"""

import logging
import math
from datetime import date
from typing import Any

from bizviz.price_types import PriceSourceError

log = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.WARNING)

_YAHOO_SYMBOL_TYPES = ("equity", "etf", "mutual fund")


def is_cash_like(security: Any) -> bool:
    """Cash-equivalent securities (and type 'cash') are cash, not positions."""
    return bool(security["is_cash_equivalent"]) or (security["type"] or "").lower() == "cash"


def yahoo_symbol(security: Any) -> str | None:
    """Yahoo symbol for a securities row, or None when unpriceable (or cash-like)."""
    if is_cash_like(security):
        return None
    ticker = (security["ticker_symbol"] or "").strip()
    if not ticker:
        return None
    sec_type = (security["type"] or "").lower()
    if sec_type in _YAHOO_SYMBOL_TYPES:
        return ticker.replace(".", "-").upper()
    if sec_type == "cryptocurrency":
        return f"{ticker.upper()}-USD"
    return None


class YahooPriceSource:
    def download_closes(
        self, symbols: list[str], start: date, end_exclusive: date
    ) -> dict[str, list[tuple[date, float]]]:
        try:
            import yfinance

            frame = yfinance.download(
                symbols,
                start=start.isoformat(),
                end=end_exclusive.isoformat(),
                auto_adjust=False,
                actions=True,
                group_by="ticker",
                progress=False,
                threads=False,
            )
            return {symbol: _extract_closes(frame, symbol) for symbol in symbols}
        except Exception as exc:
            raise PriceSourceError(type(exc).__name__) from exc

    def splits(self, symbol: str) -> list[tuple[date, float]]:
        try:
            import yfinance

            series = yfinance.Ticker(symbol).splits
            out: list[tuple[date, float]] = []
            for ts, ratio in series.items():
                if ratio is not None and float(ratio) > 0:
                    out.append((ts.date(), float(ratio)))
            return out
        except Exception as exc:
            raise PriceSourceError(type(exc).__name__) from exc


def _extract_closes(frame: Any, symbol: str) -> list[tuple[date, float]]:
    if frame is None or len(frame) == 0:
        return []
    columns = frame.columns
    if columns.nlevels > 1:
        if symbol not in columns.get_level_values(0):
            return []
        sub = frame[symbol]
    else:
        sub = frame
    if "Close" not in sub.columns:
        return []
    out: list[tuple[date, float]] = []
    for ts, value in sub["Close"].items():
        if value is None or math.isnan(float(value)):
            continue
        out.append((ts.date(), float(value)))
    return out
