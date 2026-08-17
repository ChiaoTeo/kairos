"""Reference v2 control commands and typed mmap queries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .view import ReferenceViewKey, ReferenceViewReader, decimal, lifecycle, text


@dataclass(frozen=True, slots=True)
class ReferenceClient:
    """Compose the v2 control client with the typed mmap data plane."""

    socket_path: Path | None = None
    view_root: Path | None = None
    actor_id: str = "reference-actor"
    timeout: float = 5.0

    def _control(self):
        if self.socket_path is None:
            raise RuntimeError("Reference control socket is not configured")
        from .control import ReferenceControlClient

        return ReferenceControlClient(self.socket_path, timeout=self.timeout)

    def _view(self):
        if self.view_root is None:
            raise RuntimeError("Reference mmap view root is not configured")
        return ReferenceViewReader(
            self.view_root,
            key=ReferenceViewKey(self.actor_id),
        ).read()

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        timeout: float | None = None,
        **params: object,
    ) -> dict[str, Any]:
        if self.socket_path is None:
            raise RuntimeError("Reference control socket is not configured")
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
        frame = self._view()
        state = frame.value.State()
        return {
            "generation": frame.generation,
            "event_sequence": frame.event_sequence,
            "providers": [
                {
                    "source_id": text(state.ProviderHealth(index).ProviderId()),
                    "status": text(state.ProviderHealth(index).Status()),
                    "message": text(state.ProviderHealth(index).Message()),
                    "updated_at_unix_nanos": int(
                        state.ProviderHealth(index).UpdatedAtUnixNanos()
                    ),
                }
                for index in range(state.ProviderHealthLength())
            ],
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
        frame = self._view()
        state = frame.value.State()
        values = []
        for index in range(state.LifecycleEventsLength()):
            value = state.LifecycleEvents(index)
            event_id = text(value.EventId()) or ""
            sequence = int(event_id.rsplit(":", 1)[-1]) if ":" in event_id else 0
            if sequence_from is not None and sequence < sequence_from:
                continue
            if sequence_to is not None and sequence > sequence_to:
                continue
            values.append(
                {
                    "event_id": event_id,
                    "event_type": text(value.EventType()),
                    "event_time_unix_nanos": int(value.EventTimeUnixNanos()),
                    "record_kind": text(value.RecordKind()),
                    "record_id": text(value.RecordId()),
                }
            )
            if len(values) >= limit:
                break
        return {
            "generation": frame.generation,
            "event_sequence": frame.event_sequence,
            "events": values,
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
        frame = self._view()
        state = frame.value.State()
        return {
            "source_id": "massive-options",
            "generation": frame.generation,
            "event_sequence": frame.event_sequence,
            "underlyings": [
                text(state.OptionUnderlyings(index))
                for index in range(state.OptionUnderlyingsLength())
            ],
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
        frame = self._view()
        state = frame.value.State()
        markets = [state.Markets(index) for index in range(state.MarketsLength())]
        return {
            "generation": frame.generation,
            "event_sequence": frame.event_sequence,
            "catalog": {
                "entity_count": state.EntitiesLength(),
                "asset_count": state.AssetsLength(),
                "instrument_count": state.InstrumentsLength(),
                "listing_count": state.ListingsLength(),
                "market_count": state.MarketsLength(),
                "financial_product_count": state.FinancialProductsLength(),
                "execution_access_count": state.ExecutionAccessesLength(),
                "market_data_access_count": state.MarketDataAccessesLength(),
                "active_market_count": sum(
                    lifecycle(int(value.Status())) in {"active", "trading"}
                    for value in markets
                ),
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
        state = self._view().value.State()
        result = []
        for index in range(state.MarketsLength()):
            value = _market(state.Markets(index))
            if symbol is not None and value["source_symbol"] != symbol:
                continue
            if exchange_id is not None and value["exchange_id"] != exchange_id:
                continue
            if market_type is not None and value["market_type"] != market_type:
                continue
            if asset_type is not None and value["asset_type"] != asset_type:
                continue
            if status is not None and value["status"] != status:
                continue
            if active_only and value["status"] not in {"active", "trading"}:
                continue
            result.append(value)
            if len(result) >= max(1, min(limit or 10_000, 10_000)):
                break
        return result

    def execution_accesses(self, **filters: object) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for value in self.collection("execution-accesses"):
            if (
                filters.get("provider_id") is not None
                and value.get("providerId") != filters["provider_id"]
            ):
                continue
            if (
                filters.get("product_family") is not None
                and value.get("productFamily") != filters["product_family"]
            ):
                continue
            if (
                filters.get("provider_symbol") is not None
                and value.get("providerSymbol") != filters["provider_symbol"]
            ):
                continue
            if (
                filters.get("status") is not None
                and value.get("status") != filters["status"]
            ):
                continue
            if filters.get("active_only") and value.get("status") != "active":
                continue
            result.append(value)
            if filters.get("limit") is not None and len(result) >= int(
                filters["limit"]
            ):
                break
        return result

    def collection(self, name: str) -> list[dict[str, Any]]:
        state = self._view().value.State()
        specs = {
            "entities": (state.EntitiesLength, state.Entities, _entity),
            "assets": (state.AssetsLength, state.Assets, _asset),
            "instruments": (state.InstrumentsLength, state.Instruments, _instrument),
            "listings": (state.ListingsLength, state.Listings, _listing),
            "financial-products": (
                state.FinancialProductsLength,
                state.FinancialProducts,
                _financial_product,
            ),
            "execution-accesses": (
                state.ExecutionAccessesLength,
                state.ExecutionAccesses,
                _execution_access,
            ),
        }
        spec = specs.get(name)
        if spec is None:
            raise ValueError(f"unsupported Reference collection: {name}")
        length, item, mapper = spec
        return [mapper(item(index)) for index in range(length())]

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

def _market(value: Any) -> dict[str, Any]:
    return {
        "market_id": text(value.MarketId()),
        "market_key": text(value.MarketKey()),
        "instrument_id": text(value.InstrumentId()),
        "listing_id": text(value.ListingId()),
        "exchange_id": text(value.ExchangeId()),
        "market_type": text(value.MarketType()),
        "asset_type": text(value.AssetType()),
        "source_symbol": text(value.SourceSymbol()),
        "symbol": text(value.SourceSymbol()),
        "base_asset_id": text(value.BaseAssetId()),
        "quote_asset_id": text(value.QuoteAssetId()),
        "base_asset": text(value.BaseAssetId()),
        "quote_asset": text(value.QuoteAssetId()),
        "underlying_instrument_id": text(value.UnderlyingInstrumentId()),
        "status": lifecycle(int(value.Status())),
        "price_tick": decimal(value.PriceTick()),
        "quantity_tick": decimal(value.QuantityTick()),
        "price_increment": decimal(value.PriceTick()),
        "quantity_increment": decimal(value.QuantityTick()),
        "minimum_quantity": decimal(value.MinimumQuantity()),
        "minimum_notional": decimal(value.MinimumNotional()),
        "contract_size": decimal(value.ContractSize()),
        "contract_multiplier": decimal(value.ContractSize()),
    }


def _entity(value: Any) -> dict[str, Any]:
    return {
        "entityId": text(value.EntityId()),
        "entityType": text(value.EntityType()),
        "name": text(value.Name()),
        "status": lifecycle(int(value.Status())),
    }


def _asset(value: Any) -> dict[str, Any]:
    return {
        "assetId": text(value.AssetId()),
        "code": text(value.Code()),
        "name": text(value.Name()),
        "assetClass": text(value.AssetClass()),
        "status": lifecycle(int(value.Status())),
    }


def _instrument(value: Any) -> dict[str, Any]:
    return {
        "instrumentId": text(value.InstrumentId()),
        "symbol": text(value.Symbol()),
        "name": text(value.Name()),
        "instrumentType": text(value.InstrumentType()),
        "underlyingInstrumentId": text(value.UnderlyingInstrumentId()),
        "expiryUnixNanos": int(value.ExpiryUnixNanos()) or None,
        "strike": decimal(value.Strike()),
        "optionRight": text(value.OptionRight()),
        "status": lifecycle(int(value.Status())),
    }


def _listing(value: Any) -> dict[str, Any]:
    return {
        "listingId": text(value.ListingId()),
        "instrumentId": text(value.InstrumentId()),
        "exchangeId": text(value.ExchangeId()),
        "exchangeSymbol": text(value.ExchangeSymbol()),
        "status": lifecycle(int(value.Status())),
    }


def _financial_product(value: Any) -> dict[str, Any]:
    return {
        "productId": text(value.ProductId()),
        "productType": text(value.ProductType()),
        "name": text(value.Name()),
        "assetId": text(value.AssetId()),
        "providerProductId": text(value.ProviderProductId()),
        "providerId": text(value.ProviderId()),
        "status": lifecycle(int(value.Status())),
    }


def _execution_access(value: Any) -> dict[str, Any]:
    return {
        "accessId": text(value.AccessId()),
        "routingMode": text(value.RoutingMode()),
        "instrumentId": text(value.InstrumentId()),
        "listingId": text(value.ListingId()),
        "marketId": text(value.MarketId()),
        "destinationMarketId": text(value.DestinationMarketId()),
        "brokerId": text(value.BrokerId()),
        "providerId": text(value.ProviderId()),
        "productFamily": text(value.ProductFamily()),
        "providerSymbol": text(value.ProviderSymbol()),
        "settlementAssetId": text(value.SettlementAssetId()),
        "status": lifecycle(int(value.Status())),
    }


__all__ = ["ReferenceClient"]
