"""Reference SQLite and low-frequency control contract client."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any, cast
from urllib.parse import urlencode

from kairospy.infrastructure.unix_http import request_sync


@dataclass(frozen=True, slots=True)
class ReferenceClient:
    """Read Reference state from the contract-owned read-only SQLite model."""

    socket_path: Path | None = None
    database_path: Path | None = None
    timeout: float = 5.0

    def reference_views(self) -> list[dict[str, Any]]:
        tables = {
            "catalog": "reference_meta",
            "entities": "reference_entities_current",
            "assets": "reference_assets_current",
            "instruments": "reference_instruments_current",
            "listings": "reference_listings_current",
            "markets": "reference_markets_current",
            "financial-products": "reference_financial_products_current",
            "execution-accesses": "reference_execution_accesses_current",
        }
        existing: set[str] = set()
        if self.database_path is not None and self.database_path.exists():
            with self._connection() as connection:
                existing = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
        return [
            {
                "view": view,
                "view_key": f"reference.{view.replace('-', '_')}",
                "table": table,
                "path": str(self.database_path) if self.database_path else None,
                "exists": table in existing,
            }
            for view, table in tables.items()
        ]

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        timeout: float | None = None,
        **params: object,
    ) -> Any:
        if self.socket_path is None:
            raise RuntimeError("Reference control socket is not configured")
        query = urlencode(
            {
                key: str(value).lower() if isinstance(value, bool) else str(value)
                for key, value in params.items()
                if value is not None
            }
        )
        target = f"{path}?{query}" if query else path
        try:
            status, value = request_sync(
                self.socket_path,
                method,
                target,
                timeout=self.timeout if timeout is None else timeout,
            )
        except OSError as error:
            raise RuntimeError(f"Reference request failed: {error}") from error
        except ValueError as error:
            raise RuntimeError("Reference returned an invalid JSON response") from error
        if status >= 400:
            message = (
                value.get("error", f"HTTP {status}")
                if isinstance(value, dict)
                else f"HTTP {status}"
            )
            raise RuntimeError(str(message))
        return value

    def health(self) -> dict[str, Any]:
        return self.request("/v1/health")

    def providers(self) -> dict[str, Any]:
        return self.request("/v1/providers")

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
        return self.request(
            "/v1/events",
            timeout=max(self.timeout, 120.0),
            sequence_from=sequence_from,
            sequence_to=sequence_to,
            limit=limit,
        )

    def refresh(self, *, source: str | None = None) -> dict[str, Any]:
        # One incremental provider page is allowed to spend the provider
        # fetch budget; query/status calls should remain short-lived.
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
        return self.request("/v1/options/coverage")

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
        with self._connection() as connection:
            meta = connection.execute(
                "SELECT generation,event_sequence FROM reference_meta WHERE id = 1"
            ).fetchone()
            if meta is None:
                raise RuntimeError("Reference SQLite metadata is missing")
            names = {
                "entity": "reference_entities_current",
                "asset": "reference_assets_current",
                "instrument": "reference_instruments_current",
                "listing": "reference_listings_current",
                "market": "reference_markets_current",
                "financial_product": "reference_financial_products_current",
                "execution_access": "reference_execution_accesses_current",
            }
            counts = {
                f"{name}_count": int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                for name, table in names.items()
            }
            active_market_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM reference_markets_current WHERE status IN ('active','trading')"
                ).fetchone()[0]
            )
        return {
            "generation": int(meta[0]),
            "event_sequence": int(meta[1]),
            "catalog": {
                **counts,
                "active_market_count": active_market_count,
            },
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
        clauses: list[str] = []
        params: list[object] = []
        filters = (
            ("source_symbol", symbol),
            ("exchange_id", exchange_id),
            ("market_type", market_type),
            ("asset_type", asset_type),
            ("status", status),
        )
        for column, value in filters:
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if active_only:
            clauses.append("status IN ('active','trading')")
        sql = "SELECT payload FROM reference_markets_current"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY market_id LIMIT ?"
        params.append(max(1, min(limit or 10_000, 10_000)))
        with self._connection() as connection:
            result = [
                json.loads(str(row[0])) for row in connection.execute(sql, params)
            ]
        for value in result:
            value["symbol"] = value.get("source_symbol")
        return result

    def execution_accesses(
        self,
        *,
        provider_id: str | None = None,
        product_family: str | None = None,
        provider_symbol: str | None = None,
        active_only: bool = False,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for value in self.collection("execution-accesses"):
            if provider_id is not None and value.get("providerId") != provider_id:
                continue
            if (
                product_family is not None
                and value.get("productFamily") != product_family
            ):
                continue
            if (
                provider_symbol is not None
                and value.get("providerSymbol") != provider_symbol
            ):
                continue
            if status is not None and value.get("status") != status:
                continue
            if active_only and value.get("status") != "active":
                continue
            result.append(value)
            if limit is not None and len(result) >= limit:
                break
        return result

    def collection(self, view: str) -> list[dict[str, Any]]:
        tables = {
            "entities": "reference_entities_current",
            "assets": "reference_assets_current",
            "instruments": "reference_instruments_current",
            "listings": "reference_listings_current",
            "financial-products": "reference_financial_products_current",
            "execution-accesses": "reference_execution_accesses_current",
        }
        table = tables.get(view)
        if table is None:
            raise ValueError(f"unsupported Reference collection: {view}")
        with self._connection() as connection:
            return [
                self._camelize(json.loads(str(row[0])))
                for row in connection.execute(
                    f"SELECT payload FROM {table} ORDER BY 1 LIMIT 10000"
                )
            ]

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

    def _connection(self) -> sqlite3.Connection:
        if self.database_path is None:
            raise RuntimeError("Reference SQLite database path is not configured")
        try:
            connection = sqlite3.connect(
                f"file:{self.database_path}?mode=ro", uri=True, timeout=self.timeout
            )
            connection.execute("PRAGMA query_only = ON")
            version = connection.execute(
                "SELECT schema_version FROM reference_meta WHERE id = 1"
            ).fetchone()
        except sqlite3.Error as error:
            raise RuntimeError(f"Reference SQLite read failed: {error}") from error
        if version is None or int(version[0]) != 1:
            connection.close()
            raise RuntimeError("unsupported Reference SQLite schema")
        return connection

    @staticmethod
    def _camelize(value: dict[str, Any]) -> dict[str, Any]:
        return {
            key.split("_")[0]
            + "".join(part.title() for part in key.split("_")[1:]): item
            for key, item in value.items()
        }


__all__ = ["ReferenceClient"]
