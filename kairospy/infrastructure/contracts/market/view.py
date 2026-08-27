"""Typed Market current-view contract backed by the owner Rust binding."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from importlib import import_module
from pathlib import Path
from typing import Any


class MarketViewKind(str, Enum):
    QUOTE = "quote"
    BAR = "bar"
    GREEKS = "greeks"
    RATE = "rate"
    TICKER_24H = "ticker-24h"
    MARK_PRICE = "mark-price"
    FUNDING_RATE = "funding-rate"
    OPEN_INTEREST = "open-interest"
    INDEX_PRICE = "index-price"
    ORDER_BOOK = "order-book"
    FRESHNESS = "freshness"


@dataclass(frozen=True, slots=True)
class MarketViewKey:
    scope_key: str
    provider: str
    kind: MarketViewKind
    qualifier: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.scope_key
            or self.scope_key.strip() != self.scope_key
            or not self.provider
            or self.provider.strip() != self.provider
            or (self.qualifier is not None and self.qualifier.strip() != self.qualifier)
        ):
            raise ValueError("Market indexed view identity is invalid")

    def canonical_key(self) -> str:
        return (
            f"scope={self.scope_key};provider={self.provider};"
            f"view={self.kind.value};qualifier={self.qualifier or ''}"
        )


@dataclass(frozen=True, slots=True)
class MarketCurrentEvidence:
    resource_epoch: int
    producer_incarnation: int
    applied_event_sequence: int
    committed_at_unix_nanos: int
    source_event_id: str | None
    synchronized: bool


@dataclass(frozen=True, slots=True)
class MarketObservationScope:
    kind: str
    market_id: str | None
    instrument_id: str | None
    network_id: str | None


@dataclass(frozen=True, slots=True)
class MarketQuoteCurrent:
    evidence: MarketCurrentEvidence
    quote_id: str | None
    scope: MarketObservationScope
    instrument_id: str
    provider: str
    bid_price: Decimal | None
    bid_quantity: Decimal | None
    ask_price: Decimal | None
    ask_quantity: Decimal | None
    bid_venue_code: str | None
    ask_venue_code: str | None
    tape: int | None
    source_observed_at_unix_nanos: int
    received_at_unix_nanos: int


@dataclass(frozen=True, slots=True)
class MarketBarCurrent:
    evidence: MarketCurrentEvidence
    scope: MarketObservationScope
    instrument_id: str
    provider: str
    bar_spec_id: str
    kind: str
    window_start_unix_nanos: int
    window_end_unix_nanos: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    source_observed_at_unix_nanos: int
    received_at_unix_nanos: int


@dataclass(frozen=True, slots=True)
class MarketGreeksCurrent:
    evidence: MarketCurrentEvidence
    scope: MarketObservationScope
    instrument_id: str
    provider: str
    expiry_unix_nanos: int | None
    strike: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    vega: Decimal | None
    theta: Decimal | None
    implied_volatility: Decimal | None
    source_observed_at_unix_nanos: int
    received_at_unix_nanos: int
    derivation_id: str | None

@dataclass(frozen=True, slots=True)
class MarketRateCurrent:
    evidence: MarketCurrentEvidence; rate_id: str; scope: MarketObservationScope; instrument_id: str; provider: str; basis: str; value: Decimal; mark_price: Decimal | None; source_observed_at_unix_nanos: int; received_at_unix_nanos: int
@dataclass(frozen=True, slots=True)
class MarketTicker24hCurrent:
    evidence: MarketCurrentEvidence; scope: MarketObservationScope; instrument_id: str; provider: str; last_price: Decimal | None; bid_price: Decimal | None; bid_quantity: Decimal | None; ask_price: Decimal | None; ask_quantity: Decimal | None; open_price: Decimal | None; high_price: Decimal | None; low_price: Decimal | None; volume_base: Decimal | None; volume_quote: Decimal | None; price_change_abs: Decimal | None; price_change_pct: Decimal | None; vwap: Decimal | None; mark_price: Decimal | None; source_observed_at_unix_nanos: int; received_at_unix_nanos: int
@dataclass(frozen=True, slots=True)
class MarketMarkPriceCurrent:
    evidence: MarketCurrentEvidence; scope: MarketObservationScope; instrument_id: str; provider: str; mark_price: Decimal; index_price: Decimal | None; estimated_settlement_price: Decimal | None; funding_rate: Decimal | None; next_funding_time_unix_nanos: int | None; source_observed_at_unix_nanos: int; received_at_unix_nanos: int
@dataclass(frozen=True, slots=True)
class MarketFundingRateCurrent:
    evidence: MarketCurrentEvidence; scope: MarketObservationScope; instrument_id: str; provider: str; funding_rate: Decimal; funding_period_seconds: int; next_funding_time_unix_nanos: int | None; source_observed_at_unix_nanos: int; received_at_unix_nanos: int
@dataclass(frozen=True, slots=True)
class MarketOpenInterestCurrent:
    evidence: MarketCurrentEvidence; scope: MarketObservationScope; instrument_id: str; provider: str; contracts: Decimal; quote_value: Decimal | None; change_24h: Decimal | None; change_pct_24h: Decimal | None; source_observed_at_unix_nanos: int; received_at_unix_nanos: int
@dataclass(frozen=True, slots=True)
class MarketIndexPriceCurrent:
    evidence: MarketCurrentEvidence; scope: MarketObservationScope; instrument_id: str; provider: str; spot_index_price: Decimal | None; contract_index_price: Decimal | None; index_price: Decimal | None; funding_rate: Decimal | None; source_observed_at_unix_nanos: int; received_at_unix_nanos: int
@dataclass(frozen=True, slots=True)
class MarketOrderBookLevel:
    price: Decimal; quantity: Decimal; order_count: int
@dataclass(frozen=True, slots=True)
class MarketOrderBookCurrent:
    evidence: MarketCurrentEvidence; provider: str; market_id: str; instrument_id: str; sequence: int; source_observed_at_unix_nanos: int; received_at_unix_nanos: int; checksum: str | None; depth_policy: str; bids: tuple[MarketOrderBookLevel,...]; asks: tuple[MarketOrderBookLevel,...]
@dataclass(frozen=True, slots=True)
class MarketFreshnessCurrent:
    evidence: MarketCurrentEvidence; provider: str; scope: MarketObservationScope; data_kind: str; last_event_time_unix_nanos: int | None; last_received_time_unix_nanos: int | None; age_nanos: int; event_sequence: int; status: str


MarketCurrentValue = MarketQuoteCurrent | MarketBarCurrent | MarketGreeksCurrent | MarketRateCurrent | MarketTicker24hCurrent | MarketMarkPriceCurrent | MarketFundingRateCurrent | MarketOpenInterestCurrent | MarketIndexPriceCurrent | MarketOrderBookCurrent | MarketFreshnessCurrent


@dataclass(frozen=True, slots=True)
class MarketIndexedFrame:
    key: MarketViewKey
    metadata: MarketCurrentEvidence
    value: MarketCurrentValue


def market_indexed_environment_path(root: str | Path) -> Path:
    return Path(root) / "views" / "v3" / "Market" / "market-main" / "epoch-1" / "current.lmdb"


class MarketIndexedViewQueries:
    """Read typed Market entities through the owner Rust contract."""

    def __init__(
        self,
        root: str | Path,
        *,
        workspace_id: str,
        launch_id: str | None,
        instance_id: str | None,
    ) -> None:
        self._root = Path(root)
        self._path = market_indexed_environment_path(root)
        self._configuration = (workspace_id, launch_id, instance_id)
        self._reader: Any | None = None

    def _open_reader(self) -> Any:
        if self._reader is None:
            native = import_module("kairospy._native_market_contract")
            build = native.build_info()
            if build.api_version != 1 or build.owner != "Market":
                raise ImportError("kairospy Market native contract ABI mismatch")
            self._reader = native.MarketCurrentView(
                self._root,
                self._configuration[0],
                self._configuration[1],
                self._configuration[2],
            )
        return self._reader

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    @property
    def path(self) -> Path:
        return self._path

    def read(self, key: MarketViewKey) -> MarketIndexedFrame | None:
        reader = self._open_reader()
        if key.kind is MarketViewKind.QUOTE:
            native = reader.quote(key.scope_key, key.provider)
            value = None if native is None else _quote(native)
        elif key.kind is MarketViewKind.BAR:
            if key.qualifier is None:
                raise ValueError("Market bar current view requires a qualifier")
            native = reader.bar(key.scope_key, key.provider, key.qualifier)
            value = None if native is None else _bar(native)
        elif key.kind is MarketViewKind.GREEKS:
            native = reader.greeks(key.scope_key, key.provider)
            value = None if native is None else _greeks(native)
        elif key.kind is MarketViewKind.RATE: native = reader.rate(key.scope_key,key.provider,key.qualifier); value=None if native is None else _rate(native)
        elif key.kind is MarketViewKind.TICKER_24H: native=reader.ticker_24h(key.scope_key,key.provider,key.qualifier); value=None if native is None else _ticker(native)
        elif key.kind is MarketViewKind.MARK_PRICE: native=reader.mark_price(key.scope_key,key.provider,key.qualifier); value=None if native is None else _mark(native)
        elif key.kind is MarketViewKind.FUNDING_RATE: native=reader.funding_rate(key.scope_key,key.provider,key.qualifier); value=None if native is None else _funding(native)
        elif key.kind is MarketViewKind.OPEN_INTEREST: native=reader.open_interest(key.scope_key,key.provider,key.qualifier); value=None if native is None else _open_interest(native)
        elif key.kind is MarketViewKind.INDEX_PRICE: native=reader.index_price(key.scope_key,key.provider,key.qualifier); value=None if native is None else _index(native)
        elif key.kind is MarketViewKind.ORDER_BOOK: native=reader.order_book(key.scope_key,key.provider,key.qualifier); value=None if native is None else _order_book(native)
        else: native=reader.freshness(key.scope_key,key.provider,key.qualifier); value=None if native is None else _freshness(native)
        if value is None:
            return None
        return MarketIndexedFrame(key, value.evidence, value)


def _evidence(value: Any) -> MarketCurrentEvidence:
    return MarketCurrentEvidence(
        resource_epoch=int(value.resource_epoch),
        producer_incarnation=int(value.producer_incarnation),
        applied_event_sequence=int(value.applied_event_sequence),
        committed_at_unix_nanos=int(value.committed_at_unix_nanos),
        source_event_id=value.source_event_id,
        synchronized=bool(value.synchronized),
    )


def _scope(value: Any) -> MarketObservationScope:
    return MarketObservationScope(
        kind=str(value.kind),
        market_id=value.market_id,
        instrument_id=value.instrument_id,
        network_id=value.network_id,
    )


def _decimal(value: Any | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(int(value.mantissa)).scaleb(-int(value.scale))


def _quote(value: Any) -> MarketQuoteCurrent:
    return MarketQuoteCurrent(
        evidence=_evidence(value.evidence),
        quote_id=value.quote_id,
        scope=_scope(value.scope),
        instrument_id=str(value.instrument_id),
        provider=str(value.provider),
        bid_price=_decimal(value.bid_price),
        bid_quantity=_decimal(value.bid_quantity),
        ask_price=_decimal(value.ask_price),
        ask_quantity=_decimal(value.ask_quantity),
        bid_venue_code=value.bid_venue_code,
        ask_venue_code=value.ask_venue_code,
        tape=value.tape,
        source_observed_at_unix_nanos=int(value.source_observed_at_unix_nanos),
        received_at_unix_nanos=int(value.received_at_unix_nanos),
    )


def _bar(value: Any) -> MarketBarCurrent:
    open_price = _decimal(value.open)
    high = _decimal(value.high)
    low = _decimal(value.low)
    close = _decimal(value.close)
    if open_price is None or high is None or low is None or close is None:
        raise ValueError("Market native bar omitted a required price")
    return MarketBarCurrent(
        evidence=_evidence(value.evidence),
        scope=_scope(value.scope),
        instrument_id=str(value.instrument_id),
        provider=str(value.provider),
        bar_spec_id=str(value.bar_spec_id),
        kind=str(value.kind),
        window_start_unix_nanos=int(value.window_start_unix_nanos),
        window_end_unix_nanos=int(value.window_end_unix_nanos),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=_decimal(value.volume),
        source_observed_at_unix_nanos=int(value.source_observed_at_unix_nanos),
        received_at_unix_nanos=int(value.received_at_unix_nanos),
    )


def _greeks(value: Any) -> MarketGreeksCurrent:
    return MarketGreeksCurrent(
        evidence=_evidence(value.evidence),
        scope=_scope(value.scope),
        instrument_id=str(value.instrument_id),
        provider=str(value.provider),
        expiry_unix_nanos=value.expiry_unix_nanos,
        strike=_decimal(value.strike),
        delta=_decimal(value.delta),
        gamma=_decimal(value.gamma),
        vega=_decimal(value.vega),
        theta=_decimal(value.theta),
        implied_volatility=_decimal(value.implied_volatility),
        source_observed_at_unix_nanos=int(value.source_observed_at_unix_nanos),
        received_at_unix_nanos=int(value.received_at_unix_nanos),
        derivation_id=value.derivation_id,
    )

def _common(value: Any) -> tuple[MarketCurrentEvidence, MarketObservationScope, str, str]:
    return _evidence(value.evidence), _scope(value.scope), str(value.instrument_id), str(value.provider)

def _required_decimal(value: Any) -> Decimal:
    result = _decimal(value)
    if result is None: raise ValueError("Market native contract omitted a required decimal")
    return result

def _rate(v: Any) -> MarketRateCurrent:
    e,s,i,p=_common(v); return MarketRateCurrent(e,str(v.rate_id),s,i,p,str(v.basis),_required_decimal(v.value),_decimal(v.mark_price),v.source_observed_at_unix_nanos,v.received_at_unix_nanos)
def _ticker(v: Any) -> MarketTicker24hCurrent:
    e, s, i, p = _common(v)
    return MarketTicker24hCurrent(
        e, s, i, p, _decimal(v.last_price), _decimal(v.bid_price),
        _decimal(v.bid_quantity), _decimal(v.ask_price), _decimal(v.ask_quantity),
        _decimal(v.open_price), _decimal(v.high_price), _decimal(v.low_price),
        _decimal(v.volume_base), _decimal(v.volume_quote), _decimal(v.price_change_abs),
        _decimal(v.price_change_pct), _decimal(v.vwap), _decimal(v.mark_price),
        v.source_observed_at_unix_nanos, v.received_at_unix_nanos,
    )
def _mark(v: Any) -> MarketMarkPriceCurrent:
    e,s,i,p=_common(v); return MarketMarkPriceCurrent(e,s,i,p,_required_decimal(v.mark_price),_decimal(v.index_price),_decimal(v.estimated_settlement_price),_decimal(v.funding_rate),v.next_funding_time_unix_nanos,v.source_observed_at_unix_nanos,v.received_at_unix_nanos)
def _funding(v: Any) -> MarketFundingRateCurrent:
    e,s,i,p=_common(v); return MarketFundingRateCurrent(e,s,i,p,_required_decimal(v.funding_rate),v.funding_period_seconds,v.next_funding_time_unix_nanos,v.source_observed_at_unix_nanos,v.received_at_unix_nanos)
def _open_interest(v: Any) -> MarketOpenInterestCurrent:
    e,s,i,p=_common(v); return MarketOpenInterestCurrent(e,s,i,p,_required_decimal(v.contracts),_decimal(v.quote_value),_decimal(v.change_24h),_decimal(v.change_pct_24h),v.source_observed_at_unix_nanos,v.received_at_unix_nanos)
def _index(v: Any) -> MarketIndexPriceCurrent:
    e,s,i,p=_common(v); return MarketIndexPriceCurrent(e,s,i,p,_decimal(v.spot_index_price),_decimal(v.contract_index_price),_decimal(v.index_price),_decimal(v.funding_rate),v.source_observed_at_unix_nanos,v.received_at_unix_nanos)
def _order_book(v: Any) -> MarketOrderBookCurrent:
    level=lambda x:MarketOrderBookLevel(_required_decimal(x.price),_required_decimal(x.quantity),x.order_count)
    return MarketOrderBookCurrent(_evidence(v.evidence),v.provider,v.market_id,v.instrument_id,v.sequence,v.source_observed_at_unix_nanos,v.received_at_unix_nanos,v.checksum,v.depth_policy,tuple(level(x) for x in v.bids),tuple(level(x) for x in v.asks))
def _freshness(v: Any) -> MarketFreshnessCurrent:
    return MarketFreshnessCurrent(_evidence(v.evidence),v.provider,_scope(v.scope),v.data_kind,v.last_event_time_unix_nanos,v.last_received_time_unix_nanos,v.age_nanos,v.event_sequence,v.status)


__all__ = [
    "MarketBarCurrent",
    "MarketCurrentEvidence",
    "MarketGreeksCurrent",
    "MarketFreshnessCurrent",
    "MarketFundingRateCurrent",
    "MarketIndexPriceCurrent",
    "MarketMarkPriceCurrent",
    "MarketOpenInterestCurrent",
    "MarketOrderBookCurrent",
    "MarketOrderBookLevel",
    "MarketRateCurrent",
    "MarketTicker24hCurrent",
    "MarketIndexedFrame",
    "MarketIndexedViewQueries",
    "MarketObservationScope",
    "MarketQuoteCurrent",
    "MarketViewKey",
    "MarketViewKind",
    "market_indexed_environment_path",
]
