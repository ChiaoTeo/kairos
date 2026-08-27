"""Reference control and owner-contract catalog reads."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any


def _native_module() -> Any:
    native = import_module("kairospy._native_reference_contract")
    info = native.build_info()
    if info.api_version != 1 or info.owner != "Reference":
        raise RuntimeError("incompatible Reference native contract binding")
    return native


@dataclass(frozen=True, slots=True)
class ReferenceReadSession:
    """One generation-pinned view whose SQLite transaction stays in Rust."""

    _native: Any
    generation: int
    event_sequence: int

    def close(self) -> None:
        self._native.close()

    def catalog(self) -> dict[str, Any]:
        status = self._native.status()
        return {
            "generation": int(status.generation),
            "event_sequence": int(status.event_sequence),
            "catalog": {
                "exchange_count": int(status.exchange_count),
                "asset_count": int(status.asset_count),
                "instrument_count": int(status.instrument_count),
                "listing_count": int(status.listing_count),
                "market_count": int(status.market_count),
                "active_market_count": int(status.active_market_count),
            },
            "integrity": {
                "missing_equity_markets": int(status.missing_equity_markets),
                "legacy_exchange_market_ids": int(
                    status.legacy_exchange_market_ids
                ),
                "legacy_exchange_listing_ids": int(
                    status.legacy_exchange_listing_ids
                ),
                "option_listings": int(status.option_listings),
                "option_markets": int(status.option_markets),
            },
        }

    def events(
        self,
        *,
        sequence_from: int | None = None,
        sequence_to: int | None = None,
        limit: int = 256,
    ) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "event_sequence": self.event_sequence,
            "events": [
                _event(value)
                for value in self._native.events(
                    sequence_from=sequence_from,
                    sequence_to=sequence_to,
                    limit=limit,
                )
            ],
        }

    def option_coverage(self) -> dict[str, Any]:
        return {
            "source_id": "massive-options",
            "generation": self.generation,
            "event_sequence": self.event_sequence,
            "underlyings": list(self._native.option_coverage()),
        }

    def outbox_depth(self) -> int:
        return int(self._native.outbox_depth())

    def exchanges(
        self,
        *,
        exchange_ids: Sequence[str] | None = None,
        query: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        values = self._native.exchanges(
            exchange_ids=_identifiers(exchange_ids),
            query=query,
            status=status,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
        return [_exchange(value) for value in values]

    def assets(
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
    ) -> list[dict[str, Any]]:
        values = self._native.assets(
            asset_ids=_identifiers(asset_ids),
            query=query,
            code=code,
            asset_class=asset_class,
            status=status,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
        return [_asset(value) for value in values]

    def instruments(
        self,
        *,
        instrument_ids: Sequence[str] | None = None,
        query: str | None = None,
        symbol: str | None = None,
        instrument_type: str | None = None,
        product_family: str | None = None,
        underlying_instrument_id: str | None = None,
        expiry_unix_nanos: int | None = None,
        expiry_from_unix_nanos: int | None = None,
        expiry_to_unix_nanos: int | None = None,
        option_right: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        values = self._native.instruments(
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
        return [_instrument(value) for value in values]

    def listings(
        self,
        *,
        listing_ids: Sequence[str] | None = None,
        query: str | None = None,
        instrument_id: str | None = None,
        exchange_id: str | None = None,
        exchange_symbol: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        values = self._native.listings(
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
        return [_listing(value) for value in values]

    def markets(
        self,
        *,
        market_ids: Sequence[str] | None = None,
        query: str | None = None,
        symbol: str | None = None,
        asset_code: str | None = None,
        exchange_id: str | None = None,
        instrument_kind: str | None = None,
        asset_type: str | None = None,
        instrument_id: str | None = None,
        listing_id: str | None = None,
        underlying_instrument_id: str | None = None,
        active_only: bool = False,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        values = self._native.markets(
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
        return [_market(value) for value in values]

    def collection(
        self,
        name: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
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

    def health(self) -> dict[str, Any]:
        return self.request("reference_health")

    def providers(self) -> dict[str, Any]:
        health = self.health()
        dependencies = health.get("dependencies")
        provider_rows = (
            dependencies.get("providers", [])
            if isinstance(dependencies, dict)
            else health.get("providers", [])
        )
        if self.database_path is None:
            return {
                "generation": health.get("generation", 0),
                "event_sequence": health.get("event_sequence", 0),
                "outbox_depth": health.get("outbox_depth", 0),
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

    def catalog(self) -> dict[str, Any]:
        with self.snapshot() as snapshot:
            return snapshot.catalog()

    def events(self, **filters: Any) -> dict[str, Any]:
        with self.snapshot() as snapshot:
            return snapshot.events(**filters)

    def option_coverage(self) -> dict[str, Any]:
        with self.snapshot() as snapshot:
            return snapshot.option_coverage()

    def exchanges(self, **filters: Any) -> list[dict[str, Any]]:
        with self.snapshot() as snapshot:
            return snapshot.exchanges(**filters)

    def assets(self, **filters: Any) -> list[dict[str, Any]]:
        with self.snapshot() as snapshot:
            return snapshot.assets(**filters)

    def instruments(self, **filters: Any) -> list[dict[str, Any]]:
        with self.snapshot() as snapshot:
            return snapshot.instruments(**filters)

    def listings(self, **filters: Any) -> list[dict[str, Any]]:
        with self.snapshot() as snapshot:
            return snapshot.listings(**filters)

    def markets(self, **filters: Any) -> list[dict[str, Any]]:
        with self.snapshot() as snapshot:
            return snapshot.markets(**filters)

    def collection(
        self, name: str, *, limit: int | None = None, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self.snapshot() as snapshot:
            return snapshot.collection(name, limit=limit, offset=offset)

    def resolve_market(self, **filters: object) -> dict[str, Any]:
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


def _exchange(value: Any) -> dict[str, Any]:
    return {
        "exchangeId": value.exchange_id,
        "name": value.name,
        "status": value.status,
    }


def _asset(value: Any) -> dict[str, Any]:
    return {
        "assetId": value.asset_id,
        "code": value.code,
        "name": value.name,
        "assetClass": value.asset_class,
        "status": value.status,
    }


def _instrument(value: Any) -> dict[str, Any]:
    return {
        "instrumentId": value.instrument_id,
        "symbol": value.symbol,
        "name": value.name,
        "instrumentType": value.instrument_type,
        "productFamily": value.product_family,
        "issuerId": value.issuer_id,
        "shareClass": value.share_class,
        "primaryCurrencyAssetId": value.primary_currency_asset_id,
        "underlyingInstrumentId": value.underlying_instrument_id,
        "expiryUnixNanos": value.expiry_unix_nanos,
        "strike": value.strike,
        "optionRight": value.option_right,
        "status": value.status,
    }


def _listing(value: Any) -> dict[str, Any]:
    return {
        "listingId": value.listing_id,
        "instrumentId": value.instrument_id,
        "exchangeId": value.exchange_id,
        "exchangeSymbol": value.exchange_symbol,
        "status": value.status,
        "effectiveFromUnixNanos": value.effective_from_unix_nanos,
        "effectiveToUnixNanos": value.effective_to_unix_nanos,
    }


def _market(value: Any) -> dict[str, Any]:
    return {
        "market_id": value.market_id,
        "instrument_id": value.instrument_id,
        "listing_id": value.listing_id,
        "exchange_id": value.exchange_id,
        "instrument_kind": value.instrument_kind,
        "asset_type": value.asset_type,
        "underlying_instrument_id": value.underlying_instrument_id,
        "venue_symbol": value.venue_symbol,
        "symbol": value.venue_symbol,
        "base_asset_id": value.base_asset_id,
        "base_asset": value.base_asset_id,
        "quote_asset_id": value.quote_asset_id,
        "quote_asset": value.quote_asset_id,
        "status": value.status,
        "price_tick": value.price_tick,
        "price_increment": value.price_tick,
        "quantity_tick": value.quantity_tick,
        "quantity_increment": value.quantity_tick,
        "price_precision": value.price_precision,
        "quantity_precision": value.quantity_precision,
        "minimum_quantity": value.minimum_quantity,
        "minimum_notional": value.minimum_notional,
        "contract_size": value.contract_size,
        "contract_multiplier": value.contract_size,
        "effective_from_unix_nanos": value.effective_from_unix_nanos,
        "effective_to_unix_nanos": value.effective_to_unix_nanos,
    }


def _event(value: Any) -> dict[str, Any]:
    result = {
        "sequence": value.sequence,
        "event_id": value.event_id,
        "event_type": value.event_type,
        "event_time_unix_nanos": value.event_time_unix_nanos,
        "record_kind": value.record_kind,
        "record_id": value.record_id,
        "market_id": value.market_id,
        "instrument_id": value.instrument_id,
        "listing_id": value.listing_id,
        "exchange_id": value.exchange_id,
        "venue_symbol": value.venue_symbol,
        "previous_status": value.previous_status,
        "current_status": value.current_status,
        "previous_symbol": value.previous_symbol,
        "current_symbol": value.current_symbol,
        "operation": value.operation,
        "provenance": value.provenance,
        "conflict_policy": value.conflict_policy,
        "generation": value.generation,
    }
    return {key: item for key, item in result.items() if item is not None}


__all__ = ["ReferenceClient", "ReferenceReadSession"]
