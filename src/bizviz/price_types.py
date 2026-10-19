from datetime import date
from typing import Protocol


class PriceSourceError(Exception):
    pass


class PriceSource(Protocol):
    def download_closes(
        self, symbols: list[str], start: date, end_exclusive: date
    ) -> dict[str, list[tuple[date, float]]]:
        """Yahoo's split-ADJUSTED `Close` per symbol; symbols with no data map to []."""
        ...

    def splits(self, symbol: str) -> list[tuple[date, float]]:
        """Full split history; ratio 4.0 means a 4-for-1 forward split."""
        ...
