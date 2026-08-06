"""Business requests used by the public market application API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Mapping, TypedDict

from kairospy.domain.market import MarketEvent, MarketSelector
from kairospy.domain.reference import MarketRef
from kairospy.application.usecases.market.domain.specs import MarketDataKind, MarketOptions, MarketTime
from kairospy.application.usecases.market.domain.datasets import parse_market_dataset_id

MarketDataMode = Literal["append", "overwrite"]
MarketOptionValue = str | int | float | bool | None


class MarketDataRow(TypedDict, total=False):
    """Normalized persisted row before conversion to a market domain value.

    Unknown vendor fields must stay in the adapter's private payload.  This
    DTO contains only fields understood by the market boundary.
    """

    domain: str
    kind: str
    time: MarketTime
    subject_type: str
    subject_id: str
    market_id: str
    market_key: str
    instrument_id: str
    rate_id: str
    timeframe: str
    interval: str
    venue: str
    market: str
    source_symbol: str
    source: str
    basis: str
    trade_id: str
    id: str
    side: str
    nonce: int
    open: Decimal | int | float | str | None
    high: Decimal | int | float | str | None
    low: Decimal | int | float | str | None
    close: Decimal | int | float | str | None
    volume: Decimal | int | float | str | None
    bid: Decimal | int | float | str | None
    ask: Decimal | int | float | str | None
    bid_size: Decimal | int | float | str | None
    ask_size: Decimal | int | float | str | None
    price: Decimal | int | float | str | None
    size: Decimal | int | float | str | None
    amount: Decimal | int | float | str | None
    cost: Decimal | int | float | str | None
    rate: Decimal | int | float | str | None
    delta: Decimal | int | float | str | None
    gamma: Decimal | int | float | str | None
    theta: Decimal | int | float | str | None
    vega: Decimal | int | float | str | None
    rho: Decimal | int | float | str | None
    implied_volatility: Decimal | int | float | str | None
    mark_price: Decimal | int | float | str | None
    underlying_price: Decimal | int | float | str | None
    bids: tuple[tuple[Decimal | int | float | str, Decimal | int | float | str], ...]
    asks: tuple[tuple[Decimal | int | float | str, Decimal | int | float | str], ...]


class MarketWarmupStatus(TypedDict, total=False):
    state: Literal["empty", "failed", "ready"]
    updated_at: str
    retry_at: float
    error_type: str


class MarketDatasetListResult(TypedDict):
    root: str
    datasets: tuple[str, ...]
    aliases: Mapping[str, str]
    count: int


class MarketDatasetInspectResult(TypedDict):
    dataset: str
    path: str | None
    rows: int
    start: str | None
    end: str | None
    columns: tuple[str, ...]
    sample: tuple[MarketDataRow, ...]


class MarketDatasetAliasResult(TypedDict):
    dataset: str
    alias: str
    path: str


class MarketDatasetPruneResult(TypedDict):
    dataset: str
    deleted_rows: int
    remaining_rows: int


class MarketFeature(TypedDict):
    kind: str
    label: str
    selector: str
    command_kind: str
    timeframe_required: bool


class MarketCapability(TypedDict):
    venue: str
    exchange: str
    market: str
    driver: str
    status: Literal["configured", "not_configured"]
    reason: str | None
    historical: tuple[MarketFeature, ...]
    live: tuple[MarketFeature, ...]


class MarketCapabilitiesResult(TypedDict):
    driver: str
    markets: tuple[MarketCapability, ...]
    count: int


class MarketSourceCheckResult(TypedDict):
    valid: bool
    reason: str | None
    driver: str
    venue: str
    market: str
    symbol: str
    kind: str
    mode: Literal["historical", "live"]
    timeframe: str | None
    dataset: str | None
    capability: MarketCapability


class MarketDoctorResult(TypedDict):
    valid: bool
    exchange: str
    driver: str


class MarketPrefetchDownload(TypedDict):
    subscription: str
    dataset: str
    path: str | None
    kind: str
    symbol: str
    venue: str | None
    market: str | None
    timeframe: str | None
    start: MarketTime | None
    end: MarketTime | None
    supported: bool
    status: str


class MarketPrefetchResult(TypedDict):
    launch_id: str
    config: str
    dry_run: bool
    count: int
    plan: tuple[MarketPrefetchDownload, ...]
    downloads: tuple[MarketPrefetchDownload, ...]


@dataclass(frozen=True, slots=True)
class MarketDataReadRequest:
    spec: "MarketDataSpec"
    columns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "columns", tuple(self.columns))


@dataclass(frozen=True, slots=True)
class MarketDataDownloadRequest:
    spec: "MarketDataSpec"
    mode: MarketDataMode = "append"
    options: MarketOptions = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MarketDataEnsureRequest:
    spec: "MarketDataSpec"
    mode: MarketDataMode = "append"
    options: MarketOptions = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MarketDataPersistRequest:
    spec: "MarketDataSpec"
    events: tuple[MarketEvent, ...] = ()
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class MarketSubscriptionRequest:
    market: MarketRef
    selectors: tuple[MarketSelector | type, ...]
    identity: str | None = None
    params: MarketOptions = field(default_factory=dict)
    dataset_id: str | None = None


from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.application.usecases.market.domain.subscriptions import (
    DataSubscription,
    DataSubscriptionGroup,
    DynamicMarketDataSubscriptionSpec,
    MarketDataSubscriptionGroupSpec,
    MarketDataSubscriptionSpec,
)

__all__ = [
    "MarketDataDownloadRequest",
    "MarketDatasetAliasResult",
    "MarketDatasetInspectResult",
    "MarketDatasetListResult",
    "MarketDatasetPruneResult",
    "MarketCapability",
    "MarketCapabilitiesResult",
    "MarketDoctorResult",
    "MarketFeature",
    "MarketPrefetchDownload",
    "MarketPrefetchResult",
    "MarketSourceCheckResult",
    "MarketDataEnsureRequest",
    "MarketDataKind",
    "MarketDataMode",
    "MarketDataPersistRequest",
    "MarketDataReadRequest",
    "MarketDataRow",
    "DataSubscription",
    "DataSubscriptionGroup",
    "DynamicMarketDataSubscriptionSpec",
    "MarketDataSubscriptionGroupSpec",
    "MarketDataSubscriptionSpec",
    "parse_market_dataset_id",
    "MarketWarmupStatus",
    "MarketOptionValue",
    "MarketOptions",
    "MarketSubscriptionRequest",
    "MarketTime",
]
