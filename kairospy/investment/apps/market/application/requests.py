from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar, Literal, TypeAlias


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


class Provider(StrEnum):
    """A user-selectable Market data provider, not a runtime feed."""

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
class ProviderPreference:
    """How Market should select eligible provider routes."""

    mode: Literal["automatic", "prefer", "require", "all_eligible"]
    providers: tuple[Provider | str, ...] = ()

    def __post_init__(self) -> None:
        providers = tuple(_provider_value(value) for value in self.providers)
        if self.mode in {"automatic", "all_eligible"} and providers:
            raise ValueError(f"{self.mode} provider preference cannot list providers")
        if self.mode in {"prefer", "require"} and not providers:
            raise ValueError(f"{self.mode} provider preference requires providers")
        object.__setattr__(self, "providers", providers)

    @classmethod
    def automatic(cls) -> ProviderPreference:
        return cls("automatic")

    @classmethod
    def prefer(cls, *providers: Provider | str) -> ProviderPreference:
        return cls("prefer", providers)

    @classmethod
    def require(cls, *providers: Provider | str) -> ProviderPreference:
        return cls("require", providers)

    @classmethod
    def all_eligible(cls) -> ProviderPreference:
        return cls("all_eligible")

    def wire(self) -> dict[str, object]:
        value: dict[str, object] = {"mode": self.mode}
        if self.providers:
            value["providers"] = list(self.providers)
        return value


@dataclass(frozen=True, slots=True)
class ObservationRequirement:
    kind: str
    qualifier: str | None = None

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("observation kind is required")
        if self.qualifier is not None and not self.qualifier.strip():
            raise ValueError("observation qualifier must be non-empty")

    @classmethod
    def from_selector(cls, value: MarketData | str) -> ObservationRequirement:
        selector = value.selector if isinstance(value, MarketData) else str(value)
        kind, separator, qualifier = selector.partition(":")
        return cls(kind, qualifier if separator else None)

    @property
    def selector(self) -> str:
        return self.kind if self.qualifier is None else f"{self.kind}:{self.qualifier}"

    def wire(self) -> dict[str, object]:
        return {"kind": self.kind, "qualifier": self.qualifier}


@dataclass(frozen=True, slots=True)
class CanonicalMarketTarget:
    market_id: str

    def wire(self) -> dict[str, object]:
        return {"type": "market", "market_id": self.market_id}


@dataclass(frozen=True, slots=True)
class ConsolidatedInstrumentTarget:
    instrument_id: str
    network_id: str | None = None

    def wire(self) -> dict[str, object]:
        return {
            "type": "consolidated_instrument",
            "instrument_id": self.instrument_id,
            "network_id": self.network_id,
        }


@dataclass(frozen=True, slots=True)
class OptionsTarget:
    underlying_market_id: str | None = None
    underlying_instrument_id: str | None = None
    expiry_from_unix_nanos: int | None = None
    expiry_to_unix_nanos: int | None = None
    strike_lower: str | None = None
    strike_upper: str | None = None
    option_right: str | None = None
    limit: int | None = None
    progressive: bool = False

    def __post_init__(self) -> None:
        if (self.underlying_market_id is None) == (self.underlying_instrument_id is None):
            raise ValueError("options target requires exactly one underlying identity")

    def wire(self) -> dict[str, object]:
        return {
            "type": "options",
            "underlying_market_id": self.underlying_market_id,
            "underlying_instrument_id": self.underlying_instrument_id,
            "expiry_from_unix_nanos": self.expiry_from_unix_nanos,
            "expiry_to_unix_nanos": self.expiry_to_unix_nanos,
            "strike_lower": self.strike_lower,
            "strike_upper": self.strike_upper,
            "option_right": self.option_right,
            "limit": self.limit,
            "progressive": self.progressive,
        }


MarketTarget: TypeAlias = CanonicalMarketTarget | ConsolidatedInstrumentTarget | OptionsTarget


@dataclass(frozen=True, slots=True)
class SubscriptionRequest:
    """Market-owned strategy subscription request used at the process boundary."""

    target: MarketTarget
    observations: tuple[ObservationRequirement, ...]
    provider_preference: ProviderPreference = field(
        default_factory=ProviderPreference.automatic
    )

    def __post_init__(self) -> None:
        if not self.observations:
            raise ValueError("subscription observations are required")
        object.__setattr__(self, "observations", tuple(self.observations))


def _wire_value(value: object) -> str:
    enum_value = getattr(value, "value", None)
    return enum_value if isinstance(enum_value, str) else str(value)


def _provider_value(value: object) -> str:
    text = _wire_value(value).strip().lower()
    if not text or any(character.isspace() for character in text):
        raise ValueError("provider must be a non-empty canonical identifier")
    return text


def _unix_nanos(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)
