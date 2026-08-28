"""Read-only Strategy view of the Reference catalog.

The runtime injects the composed Reference application through
``StrategyContext.reference``.  These protocols describe only the facts and
queries a strategy may read; they neither own DTOs nor construct owner types.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol, Sequence, overload

from kairospy.primitives.decimal import (
    MoneyLike,
    PriceLike,
    QuantityLike,
    RateLike,
)

from kairospy.primitives.reference import (
    ExchangeId,
    AssetId,
    InstrumentId,
    ListingId,
    MarketId,
)


class Exchange(Protocol):
    @property
    def id(self) -> ExchangeId: ...

    @property
    def name(self) -> str: ...

    @property
    def status(self) -> str: ...


class Asset(Protocol):
    @property
    def id(self) -> AssetId: ...

    @property
    def code(self) -> str: ...

    @property
    def name(self) -> str | None: ...

    @property
    def asset_class(self) -> str: ...

    @property
    def status(self) -> str: ...


class InstrumentRef(Protocol):
    @property
    def id(self) -> InstrumentId: ...

    @property
    def display_symbol(self) -> str: ...


class Instrument(Protocol):
    @property
    def id(self) -> InstrumentId: ...

    @property
    def symbol(self) -> str: ...

    @property
    def instrument_type(self) -> str: ...

    @property
    def name(self) -> str | None: ...

    @property
    def product_family(self) -> str | None: ...

    @property
    def issuer_id(self) -> str | None: ...

    @property
    def share_class(self) -> str | None: ...

    @property
    def primary_currency_asset_id(self) -> AssetId | None: ...

    @property
    def underlying_instrument_id(self) -> InstrumentId | None: ...

    @property
    def expiry_unix_nanos(self) -> int | None: ...

    @property
    def strike(self) -> PriceLike | None: ...

    @property
    def option_right(self) -> str | None: ...

    @property
    def status(self) -> str: ...

    @property
    def ref(self) -> InstrumentRef: ...


class Listing(Protocol):
    @property
    def id(self) -> ListingId: ...

    @property
    def instrument_id(self) -> InstrumentId: ...

    @property
    def exchange_id(self) -> ExchangeId: ...

    @property
    def exchange_symbol(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def effective_from_unix_nanos(self) -> int: ...

    @property
    def effective_to_unix_nanos(self) -> int | None: ...


class TradingRules(Protocol):
    @property
    def price_increment(self) -> PriceLike | None: ...

    @property
    def quantity_increment(self) -> QuantityLike | None: ...

    @property
    def minimum_quantity(self) -> QuantityLike | None: ...

    @property
    def minimum_notional(self) -> MoneyLike | None: ...

    @property
    def contract_multiplier(self) -> RateLike | None: ...


class Market(Protocol):
    @property
    def id(self) -> MarketId: ...

    @property
    def instrument(self) -> InstrumentRef: ...

    @property
    def listing_id(self) -> ListingId | None: ...

    @property
    def exchange_id(self) -> ExchangeId: ...

    @property
    def instrument_kind(self) -> str: ...

    @property
    def venue_symbol(self) -> str | None: ...

    @property
    def base_asset(self) -> AssetId | None: ...

    @property
    def quote_asset(self) -> AssetId | None: ...

    @property
    def status(self) -> str: ...

    @property
    def trading_rules(self) -> TradingRules: ...


class Reference(Protocol):
    """Minimal read-only Reference capability exposed to strategies."""

    @property
    def generation(self) -> int | None: ...

    @property
    def event_sequence(self) -> int | None: ...

    def snapshot(self) -> AbstractContextManager[Reference]: ...

    def find_exchanges(
        self,
        *,
        exchange_ids: Sequence[str] | None = None,
        query: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Exchange, ...]: ...

    def find_assets(
        self,
        *,
        asset_ids: Sequence[str] | None = None,
        query: str | None = None,
        code: str | None = None,
        asset_class: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Asset, ...]: ...

    def find_instruments(
        self,
        *,
        instrument_ids: Sequence[InstrumentId | str] | None = None,
        query: str | None = None,
        symbol: str | None = None,
        instrument_type: str | None = None,
        product_family: str | None = None,
        underlying_instrument_id: InstrumentId | str | None = None,
        expiry_unix_nanos: int | None = None,
        expiry_from_unix_nanos: int | None = None,
        expiry_to_unix_nanos: int | None = None,
        option_right: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Instrument, ...]: ...

    def instrument(self, instrument_id: InstrumentId | str) -> Instrument | None: ...

    def require_instrument(self, instrument_id: InstrumentId | str) -> Instrument: ...

    def option_chain(
        self,
        underlying_instrument_id: InstrumentId | str,
        *,
        expiry_unix_nanos: int | None = None,
        expiry_from_unix_nanos: int | None = None,
        expiry_to_unix_nanos: int | None = None,
        option_right: str | None = None,
        active_only: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Instrument, ...]: ...

    def find_listings(
        self,
        *,
        listing_ids: Sequence[ListingId | str] | None = None,
        instrument_id: InstrumentId | str | None = None,
        exchange_id: ExchangeId | str | None = None,
        query: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Listing, ...]: ...

    def find_markets(
        self,
        *,
        market_ids: Sequence[MarketId | str] | None = None,
        query: str | None = None,
        symbol: str | None = None,
        asset_code: str | None = None,
        exchange: ExchangeId | str | None = None,
        instrument_kind: str | None = None,
        asset_type: str | None = None,
        instrument_id: InstrumentId | str | None = None,
        listing_id: ListingId | str | None = None,
        underlying_instrument_id: InstrumentId | str | None = None,
        active_only: bool = True,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Market, ...]: ...

    @overload
    def require_market(self, market_id: MarketId, /) -> Market: ...

    @overload
    def require_market(
        self,
        *,
        symbol: str,
        exchange: str,
        instrument_kind: str,
    ) -> Market: ...

    def market(self, market_id: MarketId) -> Market | None: ...


__all__ = [
    "Asset",
    "Exchange",
    "Instrument",
    "InstrumentRef",
    "Listing",
    "Market",
    "TradingRules",
]
