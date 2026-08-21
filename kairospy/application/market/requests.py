from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Mapping


class Timeframe(StrEnum):
    MIN_1 = "1m"
    MIN_5 = "5m"
    MIN_15 = "15m"
    HOUR_1 = "1h"
    DAY_1 = "1d"


@dataclass(frozen=True, slots=True)
class MarketData:
    """One strategy-facing Market observation selector."""

    QUOTE: ClassVar[MarketData]
    TRADE: ClassVar[MarketData]
    ORDER_BOOK: ClassVar[MarketData]
    GREEKS: ClassVar[MarketData]

    selector: str

    def __post_init__(self) -> None:
        if not self.selector.strip():
            raise ValueError("market data selector is required")

    @classmethod
    def bar(cls, timeframe: Timeframe | str) -> MarketData:
        value = _wire_value(timeframe)
        if not value.strip():
            raise ValueError("bar timeframe is required")
        return cls(f"bar:{value}")


MarketData.QUOTE = MarketData("quote")
MarketData.TRADE = MarketData("trade")
MarketData.ORDER_BOOK = MarketData("order_book")
MarketData.GREEKS = MarketData("greeks")


class Source(StrEnum):
    MASSIVE_EQUITY = "massive-equity"
    MASSIVE_OPTIONS = "massive-options"
    BINANCE_EQUITY = "binance-equity"
    BINANCE_SPOT = "binance-spot"
    BINANCE_OPTIONS = "binance-options"


class Participant(StrEnum):
    MASSIVE = "massive"
    BINANCE = "binance"
    OKX = "okx"
    HYPERLIQUID = "hyperliquid"
    IBKR = "ibkr"


class OptionRight(StrEnum):
    CALL = "call"
    PUT = "put"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class ExpiryRange:
    """Option expiry filter represented as Unix nanosecond bounds."""

    from_unix_nanos: int | None = None
    to_unix_nanos: int | None = None
    from_days: int | None = None
    to_days: int | None = None

    def __post_init__(self) -> None:
        has_absolute = self.from_unix_nanos is not None or self.to_unix_nanos is not None
        has_relative = self.from_days is not None or self.to_days is not None
        if has_absolute and has_relative:
            raise ValueError("expiry range must be absolute or relative, not both")
        if not has_absolute and not has_relative:
            raise ValueError("expiry range requires a bound")
        for name in ("from_unix_nanos", "to_unix_nanos", "from_days", "to_days"):
            value = getattr(self, name)
            if value is not None and int(value) < 0:
                raise ValueError(f"{name} must be non-negative")
        if (
            self.from_unix_nanos is not None
            and self.to_unix_nanos is not None
            and self.from_unix_nanos > self.to_unix_nanos
        ):
            raise ValueError("expiry lower bound must not exceed upper bound")
        if (
            self.from_days is not None
            and self.to_days is not None
            and self.from_days > self.to_days
        ):
            raise ValueError("expiry lower day bound must not exceed upper day bound")

    @classmethod
    def next_days(cls, days: int, *, from_days: int = 0) -> ExpiryRange:
        return cls(from_days=from_days, to_days=days)

    @classmethod
    def between_unix_nanos(
        cls, from_unix_nanos: int | None, to_unix_nanos: int | None
    ) -> ExpiryRange:
        return cls(from_unix_nanos=from_unix_nanos, to_unix_nanos=to_unix_nanos)

    def params(self, *, now: datetime | None = None) -> dict[str, object]:
        if self.from_days is None and self.to_days is None:
            result: dict[str, object] = {}
            if self.from_unix_nanos is not None:
                result["expiry_from_unix_nanos"] = self.from_unix_nanos
            if self.to_unix_nanos is not None:
                result["expiry_to_unix_nanos"] = self.to_unix_nanos
            return result
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
        result = {}
        if self.from_days is not None:
            lower = start + timedelta(days=self.from_days)
            result["expiry_from_unix_nanos"] = _unix_nanos(lower)
        if self.to_days is not None:
            upper = start + timedelta(days=self.to_days + 1) - timedelta(microseconds=1)
            result["expiry_to_unix_nanos"] = _unix_nanos(upper)
        return result


@dataclass(frozen=True, slots=True)
class StrikeRange:
    """Option strike filter."""

    mode: str
    percent: Decimal | None = None
    lower: Decimal | None = None
    upper: Decimal | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"around_spot", "absolute"}:
            raise ValueError("strike range mode must be around_spot or absolute")
        if self.mode == "around_spot":
            if self.percent is None or self.percent <= 0:
                raise ValueError("around_spot strike range requires a positive percent")
            if self.lower is not None or self.upper is not None:
                raise ValueError("around_spot strike range cannot include absolute bounds")
        if self.mode == "absolute":
            if self.lower is None and self.upper is None:
                raise ValueError("absolute strike range requires a bound")
            if self.lower is not None and self.lower <= 0:
                raise ValueError("strike lower bound must be positive")
            if self.upper is not None and self.upper <= 0:
                raise ValueError("strike upper bound must be positive")
            if self.lower is not None and self.upper is not None and self.lower > self.upper:
                raise ValueError("strike lower bound must not exceed upper bound")

    @classmethod
    def around_spot(cls, *, percent: Decimal | str) -> StrikeRange:
        return cls("around_spot", percent=Decimal(str(percent)))

    @classmethod
    def between(
        cls, lower: Decimal | str | None = None, upper: Decimal | str | None = None
    ) -> StrikeRange:
        return cls(
            "absolute",
            lower=None if lower is None else Decimal(str(lower)),
            upper=None if upper is None else Decimal(str(upper)),
        )

    def params(self) -> dict[str, object]:
        if self.mode == "around_spot":
            assert self.percent is not None
            return {"strike_mode": "around_spot", "strike_percent": str(self.percent)}
        result: dict[str, object] = {"strike_mode": "absolute"}
        if self.lower is not None:
            result["strike_lower"] = str(self.lower)
        if self.upper is not None:
            result["strike_upper"] = str(self.upper)
        return result


