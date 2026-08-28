from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar, overload

from kairospy.contracts.reference.events import ReferenceEventVariant
from kairospy.infrastructure.protocol import LiveEventSource
from kairospy.contracts.reference import (
    ReferenceAsset,
    ReferenceExchange,
    ReferenceInstrument,
    ReferenceListing,
    ReferenceMarket,
)
from kairospy.primitives.reference import (
    ExchangeId,
    InstrumentId,
    ListingId,
    MarketId,
)


class ReferenceNotFoundError(LookupError):
    pass


class AmbiguousReferenceError(LookupError):
    pass


_T = TypeVar("_T")


async def observe_reference_stream(
    source: LiveEventSource[ReferenceEventVariant],
    *,
    timeout_seconds: float,
    idle_timeout_seconds: float,
) -> dict[str, Any]:
    """Observe a bounded live Reference stream for diagnostics."""

    if timeout_seconds <= 0 or idle_timeout_seconds <= 0:
        raise ValueError("Reference stream timeouts must be positive")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    idle_deadline = loop.time() + idle_timeout_seconds
    event_count = 0
    batch_count = 0
    generation = 0
    event_sequence = 0
    first_event_id: str | None = None
    last_event_id: str | None = None

    def observe(event: ReferenceEventVariant) -> None:
        nonlocal event_count, generation, event_sequence, first_event_id, last_event_id
        event_id = str(event.metadata.event_id)
        if first_event_id is None:
            first_event_id = event_id
        last_event_id = event_id
        generation = int(event.catalog_revision)
        event_sequence = int(event.metadata.sequence)
        event_count += 1

    try:
        while loop.time() < deadline:
            count = source.poll_visit(observe)
            if count:
                batch_count += 1
                idle_deadline = loop.time() + idle_timeout_seconds
                await asyncio.sleep(0)
                continue
            if event_count and loop.time() >= idle_deadline:
                break
            await asyncio.sleep(0.001)
    finally:
        source.close()
    if event_count == 0:
        raise RuntimeError("Reference stream produced no events before timeout")
    return {
        "status": "received",
        "batches": batch_count,
        "events": event_count,
        "generation": generation,
        "event_sequence": event_sequence,
        "first_event_id": first_event_id,
        "last_event_id": last_event_id,
    }


class ReferenceApplication:
    """Read-only, typed facade over Reference's current SQLite catalog."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        live_source: LiveEventSource[ReferenceEventVariant] | None = None,
        launch_id: str | None = None,
        instance_id: str | None = None,
        generation: int | None = None,
        event_sequence: int | None = None,
    ) -> None:
        self._client = client
        self._live_source = live_source
        self._launch_id = launch_id
        self._instance_id = instance_id
        self._generation = generation
        self._event_sequence = event_sequence
        self._live_cursor = 0
        self._live_cursor_key: tuple[str, str, int] | None = None
        self._notification_gap_count = 0
        self._notification_incarnation_change_count = 0

    def visit_live(
        self,
        visitor: Callable[[ReferenceEventVariant], None],
        *,
        fragment_limit: int = 64,
    ) -> int:
        """Poll Reference once and consume callback-scoped events synchronously."""

        if self._live_source is None:
            return 0
        cursor = self._live_cursor

        def accept(event: ReferenceEventVariant) -> None:
            nonlocal cursor
            metadata = event.metadata
            if metadata.stream_id != "reference.events":
                raise RuntimeError(
                    f"Reference event stream identity is invalid: {metadata.stream_id}"
                )
            if self._launch_id is not None and metadata.launch_id != self._launch_id:
                raise RuntimeError("Reference event belongs to another launch")
            if self._instance_id is not None and metadata.instance_id != self._instance_id:
                raise RuntimeError("Reference event belongs to another launch instance")
            cursor_key = (
                metadata.stream_id,
                str(metadata.producer),
                int(metadata.producer_incarnation),
            )
            sequence = int(metadata.sequence)
            if self._live_cursor_key is not None and cursor_key != self._live_cursor_key:
                self._notification_incarnation_change_count += 1
                cursor = sequence - 1
            elif self._live_cursor_key is None:
                cursor = sequence - 1
            self._live_cursor_key = cursor_key
            if sequence <= cursor:
                return
            if sequence != cursor + 1:
                self._notification_gap_count += 1
            cursor = sequence
            self._live_cursor = sequence
            visitor(event)

        return self._live_source.poll_visit(accept, fragment_limit=fragment_limit)

    def notification_health(self) -> dict[str, int]:
        return {
            "cursor": self._live_cursor,
            "gap_count": self._notification_gap_count,
            "incarnation_change_count": self._notification_incarnation_change_count,
        }

    def close_live(self) -> None:
        if self._live_source is not None:
            self._live_source.close()

    @classmethod
    def from_database(cls, database_path: str | Path) -> "ReferenceApplication":
        """Open the owner Contract reader without exposing it to UI callers."""

        from kairospy.contracts.reference import ReferenceClient

        return cls(ReferenceClient(database_path=Path(database_path)))

    @classmethod
    def from_process(
        cls,
        *,
        socket_path: str | Path,
        database_path: str | Path,
        timeout: float = 30.0,
    ) -> "ReferenceApplication":
        from kairospy.contracts.reference import ReferenceClient

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
    ) -> tuple[ReferenceExchange, ...]:
        rows = self._require_client().exchanges(
            exchange_ids=exchange_ids,
            query=query,
            status=status,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
        return tuple(rows)

    def exchange(self, exchange_id: str) -> ReferenceExchange | None:
        return _optional_one(
            self.find_exchanges(exchange_ids=(exchange_id,), limit=2), "exchange", exchange_id
        )

    def require_exchange(self, exchange_id: str) -> ReferenceExchange:
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
    ) -> tuple[ReferenceAsset, ...]:
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
        return tuple(rows)

    def asset(self, asset_id: str) -> ReferenceAsset | None:
        return _optional_one(
            self.find_assets(asset_ids=(asset_id,), limit=2), "asset", asset_id
        )

    def require_asset(self, asset_id: str) -> ReferenceAsset:
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
    ) -> tuple[ReferenceInstrument, ...]:
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
        return tuple(rows)

    def instrument(
        self, instrument_id: InstrumentId | str
    ) -> ReferenceInstrument | None:
        return _optional_one(
            self.find_instruments(instrument_ids=(instrument_id,), limit=2),
            "instrument",
            str(instrument_id),
        )

    def require_instrument(
        self, instrument_id: InstrumentId | str
    ) -> ReferenceInstrument:
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
    ) -> tuple[ReferenceInstrument, ...]:
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
    ) -> tuple[ReferenceListing, ...]:
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
        return tuple(rows)

    def listing(self, listing_id: ListingId | str) -> ReferenceListing | None:
        return _optional_one(
            self.find_listings(listing_ids=(listing_id,), limit=2),
            "listing",
            str(listing_id),
        )

    def require_listing(self, listing_id: ListingId | str) -> ReferenceListing:
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
    ) -> tuple[ReferenceMarket, ...]:
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
        return tuple(rows)

    @overload
    def require_market(self, market_id: MarketId, /) -> ReferenceMarket: ...

    @overload
    def require_market(
        self,
        *,
        symbol: str,
        exchange: str,
        instrument_kind: str,
    ) -> ReferenceMarket: ...

    def require_market(
        self,
        market_id: MarketId | None = None,
        *,
        symbol: str | None = None,
        exchange: str | None = None,
        instrument_kind: str | None = None,
    ) -> ReferenceMarket:
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

    def market(self, market_id: MarketId) -> ReferenceMarket | None:
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
