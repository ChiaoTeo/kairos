"""Reference control and owner-contract catalog reads."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

from kairospy.primitives.reference import AssetClass, InstrumentKind, ReferenceStatus
from kairospy.primitives.reference import ReferenceSourceIdRead
from .results import (
    ReferenceCatalogCounts,
    ReferenceCatalogIntegrity,
    ReferenceCatalogSnapshot,
    ReferenceHealthResponse,
    ReferenceOptionCoverage,
    ReferenceRuntimeStatusResponse,
)

if TYPE_CHECKING:
    from kairospy._native_reference_contract import (
        ReferenceAsset,
        ReferenceExchange,
        ReferenceInstrument,
        ReferenceInstrumentAvailability,
        ReferenceListing,
        ReferenceMarket,
        ReferenceReadSession as NativeReferenceReadSession,
    )

    ReferenceRecord: TypeAlias = (
        ReferenceExchange
        | ReferenceAsset
        | ReferenceInstrument
        | ReferenceListing
        | ReferenceMarket
    )


def _native_module() -> ModuleType:
    native = import_module("kairospy._native_reference_contract")
    info = native.build_info()
    if info.api_version != 1 or info.owner != "Reference":
        raise RuntimeError("incompatible Reference native contract binding")
    return native


@dataclass(frozen=True, slots=True)
class ReferenceReadSession:
    """One generation-pinned view whose SQLite transaction stays in Rust."""

    _native: NativeReferenceReadSession
    generation: int
    event_sequence: int

    def close(self) -> None:
        self._native.close()

    def catalog(self) -> ReferenceCatalogSnapshot:
        status = self._native.status()
        return ReferenceCatalogSnapshot(
            generation=status.generation,
            event_sequence=status.event_sequence,
            catalog=ReferenceCatalogCounts(
                exchange_count=status.exchange_count,
                asset_count=status.asset_count,
                instrument_count=status.instrument_count,
                listing_count=status.listing_count,
                market_count=status.market_count,
                active_market_count=status.active_market_count,
            ),
            integrity=ReferenceCatalogIntegrity(
                missing_equity_markets=status.missing_equity_markets,
                legacy_exchange_market_ids=status.legacy_exchange_market_ids,
                legacy_exchange_listing_ids=status.legacy_exchange_listing_ids,
                option_listings=status.option_listings,
                option_markets=status.option_markets,
            ),
        )

    def option_coverage(self) -> ReferenceOptionCoverage:
        return ReferenceOptionCoverage(
            source_id=ReferenceSourceIdRead("massive-options"),
            generation=self._native.generation,
            event_sequence=self._native.event_sequence,
            underlyings=tuple(self._native.option_coverage()),
        )

    def outbox_depth(self) -> int:
        return int(self._native.outbox_depth())

    def exchanges(
        self,
        *,
        exchange_ids: Sequence[str] | None = None,
        query: str | None = None,
        status: ReferenceStatus | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ReferenceExchange]:
        return self._native.exchanges(
            exchange_ids=_identifiers(exchange_ids),
            query=query,
            status=status,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )

    def assets(
        self,
        *,
        asset_ids: Sequence[str] | None = None,
        query: str | None = None,
        code: str | None = None,
        asset_class: AssetClass | None = None,
        status: ReferenceStatus | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ReferenceAsset]:
        return self._native.assets(
            asset_ids=_identifiers(asset_ids),
            query=query,
            code=code,
            asset_class=asset_class,
            status=status,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )

    def instruments(
        self,
        *,
        instrument_ids: Sequence[str] | None = None,
        query: str | None = None,
        symbol: str | None = None,
        instrument_type: InstrumentKind | None = None,
        product_family: str | None = None,
        underlying_instrument_id: str | None = None,
        expiry_unix_nanos: int | None = None,
        expiry_from_unix_nanos: int | None = None,
        expiry_to_unix_nanos: int | None = None,
        option_right: Literal["call", "put"] | None = None,
        status: ReferenceStatus | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ReferenceInstrument]:
        return self._native.instruments(
            instrument_ids=_identifiers(instrument_ids),
            query=query,
            symbol=symbol,
            instrument_type=instrument_type,
            product_family=product_family,
            underlying_instrument_id=underlying_instrument_id,
            expiry_unix_nanos=expiry_unix_nanos,
            expiry_from_unix_nanos=expiry_from_unix_nanos,
            expiry_to_unix_nanos=expiry_to_unix_nanos,
            option_right=option_right,
            status=status,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )

    def instrument_availability(
        self,
        *,
        source_ids: Sequence[str] | None = None,
        instrument_ids: Sequence[str] | None = None,
        query: str | None = None,
        symbol: str | None = None,
        instrument_type: InstrumentKind | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ReferenceInstrumentAvailability]:
        return self._native.instrument_availability(
            source_ids=_identifiers(source_ids),
            instrument_ids=_identifiers(instrument_ids),
            query=query,
            symbol=symbol,
            instrument_type=instrument_type,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )

    def listings(
        self,
        *,
        listing_ids: Sequence[str] | None = None,
        query: str | None = None,
        instrument_id: str | None = None,
        exchange_id: str | None = None,
        exchange_symbol: str | None = None,
        status: ReferenceStatus | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ReferenceListing]:
        return self._native.listings(
            listing_ids=_identifiers(listing_ids),
            query=query,
            instrument_id=instrument_id,
            exchange_id=exchange_id,
            exchange_symbol=exchange_symbol,
            status=status,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )

    def markets(
        self,
        *,
        market_ids: Sequence[str] | None = None,
        query: str | None = None,
        symbol: str | None = None,
        asset_code: str | None = None,
        exchange_id: str | None = None,
        instrument_kind: InstrumentKind | None = None,
        asset_type: AssetClass | None = None,
        instrument_id: str | None = None,
        listing_id: str | None = None,
        underlying_instrument_id: str | None = None,
        active_only: bool = False,
        status: ReferenceStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ReferenceMarket]:
        return self._native.markets(
            market_ids=_identifiers(market_ids),
            query=query,
            symbol=symbol,
            asset_code=asset_code,
            exchange_id=exchange_id,
            instrument_kind=instrument_kind,
            asset_type=asset_type,
            instrument_id=instrument_id,
            listing_id=listing_id,
            underlying_instrument_id=underlying_instrument_id,
            active_only=active_only,
            status=status,
            limit=limit,
            offset=offset,
        )

    def collection(
        self,
        name: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ReferenceRecord]:
        readers = {
            "exchanges": self.exchanges,
            "assets": self.assets,
            "instruments": self.instruments,
            "listings": self.listings,
        }
        try:
            reader = readers[name]
        except KeyError as error:
            raise ValueError(f"unsupported Reference collection: {name}") from error
        return reader(limit=limit, offset=offset)


@dataclass(frozen=True, slots=True)
class ReferenceClient:
    """Reference facade with Rust-owned catalog persistence details."""

    socket_path: Path | None = None
    database_path: Path | None = None
    timeout: float = 5.0

    def _control(self):
        if self.socket_path is None:
            raise RuntimeError("Reference control socket is not configured")
        from .control import ReferenceControlClient

        return ReferenceControlClient(self.socket_path, timeout=self.timeout)

    @contextmanager
    def snapshot(self) -> Iterator[ReferenceReadSession]:
        """Pin all enclosed reads to one committed Reference generation."""

        if self.database_path is None:
            raise RuntimeError("Reference database is not configured")
        native = _native_module().ReferenceCatalog(self.database_path).snapshot()
        session = ReferenceReadSession(
            native,
            generation=int(native.generation),
            event_sequence=int(native.event_sequence),
        )
        try:
            yield session
        finally:
            session.close()

    def request(
        self,
        method: str,
        *,
        timeout: float | None = None,
        params: list[object] | None = None,
    ) -> dict[str, Any]:
        try:
            control = self._control()
            if timeout is not None and timeout != self.timeout:
                from .control import ReferenceControlClient

                socket_path = self.socket_path
                if socket_path is None:
                    raise RuntimeError("Reference control socket is not configured")
                control = ReferenceControlClient(socket_path, timeout=timeout)
            return dict(control.call(method, params))
        except OSError as error:
            raise RuntimeError(f"Reference request failed: {error}") from error

    def health(self) -> ReferenceHealthResponse:
        return ReferenceHealthResponse.from_mapping(self.request("reference_health"))

    def runtime_status(self) -> ReferenceRuntimeStatusResponse:
        """Read source, catalog, and publication status from Reference."""

        return ReferenceRuntimeStatusResponse.from_mapping(
            self.request("reference_status")
        )

    def plan_catalog_setup(self, goal: Mapping[str, object]) -> dict[str, Any]:
        """Ask Reference how a requested exchange/product catalog can be prepared."""

        return self.request(
            "reference_plan_catalog_setup",
            params=[{"goal": dict(goal)}],
        )

    def upsert_source_definition(
        self, definition: Mapping[str, object]
    ) -> dict[str, Any]:
        """Persist one source definition selected from a catalog setup plan."""

        return self.request(
            "reference_upsert_source_definition",
            params=[dict(definition)],
        )

    def providers(self) -> dict[str, Any]:
        health = self.health()
        provider_rows = [provider.to_json_dict() for provider in health.providers]
        if self.database_path is None:
            return {
                "generation": 0,
                "event_sequence": 0,
                "outbox_depth": 0,
                "providers": provider_rows,
            }
        with self.snapshot() as snapshot:
            return {
                "generation": snapshot.generation,
                "event_sequence": snapshot.event_sequence,
                "outbox_depth": snapshot.outbox_depth(),
                "providers": provider_rows,
            }

    def refresh(self, *, source: str | None = None) -> dict[str, Any]:
        return self.request(
            "reference_refresh", timeout=max(self.timeout, 120.0), params=[source]
        )

    def set_source_paused(self, source: str, paused: bool) -> dict[str, Any]:
        if not source.strip():
            raise ValueError("source is required")
        return self.request(
            "reference_pause_source" if paused else "reference_resume_source",
            params=[source],
        )

    def set_option_underlying(self, underlying: str, enabled: bool) -> dict[str, Any]:
        if not underlying.strip():
            raise ValueError("underlying is required")
        return self.request(
            (
                "reference_add_option_coverage"
                if enabled
                else "reference_remove_option_coverage"
            ),
            timeout=max(self.timeout, 120.0),
            params=[underlying],
        )

    def catalog(self) -> ReferenceCatalogSnapshot:
        with self.snapshot() as snapshot:
            return snapshot.catalog()

    def option_coverage(self) -> ReferenceOptionCoverage:
        with self.snapshot() as snapshot:
            return snapshot.option_coverage()

    def exchanges(self, **filters: Any) -> list[ReferenceExchange]:
        with self.snapshot() as snapshot:
            return snapshot.exchanges(**filters)

    def assets(self, **filters: Any) -> list[ReferenceAsset]:
        with self.snapshot() as snapshot:
            return snapshot.assets(**filters)

    def instruments(self, **filters: Any) -> list[ReferenceInstrument]:
        with self.snapshot() as snapshot:
            return snapshot.instruments(**filters)

    def instrument_availability(
        self, **filters: Any
    ) -> list[ReferenceInstrumentAvailability]:
        with self.snapshot() as snapshot:
            return snapshot.instrument_availability(**filters)

    def listings(self, **filters: Any) -> list[ReferenceListing]:
        with self.snapshot() as snapshot:
            return snapshot.listings(**filters)

    def markets(self, **filters: Any) -> list[ReferenceMarket]:
        with self.snapshot() as snapshot:
            return snapshot.markets(**filters)

    def collection(
        self, name: str, *, limit: int | None = None, offset: int = 0
    ) -> list[ReferenceRecord]:
        with self.snapshot() as snapshot:
            return snapshot.collection(name, limit=limit, offset=offset)

    def resolve_market(self, **filters: object) -> ReferenceMarket:
        markets = self.markets(**filters)
        if len(markets) != 1:
            raise RuntimeError("Reference market resolution is not unique")
        return markets[0]


def _identifiers(values: Sequence[str] | None) -> list[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        return [values]
    return list(dict.fromkeys(str(value) for value in values))


__all__ = ["ReferenceClient", "ReferenceReadSession"]
