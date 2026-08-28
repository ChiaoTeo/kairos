from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeVar, overload

from kairospy.primitives.decimal import (
    Money,
    MoneyLike,
    Price,
    PriceLike,
    Quantity,
    QuantityLike,
    Rate,
    RateLike,
)
from kairospy.primitives.reference import (
    AssetId,
    ExchangeId,
    InstrumentId,
    ListingId,
    MarketId,
)

from .models import (
    Asset,
    Exchange,
    Instrument,
    InstrumentRef,
    Listing,
    Market,
    MarketStatus,
    ReferenceStatus,
    TradingRules,
)


class ReferenceNotFoundError(LookupError):
    pass


class AmbiguousReferenceError(LookupError):
    pass


_T = TypeVar("_T")


async def observe_reference_stream(
    source: Any,
    *,
    timeout_seconds: float,
    idle_timeout_seconds: float,
) -> dict[str, Any]:
    """Observe a bounded live Reference stream for diagnostics."""

    if timeout_seconds <= 0 or idle_timeout_seconds <= 0:
        raise ValueError("Reference stream timeouts must be positive")
    events: list[Any] = []
    stream = source.subscribe_live().__aiter__()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                event = await asyncio.wait_for(
                    anext(stream),
                    timeout=min(remaining, idle_timeout_seconds),
                )
            except StopAsyncIteration:
                break
            except TimeoutError:
                if events:
                    break
                if loop.time() >= deadline:
                    break
                continue
            events.append(event)
    finally:
        await source.close()
    if not events:
        raise RuntimeError("Reference stream produced no events before timeout")
    first = events[0]
    last = events[-1]
    return {
        "status": "received",
        "batches": len(events),
        "events": len(events),
        "generation": int(last.catalog_revision),
        "event_sequence": int(last.sequence),
        "first_event_id": str(first.event_id),
        "last_event_id": str(last.event_id),
    }


