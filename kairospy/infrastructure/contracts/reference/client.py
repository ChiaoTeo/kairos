"""Reference control commands and contract-owned read-only SQLite queries."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any, cast


_COLLECTIONS = {
    "entities": ("reference_entities_current", "entity_id"),
    "assets": ("reference_assets_current", "asset_id"),
    "instruments": ("reference_instruments_current", "instrument_id"),
    "listings": ("reference_listings_current", "listing_id"),
    "execution-accesses": ("reference_execution_accesses_current", "access_id"),
}


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

    def _watermark(self, connection: sqlite3.Connection) -> tuple[int, int]:
        row = connection.execute(
            "SELECT generation, event_sequence FROM reference_meta WHERE id = 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("Reference SQLite metadata is missing")
        return int(row["generation"]), int(row["event_sequence"])

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        timeout: float | None = None,
        **params: object,
    ) -> dict[str, Any]:
        try:
            return dict(
                self._control().request(
                    method,
                    path,
                    params=params,
                    timeout=self.timeout if timeout is None else timeout,
                )
            )
        except OSError as error:
            raise RuntimeError(f"Reference request failed: {error}") from error

    def health(self) -> dict[str, Any]:
        return self.request("/v1/health")

    def providers(self) -> dict[str, Any]:
        health = self.health()
        dependencies = health.get("dependencies")
        provider_rows = (
            dependencies.get("providers", [])
            if isinstance(dependencies, dict)
            else health.get("providers", [])
        )
        if self.database_path is None:
            # Preserve compatibility for injected diagnostic clients while
            # production clients obtain durable state from SQLite.
            return {
                "generation": health.get("generation", 0),
                "event_sequence": health.get("event_sequence", 0),
                "outbox_depth": health.get("outbox_depth", 0),
                "providers": provider_rows,
            }
        with self._connection() as connection:
            generation, event_sequence = self._watermark(connection)
            outbox_depth = int(
                connection.execute(
                    "SELECT COUNT(*) FROM reference_publication_outbox"
                ).fetchone()[0]
            )
        return {
            "generation": generation,
            "event_sequence": event_sequence,
            "outbox_depth": outbox_depth,
            "providers": provider_rows,
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
        if not 1 <= limit <= 4096:
            raise ValueError("limit must be between 1 and 4096")
        clauses: list[str] = []
        values: list[object] = []
        if sequence_from is not None:
            clauses.append("sequence >= ?")
            values.append(sequence_from)
        if sequence_to is not None:
            clauses.append("sequence <= ?")
            values.append(sequence_to)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection() as connection:
            generation, event_sequence = self._watermark(connection)
            rows = connection.execute(
                f"SELECT sequence, payload FROM reference_lifecycle{where} "
                "ORDER BY sequence LIMIT ?",
                (*values, limit),
            ).fetchall()
        events = []
        for row in rows:
            payload = _payload(row["payload"])
            payload.setdefault("sequence", int(row["sequence"]))
            events.append(payload)
        return {
            "generation": generation,
            "event_sequence": event_sequence,
            "events": events,
        }

    def refresh(self, *, source: str | None = None) -> dict[str, Any]:
        return self.request(
            "/v1/refresh",
            method="POST",
            timeout=max(self.timeout, 120.0),
            source=source,
        )

    def set_source_paused(self, source: str, paused: bool) -> dict[str, Any]:
        if not source.strip():
            raise ValueError("source is required")
        return self.request(
            "/v1/sources/pause" if paused else "/v1/sources/resume",
            method="POST",
            source=source,
        )

    def option_coverage(self) -> dict[str, Any]:
        with self._connection() as connection:
            generation, event_sequence = self._watermark(connection)
            rows = connection.execute(
                "SELECT underlying FROM reference_option_coverage "
                "WHERE provider = ? AND enabled = 1 ORDER BY underlying",
                ("massive-options",),
            ).fetchall()
        return {
            "source_id": "massive-options",
            "generation": generation,
            "event_sequence": event_sequence,
            "underlyings": [str(row["underlying"]) for row in rows],
        }

    def set_option_underlying(self, underlying: str, enabled: bool) -> dict[str, Any]:
        if not underlying.strip():
            raise ValueError("underlying is required")
        return self.request(
            "/v1/options/coverage/add" if enabled else "/v1/options/coverage/remove",
            method="POST",
            timeout=max(self.timeout, 120.0),
            underlying=underlying,
        )

    def catalog(self) -> dict[str, Any]:
        tables = {
            "entity_count": "reference_entities_current",
            "asset_count": "reference_assets_current",
            "instrument_count": "reference_instruments_current",
            "listing_count": "reference_listings_current",
            "market_count": "reference_markets_current",
            "execution_access_count": "reference_execution_accesses_current",
            "market_data_access_count": "reference_market_data_accesses_current",
        }
        with self._connection() as connection:
            generation, event_sequence = self._watermark(connection)
            counts = {
                name: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for name, table in tables.items()
            }
            counts["active_market_count"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM reference_markets_current "
                    "WHERE status IN ('active', 'trading')"
                ).fetchone()[0]
            )
        return {
            "generation": generation,
            "event_sequence": event_sequence,
            "catalog": counts,
        }

    def markets(
        self,
        *,
        symbol: str | None = None,
        exchange_id: str | None = None,
        market_type: str | None = None,
        asset_type: str | None = None,
        active_only: bool = False,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if exchange_id is not None and not exchange_id.startswith("exchange:"):
            exchange_id = f"exchange:{exchange_id}"
        filters = {
            "source_symbol": symbol,
            "exchange_id": exchange_id,
            "market_type": market_type,
            "asset_type": asset_type,
            "status": status,
        }
        clauses = [f"{name} = ?" for name, value in filters.items() if value is not None]
        values = [value for value in filters.values() if value is not None]
        if active_only:
            clauses.append("status IN ('active', 'trading')")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        bounded_limit = max(1, min(limit or 10_000, 10_000))
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT payload FROM reference_markets_current{where} "
                "ORDER BY market_id LIMIT ?",
                (*values, bounded_limit),
            ).fetchall()
        return [_market(_payload(row["payload"])) for row in rows]

    def execution_accesses(self, **filters: object) -> list[dict[str, Any]]:
        result = []
        for value in self.collection("execution-accesses"):
            if any(
                filters.get(name) is not None and value.get(public) != filters[name]
                for name, public in (
                    ("provider_id", "providerId"),
                    ("product_family", "productFamily"),
                    ("provider_symbol", "providerSymbol"),
                    ("status", "status"),
                )
            ):
                continue
            if filters.get("active_only") and value.get("status") != "active":
                continue
            result.append(value)
            if filters.get("limit") is not None and len(result) >= int(
                cast(int, filters["limit"])
            ):
                break
        return result

    def collection(self, name: str) -> list[dict[str, Any]]:
        try:
            table, key = _COLLECTIONS[name]
        except KeyError as error:
            raise ValueError(f"unsupported Reference collection: {name}") from error
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT payload FROM {table} ORDER BY {key}"
            ).fetchall()
        return [_public_record(name, _payload(row["payload"])) for row in rows]

    def resolve_market(self, **filters: object) -> dict[str, Any]:
        markets = self.markets(
            symbol=cast(str | None, filters.get("symbol")),
            exchange_id=cast(str | None, filters.get("exchange_id")),
            market_type=cast(str | None, filters.get("market_type")),
            asset_type=cast(str | None, filters.get("asset_type")),
            active_only=cast(bool, filters.get("active_only", True)),
            status=cast(str | None, filters.get("status")),
        )
        if len(markets) != 1:
            raise RuntimeError("Reference market resolution is not unique")
        return markets[0]


def _payload(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise RuntimeError("Reference SQLite payload is not an object")
    return decoded


def _market(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["symbol"] = result.get("source_symbol")
    result["base_asset"] = result.get("base_asset_id")
    result["quote_asset"] = result.get("quote_asset_id")
    result["price_increment"] = result.get("price_tick")
    result["quantity_increment"] = result.get("quantity_tick")
    result["contract_multiplier"] = result.get("contract_size")
    return result


def _public_record(name: str, value: dict[str, Any]) -> dict[str, Any]:
    mappings = {
        "entities": {
            "entity_id": "entityId",
            "entity_type": "entityType",
        },
        "assets": {"asset_id": "assetId", "asset_class": "assetClass"},
        "instruments": {
            "instrument_id": "instrumentId",
            "instrument_type": "instrumentType",
            "underlying_instrument_id": "underlyingInstrumentId",
            "expiry_unix_nanos": "expiryUnixNanos",
            "option_right": "optionRight",
        },
        "listings": {
            "listing_id": "listingId",
            "instrument_id": "instrumentId",
            "exchange_id": "exchangeId",
            "exchange_symbol": "exchangeSymbol",
        },
        "execution-accesses": {
            "access_id": "accessId",
            "instrument_id": "instrumentId",
            "listing_id": "listingId",
            "market_id": "marketId",
            "provider_id": "providerId",
            "product_family": "productFamily",
            "provider_symbol": "providerSymbol",
            "settlement_asset_id": "settlementAssetId",
        },
    }
    renames = mappings[name]
    return {renames.get(key, key): item for key, item in value.items()}


__all__ = ["ReferenceClient"]
