"""Reference control commands and contract-owned read-only SQLite queries."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any


_COLLECTIONS = {
    "entities": ("reference_entities_current", "entity_id"),
    "assets": ("reference_assets_current", "asset_id"),
    "instruments": ("reference_instruments_current", "instrument_id"),
    "listings": ("reference_listings_current", "listing_id"),
}
_MAX_QUERY_LIMIT = 10_000


@dataclass(frozen=True, slots=True)
class ReferenceReadSession:
    """One generation-pinned, read-only view of the Reference projection."""

    _connection: sqlite3.Connection
    generation: int
    event_sequence: int

    def catalog(self) -> dict[str, Any]:
        tables = {
            "entity_count": "reference_entities_current",
            "asset_count": "reference_assets_current",
            "instrument_count": "reference_instruments_current",
            "listing_count": "reference_listings_current",
            "market_count": "reference_markets_current",
        }
        counts = {
            name: int(
                self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for name, table in tables.items()
        }
        counts["active_market_count"] = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM reference_markets_current "
                "WHERE status IN ('active', 'trading')"
            ).fetchone()[0]
        )
        return {
            "generation": self.generation,
            "event_sequence": self.event_sequence,
            "catalog": counts,
        }

    def events(
        self,
        *,
        sequence_from: int | None = None,
        sequence_to: int | None = None,
        limit: int = 256,
    ) -> dict[str, Any]:
        if sequence_from is not None and sequence_from < 0:
            raise ValueError("sequence_from must be non-negative")
        if sequence_to is not None and sequence_to < 0:
            raise ValueError("sequence_to must be non-negative")
        bounded_limit, _ = _page(limit, 0, maximum=4096)
        assert bounded_limit is not None
        clauses: list[str] = []
        values: list[object] = []
        if sequence_from is not None:
            clauses.append("sequence >= ?")
            values.append(sequence_from)
        if sequence_to is not None:
            clauses.append("sequence <= ?")
            values.append(sequence_to)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"SELECT sequence, payload FROM reference_lifecycle{where} "
            "ORDER BY sequence LIMIT ?",
            (*values, bounded_limit),
        ).fetchall()
        events = []
        for row in rows:
            payload = _payload(row["payload"])
            payload.setdefault("sequence", int(row["sequence"]))
            events.append(payload)
        return {
            "generation": self.generation,
            "event_sequence": self.event_sequence,
            "events": events,
        }

    def option_coverage(self) -> dict[str, Any]:
        rows = self._connection.execute(
            "SELECT underlying FROM reference_option_coverage "
            "WHERE provider = ? AND enabled = 1 ORDER BY underlying",
            ("massive-options",),
        ).fetchall()
        return {
            "source_id": "massive-options",
            "generation": self.generation,
            "event_sequence": self.event_sequence,
            "underlyings": [str(row["underlying"]) for row in rows],
        }

    def entities(
        self,
        *,
        entity_ids: Sequence[str] | None = None,
        entity_type: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self._records(
            "entities",
            filters={"entity_type": entity_type, "status": status},
            ids=entity_ids,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )

    def assets(
        self,
        *,
        asset_ids: Sequence[str] | None = None,
        code: str | None = None,
        asset_class: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self._records(
            "assets",
            filters={"code": code, "asset_class": asset_class, "status": status},
            ids=asset_ids,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )

    def instruments(
        self,
        *,
        instrument_ids: Sequence[str] | None = None,
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
        expiry_values = (
            expiry_unix_nanos,
            expiry_from_unix_nanos,
            expiry_to_unix_nanos,
        )
        if any(value is not None and value < 0 for value in expiry_values):
            raise ValueError("expiry timestamps must be non-negative")
        if (
            expiry_from_unix_nanos is not None
            and expiry_to_unix_nanos is not None
            and expiry_from_unix_nanos > expiry_to_unix_nanos
        ):
            raise ValueError(
                "expiry_from_unix_nanos must not exceed expiry_to_unix_nanos"
            )
        normalized_option_right = (
            option_right.strip().lower() if option_right is not None else None
        )
        if normalized_option_right not in {None, "call", "put"}:
            raise ValueError("option_right must be call or put")
        filters: dict[str, object | None] = {
            "symbol": symbol,
            "instrument_type": instrument_type,
            "product_family": product_family,
            "underlying_instrument_id": underlying_instrument_id,
            "expiry_unix_nanos": expiry_unix_nanos,
            "status": status,
        }
        extra_clauses: list[str] = []
        extra_values: list[object] = []
        if expiry_from_unix_nanos is not None:
            extra_clauses.append("expiry_unix_nanos >= ?")
            extra_values.append(expiry_from_unix_nanos)
        if expiry_to_unix_nanos is not None:
            extra_clauses.append("expiry_unix_nanos <= ?")
            extra_values.append(expiry_to_unix_nanos)
        if normalized_option_right is not None:
            extra_clauses.append("json_extract(payload, '$.option_right') = ?")
            extra_values.append(normalized_option_right)
        return self._records(
            "instruments",
            filters=filters,
            ids=instrument_ids,
            active_only=active_only,
            extra_clauses=extra_clauses,
            extra_values=extra_values,
            limit=limit,
            offset=offset,
            order_by=(
                "expiry_unix_nanos, "
                "CAST(json_extract(payload, '$.strike') AS REAL), instrument_id"
                if underlying_instrument_id is not None
                else "instrument_id"
            ),
        )

    def listings(
        self,
        *,
        listing_ids: Sequence[str] | None = None,
        instrument_id: str | None = None,
        exchange_id: str | None = None,
        exchange_symbol: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self._records(
            "listings",
            filters={
                "instrument_id": instrument_id,
                "exchange_id": _exchange_id(exchange_id),
                "exchange_symbol": exchange_symbol,
                "status": status,
            },
            ids=listing_ids,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )

    def markets(
        self,
        *,
        market_ids: Sequence[str] | None = None,
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
        extra_clauses: list[str] = []
        extra_values: list[object] = []
        if asset_code is not None:
            normalized_asset_code = asset_code.strip().upper()
            extra_clauses.append(
                "("
                "instrument_id IN ("
                "SELECT instrument_id FROM reference_instruments_current "
                "WHERE symbol = ?"
                ") OR underlying_instrument_id IN ("
                "SELECT instrument_id FROM reference_instruments_current "
                "WHERE symbol = ?"
                ") OR json_extract(payload, '$.base_asset_id') IN ("
                "SELECT asset_id FROM reference_assets_current WHERE code = ?"
                ") OR json_extract(payload, '$.quote_asset_id') IN ("
                "SELECT asset_id FROM reference_assets_current WHERE code = ?"
                ")"
                ")"
            )
            extra_values.extend(
                (
                    normalized_asset_code,
                    normalized_asset_code,
                    normalized_asset_code,
                    normalized_asset_code,
                )
            )
        rows = self._query_rows(
            table="reference_markets_current",
            key="market_id",
            filters={
                "venue_symbol": symbol,
                "exchange_id": _exchange_id(exchange_id),
                "instrument_kind": instrument_kind,
                "asset_type": asset_type,
                "instrument_id": instrument_id,
                "listing_id": listing_id,
                "underlying_instrument_id": underlying_instrument_id,
                "status": status,
            },
            ids=market_ids,
            active_only=active_only,
            extra_clauses=extra_clauses,
            extra_values=extra_values,
            limit=limit,
            offset=offset,
        )
        return [_market(_payload(row["payload"])) for row in rows]

    def collection(
        self,
        name: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self._records(name, filters={}, limit=limit, offset=offset)

    def _records(
        self,
        name: str,
        *,
        filters: dict[str, object | None],
        ids: Sequence[str] | None = None,
        active_only: bool = False,
        extra_clauses: Sequence[str] = (),
        extra_values: Sequence[object] = (),
        limit: int | None = None,
        offset: int = 0,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            table, key = _COLLECTIONS[name]
        except KeyError as error:
            raise ValueError(f"unsupported Reference collection: {name}") from error
        rows = self._query_rows(
            table=table,
            key=key,
            filters=filters,
            ids=ids,
            active_only=active_only,
            extra_clauses=extra_clauses,
            extra_values=extra_values,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )
        return [_public_record(name, _payload(row["payload"])) for row in rows]

    def _query_rows(
        self,
        *,
        table: str,
        key: str,
        filters: dict[str, object | None],
        ids: Sequence[str] | None = None,
        active_only: bool = False,
        extra_clauses: Sequence[str] = (),
        extra_values: Sequence[object] = (),
        limit: int | None = None,
        offset: int = 0,
        order_by: str | None = None,
    ) -> list[sqlite3.Row]:
        bounded_limit, offset = _page(limit, offset)
        clauses = [
            f"{name} = ?" for name, value in filters.items() if value is not None
        ]
        values: list[object] = [
            value for value in filters.values() if value is not None
        ]
        id_values: Sequence[str] = (ids,) if isinstance(ids, str) else (ids or ())
        normalized_ids = tuple(dict.fromkeys(str(value) for value in id_values))
        if ids is not None:
            if not normalized_ids:
                return []
            clauses.append(f"{key} IN ({','.join('?' for _ in normalized_ids)})")
            values.extend(normalized_ids)
        if active_only:
            clauses.append("status IN ('active', 'trading')")
        clauses.extend(extra_clauses)
        values.extend(extra_values)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        if bounded_limit is None:
            return self._connection.execute(
                f"SELECT payload FROM {table}{where} "
                f"ORDER BY {order_by or key} LIMIT -1 OFFSET ?",
                (*values, offset),
            ).fetchall()
        return self._connection.execute(
            f"SELECT payload FROM {table}{where} "
            f"ORDER BY {order_by or key} LIMIT ? OFFSET ?",
            (*values, bounded_limit, offset),
        ).fetchall()


@dataclass(frozen=True, slots=True)
class ReferenceClient:
    """Keep Reference persistence details behind one typed client boundary."""

    socket_path: Path | None = None
    database_path: Path | None = None
    timeout: float = 5.0

    def _control(self):
        if self.socket_path is None:
            raise RuntimeError("Reference control socket is not configured")
        from .control import ReferenceControlClient

        return ReferenceControlClient(self.socket_path, timeout=self.timeout)

    def _connection(self) -> sqlite3.Connection:
        if self.database_path is None:
            raise RuntimeError("Reference database is not configured")
        connection = sqlite3.connect(
            f"file:{self.database_path}?mode=ro",
            uri=True,
            timeout=self.timeout,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    @contextmanager
    def snapshot(self) -> Iterator[ReferenceReadSession]:
        """Pin all enclosed reads to one committed Reference generation."""

        connection = self._connection()
        try:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT generation, event_sequence FROM reference_meta WHERE id = 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("Reference SQLite metadata is missing")
            yield ReferenceReadSession(
                connection,
                generation=int(row["generation"]),
                event_sequence=int(row["event_sequence"]),
            )
        finally:
            connection.close()

    def request(
        self,
        method: str,
        *,
        timeout: float | None = None,
        params: list[object] | dict[str, object] | None = None,
    ) -> dict[str, Any]:
        try:
            control = self._control()
            if timeout is not None and timeout != self.timeout:
                from .control import ReferenceControlClient

                control = ReferenceControlClient(self.socket_path, timeout=timeout)
            return dict(
                control.call(method, params)
            )
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
            outbox_depth = int(
                snapshot._connection.execute(
                    "SELECT COUNT(*) FROM reference_publication_outbox"
                ).fetchone()[0]
            )
            return {
                "generation": snapshot.generation,
                "event_sequence": snapshot.event_sequence,
                "outbox_depth": outbox_depth,
                "providers": provider_rows,
            }

    def refresh(self, *, source: str | None = None) -> dict[str, Any]:
        return self.request(
            "reference_refresh",
            timeout=max(self.timeout, 120.0),
            params=[source],
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

    def entities(self, **filters: Any) -> list[dict[str, Any]]:
        with self.snapshot() as snapshot:
            return snapshot.entities(**filters)

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


def _page(
    limit: int | None, offset: int, *, maximum: int = _MAX_QUERY_LIMIT
) -> tuple[int | None, int]:
    if limit is not None and not 1 <= limit <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    if offset < 0:
        raise ValueError("offset must be non-negative")
    return limit, offset


def _exchange_id(value: str | None) -> str | None:
    if value is None or value.startswith("exchange:"):
        return value
    return f"exchange:{value}"


def _payload(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise RuntimeError("Reference SQLite payload is not an object")
    return decoded


def _market(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["symbol"] = result.get("venue_symbol")
    result["base_asset"] = result.get("base_asset_id")
    result["quote_asset"] = result.get("quote_asset_id")
    result["price_increment"] = result.get("price_tick")
    result["quantity_increment"] = result.get("quantity_tick")
    result["contract_multiplier"] = result.get("contract_size")
    return result


def _public_record(name: str, value: dict[str, Any]) -> dict[str, Any]:
    mappings = {
        "entities": {"entity_id": "entityId", "entity_type": "entityType"},
        "assets": {"asset_id": "assetId", "asset_class": "assetClass"},
        "instruments": {
            "instrument_id": "instrumentId",
            "instrument_type": "instrumentType",
            "product_family": "productFamily",
            "issuer_id": "issuerId",
            "share_class": "shareClass",
            "primary_currency_asset_id": "primaryCurrencyAssetId",
            "underlying_instrument_id": "underlyingInstrumentId",
            "expiry_unix_nanos": "expiryUnixNanos",
            "option_right": "optionRight",
        },
        "listings": {
            "listing_id": "listingId",
            "instrument_id": "instrumentId",
            "exchange_id": "exchangeId",
            "exchange_symbol": "exchangeSymbol",
            "effective_from_unix_nanos": "effectiveFromUnixNanos",
            "effective_to_unix_nanos": "effectiveToUnixNanos",
        },
    }
    renames = mappings[name]
    return {renames.get(key, key): item for key, item in value.items()}


__all__ = ["ReferenceClient", "ReferenceReadSession"]
