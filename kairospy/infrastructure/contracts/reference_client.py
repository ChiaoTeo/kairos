"""Reference snapshot and low-frequency query contract client."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode

from kairospy.infrastructure.transport.shared_snapshot import SharedSnapshotReader
from kairospy.infrastructure.unix_http import request_sync


@dataclass(frozen=True, slots=True)
class ReferenceSnapshotClient:
    """Read Reference projections through the contract-owned transports."""

    socket_path: Path | None = None
    snapshot_path: Path | None = None
    entities_snapshot_path: Path | None = None
    assets_snapshot_path: Path | None = None
    instruments_snapshot_path: Path | None = None
    listings_snapshot_path: Path | None = None
    markets_snapshot_path: Path | None = None
    financial_products_snapshot_path: Path | None = None
    execution_accesses_snapshot_path: Path | None = None
    timeout: float = 5.0

    def snapshot_views(self) -> list[dict[str, Any]]:
        views = (
            ("catalog", self.snapshot_path, "reference.catalog", "PRC1"),
            ("entities", self.entities_snapshot_path, "reference.entities", "PRS1"),
            ("assets", self.assets_snapshot_path, "reference.assets", "PRS1"),
            (
                "instruments",
                self.instruments_snapshot_path,
                "reference.instruments",
                "PRS1",
            ),
            ("listings", self.listings_snapshot_path, "reference.listings", "PRS1"),
            ("markets", self.markets_snapshot_path, "reference.markets", "PRD1"),
            (
                "financial-products",
                self.financial_products_snapshot_path,
                "reference.financial_products",
                "PRS1",
            ),
            (
                "execution-accesses",
                self.execution_accesses_snapshot_path,
                "reference.execution_accesses",
                "PRS1",
            ),
        )
        return [
            {
                "view": view,
                "view_key": view_key,
                "file_identifier": identifier,
                "path": str(path) if path is not None else None,
                "exists": path.exists() if path is not None else False,
            }
            for view, path, view_key, identifier in views
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
        except (OSError, ValueError) as error:
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

    def refresh(self) -> dict[str, Any]:
        # One incremental provider page is allowed to spend the provider
        # fetch budget; query/status calls should remain short-lived.
        return self.request(
            "/v1/refresh", method="POST", timeout=max(self.timeout, 120.0)
        )

    def snapshot(self) -> dict[str, Any]:
        return self.catalog()

    def catalog(self) -> dict[str, Any]:
        if self.snapshot_path is None:
            raise RuntimeError("Reference catalog snapshot path is not configured")
        payload, generation = self._read_payload(self.snapshot_path)
        from kairospy.infrastructure.transport.generated.kairos.reference.v1.CatalogSnapshot import (
            CatalogSnapshot,
        )

        self._require_identifier(payload, b"PRC1", "Reference catalog")
        root = CatalogSnapshot.GetRootAs(payload, 0)
        header = root.Header()
        catalog = root.Payload()
        if header is None or catalog is None:
            raise RuntimeError(
                "Reference catalog snapshot is missing header or payload"
            )
        collections = {
            name: self._decode_table_collection(catalog, name)
            for name in (
                "entities",
                "assets",
                "instruments",
                "listings",
                "markets",
                "financial_products",
                "execution_accesses",
            )
        }
        return {
            "actor_id": self._text(header.OwnerActorId()),
            "generation": header.Generation() or generation,
            "event_sequence": header.EventSequence(),
            "catalog": {
                "entity_count": catalog.EntityCount(),
                "asset_count": catalog.AssetCount(),
                "instrument_count": catalog.InstrumentCount(),
                "listing_count": catalog.ListingCount(),
                "market_count": catalog.MarketCount(),
                "financial_product_count": catalog.FinancialProductCount(),
                "active_market_count": catalog.ActiveMarketCount(),
                "lifecycle_event_count": catalog.LifecycleEventCount(),
                **collections,
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
        if self.markets_snapshot_path is None:
            raise RuntimeError("Reference markets snapshot path is not configured")
        payload, _ = self._read_payload(self.markets_snapshot_path)
        from kairospy.infrastructure.transport.generated.kairos.reference.v1.MarketsSnapshot import (
            MarketsSnapshot,
        )

        self._require_identifier(payload, b"PRD1", "Reference markets")
        data = MarketsSnapshot.GetRootAs(payload, 0).Payload()
        if data is None:
            raise RuntimeError("Reference markets snapshot is missing payload")
        result: list[dict[str, Any]] = []
        for index in range(data.MarketsLength()):
            market = data.Markets(index)
            if market is None:
                continue
            value = {
                "market_id": self._text(market.MarketId()),
                "market_key": self._text(market.MarketKey()),
                "instrument_id": self._text(market.InstrumentId()),
                "listing_id": self._text(market.ListingId()),
                "exchange_id": self._text(market.ExchangeId()),
                "market_type": self._text(market.MarketType()),
                "symbol": self._text(market.SourceSymbol()),
                "base_asset_id": self._text(market.BaseAssetId()),
                "quote_asset_id": self._text(market.QuoteAssetId()),
                "status": self._text(market.Status()),
                "asset_type": self._text(market.AssetType()),
                "underlying_instrument_id": self._text(market.UnderlyingInstrumentId()),
                "price_tick": self._decimal(market.PriceTick()),
                "quantity_tick": self._decimal(market.QuantityTick()),
                "minimum_quantity": self._decimal(market.MinimumQuantity()),
                "minimum_notional": self._decimal(market.MinimumNotional()),
                "contract_size": self._decimal(market.ContractSize()),
                "price_precision": market.PricePrecision(),
                "quantity_precision": market.QuantityPrecision(),
                "effective_from_unix_nanos": market.EffectiveFromUnixNanos(),
                "effective_to_unix_nanos": market.EffectiveToUnixNanos(),
            }
            if symbol is not None and value["symbol"] != symbol:
                continue
            if exchange_id is not None and value["exchange_id"] != exchange_id:
                continue
            if market_type is not None and value["market_type"] != market_type:
                continue
            if asset_type is not None and value.get("asset_type") != asset_type:
                continue
            if status is not None and value["status"] != status:
                continue
            if active_only and value["status"] != "active":
                continue
            result.append(value)
            if limit is not None and len(result) >= limit:
                break
        return result

    def collection(self, view: str) -> list[dict[str, Any]]:
        paths = {
            "entities": self.entities_snapshot_path,
            "assets": self.assets_snapshot_path,
            "instruments": self.instruments_snapshot_path,
            "listings": self.listings_snapshot_path,
            "financial-products": self.financial_products_snapshot_path,
            "execution-accesses": self.execution_accesses_snapshot_path,
        }
        path = paths.get(view)
        if path is None:
            raise RuntimeError(
                f"Reference collection snapshot path is not configured: {view}"
            )
        payload, _ = self._read_payload(path)
        from kairospy.infrastructure.transport.generated.kairos.reference.v1.ReferenceCollectionsSnapshot import (
            ReferenceCollectionsSnapshot,
        )

        self._require_identifier(payload, b"PRS1", f"Reference {view}")
        data = ReferenceCollectionsSnapshot.GetRootAs(payload, 0).Payload()
        if data is None:
            raise RuntimeError(f"Reference {view} snapshot is missing payload")
        return self._decode_table_collection(data, view)

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

    def _read_payload(self, path: Path) -> tuple[bytes, int]:
        try:
            snapshot = SharedSnapshotReader(path).read()
        except (OSError, ValueError, RuntimeError) as error:
            raise RuntimeError(f"invalid Reference shared snapshot: {path}") from error
        manifest_path = path.parent / "reference.manifest"
        if manifest_path.exists():
            from kairospy.infrastructure.contracts.reference import read_manifest

            manifest = read_manifest(manifest_path)
            if snapshot.generation != manifest["generation"]:
                raise RuntimeError(
                    f"Reference snapshot generation {snapshot.generation} does not match "
                    f"manifest generation {manifest['generation']}"
                )
        return snapshot.payload, snapshot.generation

    @staticmethod
    def _require_identifier(payload: bytes, identifier: bytes, label: str) -> None:
        if len(payload) < 8 or payload[4:8] != identifier:
            raise RuntimeError(f"invalid {label} snapshot identifier")

    @staticmethod
    def _text(value: bytes | None) -> str | None:
        return None if value is None else value.decode("utf-8")

    @staticmethod
    def _decimal(value: Any) -> str | None:
        if value is None:
            return None
        mantissa = value.Mantissa()
        scale = value.Scale()
        sign = "-" if mantissa < 0 else ""
        digits = str(abs(mantissa)).rjust(scale + 1, "0")
        return (
            f"{sign}{digits}"
            if scale == 0
            else f"{sign}{digits[:-scale]}.{digits[-scale:]}"
        )

    def _decode_table_collection(self, table: Any, view: str) -> list[dict[str, Any]]:
        field = view.replace("-", "_")
        method_name = "".join(part.title() for part in field.split("_"))
        length = getattr(table, f"{method_name}Length")()
        getter = getattr(table, method_name)
        return [
            self._decode_reference_record(getter(index))
            for index in range(length)
            if getter(index) is not None
        ]

    @staticmethod
    def _decode_reference_record(value: Any) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in dir(value):
            if not name or not name[0].isupper() or name.endswith("Length"):
                continue
            method = getattr(value, name)
            if not callable(method):
                continue
            try:
                field = method()
            except TypeError:
                continue
            if isinstance(field, bytes):
                field = field.decode("utf-8")
            result[name[0].lower() + name[1:]] = field
        return result


__all__ = ["ReferenceSnapshotClient"]