class ReferenceApplication:
    """Read-only, typed facade over Reference's current SQLite catalog."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        generation: int | None = None,
        event_sequence: int | None = None,
    ) -> None:
        self._client = client
        self._generation = generation
        self._event_sequence = event_sequence

    @classmethod
    def from_database(cls, database_path: str | Path) -> "ReferenceApplication":
        """Open the owner Contract reader without exposing it to UI callers."""

        from kairospy.infrastructure.contracts.reference import ReferenceClient

        return cls(ReferenceClient(database_path=Path(database_path)))

    @classmethod
    def from_process(
        cls,
        *,
        socket_path: str | Path,
        database_path: str | Path,
        timeout: float = 30.0,
    ) -> "ReferenceApplication":
        from kairospy.infrastructure.contracts.reference import ReferenceClient

        return cls(
            ReferenceClient(
                socket_path=Path(socket_path),
                database_path=Path(database_path),
                timeout=timeout,
            )
        )

    @property
    def generation(self) -> int | None:
        """Pinned generation inside ``snapshot()``; otherwise unavailable."""

        return self._generation

    @property
    def event_sequence(self) -> int | None:
        """Pinned event watermark inside ``snapshot()``; otherwise unavailable."""

        return self._event_sequence

    def health(self) -> dict[str, Any]:
        return dict(self._require_client().health())

    def providers(self) -> dict[str, Any]:
        return dict(self._require_client().providers())

    def catalog(self) -> dict[str, Any]:
        return dict(self._require_client().catalog())

    def refresh(self, *, source: str | None = None) -> dict[str, Any]:
        return dict(self._require_client().refresh(source=source))

    def set_source_paused(self, source: str, paused: bool) -> dict[str, Any]:
        return dict(self._require_client().set_source_paused(source, paused))

    def option_coverage(self) -> dict[str, Any]:
        return dict(self._require_client().option_coverage())

    def set_option_underlying(
        self, underlying: str, enabled: bool
    ) -> dict[str, Any]:
        return dict(self._require_client().set_option_underlying(underlying, enabled))

    @contextmanager
    def snapshot(self) -> Iterator[ReferenceApplication]:
        """Pin a group of strategy reads to one committed catalog generation."""

        client = self._require_client()
        snapshot_factory = getattr(client, "snapshot", None)
        if snapshot_factory is None:
            raise RuntimeError("Reference client does not support snapshot reads")
        with snapshot_factory() as query:
            yield ReferenceApplication(
                query,
                generation=int(query.generation),
                event_sequence=int(query.event_sequence),
            )

    def find_exchanges(
        self,
        *,
        exchange_ids: Sequence[str] | None = None,
        query: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Exchange, ...]:
        rows = self._require_client().exchanges(
            exchange_ids=exchange_ids,
            query=query,
            status=status,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
        return tuple(_exchange_from_row(row) for row in rows)

    def exchange(self, exchange_id: str) -> Exchange | None:
        return _optional_one(
            self.find_exchanges(exchange_ids=(exchange_id,), limit=2), "exchange", exchange_id
        )

    def require_exchange(self, exchange_id: str) -> Exchange:
        return _require_one(self.exchange(exchange_id), "exchange", exchange_id)

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
    ) -> tuple[Asset, ...]:
        rows = self._require_client().assets(
            asset_ids=asset_ids,
            query=query,
            code=code,
            asset_class=asset_class,
            status=status,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
        return tuple(_asset_from_row(row) for row in rows)

    def asset(self, asset_id: str) -> Asset | None:
        return _optional_one(
            self.find_assets(asset_ids=(asset_id,), limit=2), "asset", asset_id
        )

    def require_asset(self, asset_id: str) -> Asset:
        return _require_one(self.asset(asset_id), "asset", asset_id)

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
    ) -> tuple[Instrument, ...]:
        rows = self._require_client().instruments(
            instrument_ids=_strings(instrument_ids),
            query=query,
            symbol=symbol,
            instrument_type=instrument_type,
            product_family=product_family,
            underlying_instrument_id=_string(underlying_instrument_id),
            expiry_unix_nanos=expiry_unix_nanos,
            expiry_from_unix_nanos=expiry_from_unix_nanos,
            expiry_to_unix_nanos=expiry_to_unix_nanos,
            option_right=option_right,
            status=status,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
        return tuple(_instrument_from_row(row) for row in rows)

    def instrument(self, instrument_id: InstrumentId | str) -> Instrument | None:
        return _optional_one(
            self.find_instruments(instrument_ids=(instrument_id,), limit=2),
            "instrument",
            str(instrument_id),
        )

    def require_instrument(self, instrument_id: InstrumentId | str) -> Instrument:
        return _require_one(
            self.instrument(instrument_id), "instrument", str(instrument_id)
        )

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
    ) -> tuple[Instrument, ...]:
        return self.find_instruments(
            instrument_type="option",
            underlying_instrument_id=underlying_instrument_id,
            expiry_unix_nanos=expiry_unix_nanos,
            expiry_from_unix_nanos=expiry_from_unix_nanos,
            expiry_to_unix_nanos=expiry_to_unix_nanos,
            option_right=option_right,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )

    def find_listings(
        self,
        *,
        listing_ids: Sequence[ListingId | str] | None = None,
        query: str | None = None,
        instrument_id: InstrumentId | str | None = None,
        exchange: ExchangeId | str | None = None,
        exchange_symbol: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Listing, ...]:
        rows = self._require_client().listings(
            listing_ids=_strings(listing_ids),
            query=query,
            instrument_id=_string(instrument_id),
            exchange_id=_string(exchange),
            exchange_symbol=exchange_symbol,
            status=status,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
        return tuple(_listing_from_row(row) for row in rows)

    def listing(self, listing_id: ListingId | str) -> Listing | None:
        return _optional_one(
            self.find_listings(listing_ids=(listing_id,), limit=2),
            "listing",
            str(listing_id),
        )

    def require_listing(self, listing_id: ListingId | str) -> Listing:
        return _require_one(self.listing(listing_id), "listing", str(listing_id))

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
    ) -> tuple[Market, ...]:
        rows = self._require_client().markets(
            market_ids=_strings(market_ids),
            query=query,
            symbol=symbol,
            asset_code=asset_code,
            exchange_id=_string(exchange),
            instrument_kind=instrument_kind,
            asset_type=asset_type,
            instrument_id=_string(instrument_id),
            listing_id=_string(listing_id),
            underlying_instrument_id=_string(underlying_instrument_id),
            active_only=active_only,
            status=status,
            limit=limit,
            offset=offset,
        )
        return tuple(_market_from_row(row) for row in rows)

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

    def require_market(
        self,
        market_id: MarketId | None = None,
        *,
        symbol: str | None = None,
        exchange: str | None = None,
        instrument_kind: str | None = None,
    ) -> Market:
        matches = self.find_markets(
            market_ids=None if market_id is None else (market_id,),
            symbol=symbol,
            exchange=exchange,
            instrument_kind=instrument_kind,
            active_only=False if market_id is not None else True,
            limit=2,
        )
        filters = {
            "market_id": None if market_id is None else str(market_id),
            "symbol": symbol,
            "exchange": exchange,
            "instrument_kind": instrument_kind,
        }
        if not matches:
            raise ReferenceNotFoundError(f"Reference market not found: {filters}")
        if len(matches) != 1:
            raise AmbiguousReferenceError(
                f"Reference market is ambiguous ({len(matches)} matches): {filters}"
            )
        return matches[0]

    def market(self, market_id: MarketId) -> Market | None:
        try:
            return self.require_market(market_id)
        except ReferenceNotFoundError:
            return None

    def _require_client(self) -> Any:
        if self._client is None:
            raise RuntimeError("Reference application is unavailable")
        return self._client


def _optional_one(values: Sequence[_T], kind: str, identity: str) -> _T | None:
    if len(values) > 1:
        raise AmbiguousReferenceError(
            f"Reference {kind} is ambiguous ({len(values)} matches): {identity}"
        )
    return values[0] if values else None


def _require_one(value: _T | None, kind: str, identity: str) -> _T:
    if value is None:
        raise ReferenceNotFoundError(f"Reference {kind} not found: {identity}")
    return value


def _strings(values: Sequence[object] | None) -> tuple[str, ...] | None:
    return None if values is None else tuple(str(value) for value in values)


def _string(value: object | None) -> str | None:
    return None if value is None else str(value)


def _value(row: Mapping[str, object], *names: str) -> object | None:
    for name in names:
        value = row.get(name)
        if value is not None:
            return value
    return None


def _required(row: Mapping[str, object], *names: str) -> str:
    value = _value(row, *names)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Reference record is missing {names[0]}")
    return value


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (str, int)):
        return int(value)
    raise ValueError(f"Reference integer field has invalid value: {value!r}")


def _optional_price(value: object) -> PriceLike | None:
    if value is None or isinstance(value, PriceLike):
        return value
    if isinstance(value, (Decimal, str, int)) and not isinstance(value, bool):
        return Price(value)
    raise ValueError("Reference price is invalid")


def _optional_quantity(value: object) -> QuantityLike | None:
    if value is None or isinstance(value, QuantityLike):
        return value
    if isinstance(value, (Decimal, str, int)) and not isinstance(value, bool):
        return Quantity(value)
    raise ValueError("Reference quantity is invalid")


def _optional_money(value: object) -> MoneyLike | None:
    if value is None or isinstance(value, MoneyLike):
        return value
    if isinstance(value, (Decimal, str, int)) and not isinstance(value, bool):
        return Money(value)
    raise ValueError("Reference money is invalid")


def _optional_rate(value: object) -> RateLike | None:
    if value is None or isinstance(value, RateLike):
        return value
    if isinstance(value, (Decimal, str, int)) and not isinstance(value, bool):
        return Rate(value)
    raise ValueError("Reference rate is invalid")


def _status(row: Mapping[str, object]) -> ReferenceStatus:
    raw = str(row.get("status", "unknown")).lower()
    return (
        ReferenceStatus(raw)
        if raw in ReferenceStatus._value2member_map_
        else ReferenceStatus.UNKNOWN
    )


def _exchange_from_row(row: Mapping[str, object]) -> Exchange:
    return Exchange(
        id=ExchangeId(_required(row, "exchangeId", "exchange_id")),
        name=_required(row, "name"),
        status=_status(row),
    )


def _asset_from_row(row: Mapping[str, object]) -> Asset:
    return Asset(
        id=AssetId(_required(row, "assetId", "asset_id")),
        code=_required(row, "code"),
        name=_optional_text(row.get("name")),
        asset_class=_required(row, "assetClass", "asset_class"),
        status=_status(row),
    )


def _instrument_from_row(row: Mapping[str, object]) -> Instrument:
    underlying = _optional_text(
        _value(row, "underlyingInstrumentId", "underlying_instrument_id")
    )
    return Instrument(
        id=InstrumentId(_required(row, "instrumentId", "instrument_id")),
        symbol=_required(row, "symbol"),
        name=_optional_text(row.get("name")),
        instrument_type=_required(row, "instrumentType", "instrument_type"),
        product_family=_optional_text(_value(row, "productFamily", "product_family")),
        issuer_id=_optional_text(_value(row, "issuerId", "issuer_id")),
        share_class=_optional_text(_value(row, "shareClass", "share_class")),
        primary_currency_asset_id=(
            AssetId(primary_currency)
            if (
                primary_currency := _optional_text(
                    _value(
                        row,
                        "primaryCurrencyAssetId",
                        "primary_currency_asset_id",
                    )
                )
            )
            is not None
            else None
        ),
        underlying_instrument_id=(
            InstrumentId(underlying) if underlying is not None else None
        ),
        expiry_unix_nanos=_optional_int(
            _value(row, "expiryUnixNanos", "expiry_unix_nanos")
        ),
        strike=_optional_price(row.get("strike")),
        option_right=_optional_text(_value(row, "optionRight", "option_right")),
        status=_status(row),
    )


def _listing_from_row(row: Mapping[str, object]) -> Listing:
    return Listing(
        id=ListingId(_required(row, "listingId", "listing_id")),
        instrument_id=InstrumentId(_required(row, "instrumentId", "instrument_id")),
        exchange_id=ExchangeId(_required(row, "exchangeId", "exchange_id")),
        exchange_symbol=_required(row, "exchangeSymbol", "exchange_symbol"),
        status=_status(row),
        effective_from_unix_nanos=(
            _optional_int(
                _value(row, "effectiveFromUnixNanos", "effective_from_unix_nanos")
            )
            or 0
        ),
        effective_to_unix_nanos=_optional_int(
            _value(row, "effectiveToUnixNanos", "effective_to_unix_nanos")
        ),
    )


def _market_from_row(row: Mapping[str, object]) -> Market:
    instrument_id = _required(row, "instrument_id", "instrumentId")
    venue_symbol = _optional_text(_value(row, "venue_symbol", "venueSymbol"))
    raw_status = str(row.get("status", "unknown")).lower()
    status = (
        MarketStatus(raw_status)
        if raw_status in MarketStatus._value2member_map_
        else MarketStatus.UNKNOWN
    )
    listing_id = _optional_text(_value(row, "listing_id", "listingId"))
    return Market(
        id=MarketId(_required(row, "market_id", "marketId")),
        instrument=InstrumentRef(InstrumentId(instrument_id), instrument_id),
        listing_id=ListingId(listing_id) if listing_id is not None else None,
        exchange_id=ExchangeId(_required(row, "exchange_id", "exchangeId")),
        instrument_kind=_required(row, "instrument_kind", "instrumentKind"),
        venue_symbol=venue_symbol,
        base_asset=(
            AssetId(base_asset)
            if (base_asset := _optional_text(_value(row, "base_asset", "base_asset_id")))
            is not None
            else None
        ),
        quote_asset=(
            AssetId(quote_asset)
            if (quote_asset := _optional_text(_value(row, "quote_asset", "quote_asset_id")))
            is not None
            else None
        ),
        status=status,
        trading_rules=TradingRules(
            price_increment=_optional_price(
                _value(row, "price_increment", "tick_size")
            ),
            quantity_increment=_optional_quantity(
                _value(row, "quantity_increment", "step_size")
            ),
            minimum_quantity=_optional_quantity(
                _value(row, "minimum_quantity", "min_quantity")
            ),
            minimum_notional=_optional_money(
                _value(row, "minimum_notional", "min_notional")
            ),
            contract_multiplier=_optional_rate(
                _value(row, "contract_multiplier")
            ),
        ),
    )
