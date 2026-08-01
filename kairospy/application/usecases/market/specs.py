from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class MarketDataSpec:
    symbol: str
    kind: str
    venue: str | None = None
    market: str | None = None
    timeframe: str | None = None
    start: object | None = None
    end: object | None = None
    limit: int | None = None
    dataset: str | None = None
    stream: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _required_text(self.symbol, "symbol"))
        object.__setattr__(self, "kind", _required_text(self.kind, "kind").lower())
        object.__setattr__(self, "venue", _optional_text(self.venue))
        object.__setattr__(self, "market", _optional_text(self.market))
        object.__setattr__(self, "timeframe", _optional_text(self.timeframe))
        object.__setattr__(self, "dataset", _optional_text(self.dataset))
        object.__setattr__(self, "stream", _optional_text(self.stream))
        if self.limit is not None and self.limit < 0:
            raise ValueError("limit cannot be negative")

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, object],
        *,
        default_venue: str | None = None,
        default_market: str | None = None,
        default_kind: str | None = None,
    ) -> "MarketDataSpec":
        return cls(
            symbol=_required_text(values.get("symbol"), "data.symbol"),
            kind=_required_text(values.get("kind", default_kind), "data.kind"),
            venue=_optional_text(values.get("venue")) or default_venue,
            market=_optional_text(values.get("market")) or default_market,
            timeframe=_optional_text(values.get("timeframe")),
            start=values.get("start"),
            end=values.get("end"),
            limit=_optional_int(values.get("limit"), "data.limit"),
            dataset=_optional_text(values.get("dataset")),
            stream=_optional_text(values.get("stream")),
        )

    def with_defaults(self, *, venue: str | None = None, market: str | None = None) -> "MarketDataSpec":
        return MarketDataSpec(
            self.symbol,
            self.kind,
            venue=self.venue or venue,
            market=self.market or market,
            timeframe=self.timeframe,
            start=self.start,
            end=self.end,
            limit=self.limit,
            dataset=self.dataset,
            stream=self.stream,
        )


def _required_text(value: object, source: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source} must be a non-empty string")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _required_text(value, "value")


def _optional_int(value: object, source: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{source} must be an integer")
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"{source} cannot be negative")
    return parsed


__all__ = ["MarketDataSpec"]