@dataclass(frozen=True, slots=True)
class OptionFilter:
    expiry: ExpiryRange | None = None
    strike: StrikeRange | None = None
    right: OptionRight | str = OptionRight.BOTH
    limit: int | None = None

    def __post_init__(self) -> None:
        right = _wire_value(self.right).lower()
        if right not in {"call", "put", "both"}:
            raise ValueError("option right must be call, put, or both")
        object.__setattr__(self, "right", right)
        if self.limit is not None and self.limit <= 0:
            raise ValueError("option filter limit must be positive")

    def params(self, *, now: datetime | None = None) -> dict[str, object]:
        result: dict[str, object] = {"right": self.right}
        if self.expiry is not None:
            result.update(self.expiry.params(now=now))
        if self.strike is not None:
            result.update(self.strike.params())
        if self.limit is not None:
            result["limit"] = self.limit
        return result


@dataclass(frozen=True, slots=True)
class Options:
    """Dynamic option-market selection target for one underlying."""

    underlying: object
    filter: OptionFilter = field(default_factory=OptionFilter)

    @classmethod
    def on(cls, underlying: object) -> Options:
        return cls(underlying)

    def where(
        self,
        *,
        filter: OptionFilter | None = None,
        expiry: ExpiryRange | None = None,
        strike: StrikeRange | None = None,
        right: OptionRight | str | None = None,
        limit: int | None = None,
    ) -> Options:
        has_filter_field = (
            expiry is not None
            or strike is not None
            or right is not None
            or limit is not None
        )
        if filter is not None and has_filter_field:
            raise ValueError("pass either filter or filter fields, not both")
        if filter is None:
            filter = OptionFilter(
                expiry=expiry,
                strike=strike,
                right=OptionRight.BOTH if right is None else right,
                limit=limit,
            )
        return Options(self.underlying, filter)


@dataclass(frozen=True, slots=True)
class SourceSet:
    """Selection of Market data source routes for one subscription intent."""

    ALL: ClassVar[SourceSet]

    source_ids: tuple[str, ...] | None = None
    all: bool = False

    def __post_init__(self) -> None:
        if self.all and self.source_ids:
            raise ValueError("SourceSet.ALL cannot include explicit source ids")
        if self.source_ids is not None:
            source_ids = tuple(_wire_value(value) for value in self.source_ids)
            if not source_ids:
                raise ValueError("source set must not be empty")
            if any(not value.strip() for value in source_ids):
                raise ValueError("source ids must be non-empty strings")
            object.__setattr__(self, "source_ids", source_ids)

    @classmethod
    def only(cls, *sources: Source | str) -> SourceSet:
        return cls(tuple(_wire_value(source) for source in sources))


SourceSet.ALL = SourceSet(all=True)


@dataclass(frozen=True, slots=True)
class ParticipantSet:
    """Selection of market-data participants for one subscription intent."""

    ALL: ClassVar[ParticipantSet]

    participant_ids: tuple[str, ...] | None = None
    all: bool = False

    def __post_init__(self) -> None:
        if self.all and self.participant_ids:
            raise ValueError("ParticipantSet.ALL cannot include explicit participants")
        if self.participant_ids is not None:
            participant_ids = tuple(
                _participant_wire_value(value) for value in self.participant_ids
            )
            if not participant_ids:
                raise ValueError("participant set must not be empty")
            if any(not value.strip() for value in participant_ids):
                raise ValueError("participant ids must be non-empty strings")
            object.__setattr__(self, "participant_ids", participant_ids)

    @classmethod
    def only(cls, *participants: Participant | str) -> ParticipantSet:
        return cls(tuple(_participant_wire_value(participant) for participant in participants))


ParticipantSet.ALL = ParticipantSet(all=True)


@dataclass(frozen=True, slots=True)
class SubscriptionRequest:
    """Market-owned strategy subscription request used at the process boundary."""

    subject: str
    selectors: tuple[str, ...] = ()
    source_id: str | None = None
    source_ids: tuple[str, ...] = ()
    exchange: str | None = None
    market_type: str | None = None
    asset_type: str | None = None
    identity: str | None = None
    params: Mapping[str, object] = field(default_factory=dict)
    dynamic: bool = False

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise ValueError("subscription subject is required")
        if any(not selector.strip() for selector in self.selectors):
            raise ValueError("subscription selectors must be non-empty strings")
        if self.source_id is not None and not self.source_id.strip():
            raise ValueError("subscription source_id must be a non-empty string")
        if self.source_id is not None and self.source_ids:
            raise ValueError("use either source_id or source_ids, not both")
        if any(not source_id.strip() for source_id in self.source_ids):
            raise ValueError("subscription source_ids must be non-empty strings")
        object.__setattr__(self, "selectors", tuple(self.selectors))
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


def _wire_value(value: object) -> str:
    enum_value = getattr(value, "value", None)
    return enum_value if isinstance(enum_value, str) else str(value)


def _participant_wire_value(value: object) -> str:
    text = _wire_value(value).strip().lower()
    if text.startswith("data_provider:"):
        return text.removeprefix("data_provider:")
    if text.startswith("provider:"):
        return text.removeprefix("provider:")
    return text


def _unix_nanos(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)
