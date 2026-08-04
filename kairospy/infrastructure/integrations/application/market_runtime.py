"""System-owned dynamic Market integration runtime.

This object is configured once during system assembly.  It creates typed
Market connections on demand and stores them in the System-owned connection
scope; composition never creates Market connections during a launch.
"""

from __future__ import annotations

from typing import Protocol

from kairospy.application.usecases.market.application.integration import (
    MarketDataConnection,
    MarketDataConnectionRequest,
    MarketStreamConnectionRequest,
    MarketStreamConnection,
)
from kairospy.infrastructure.integrations.application.connections import (
    IntegrationConnectionApplication,
    IntegrationConnectionSpec,
    RuntimeMode,
)
from kairospy.infrastructure.integrations.domain import (
    AccessScope,
    BrokerId,
    BrokerRef,
    CredentialRef,
    ExchangeId,
    ExchangeRef,
    ParticipantKind,
    ParticipantRef,
    ProductFamily,
    ProviderId,
    ProviderRef,
    TransportKind,
    AdapterRef,
    IntegrationRoute,
)


class ConnectionScope(Protocol):
    def get(self, key: str) -> object | None: ...
    def register(self, key: str, resource: object, *, role: str = "resource") -> object: ...
    def remove(self, key: str) -> object | None: ...
    def reconnect(self, key: str | None = None) -> object | None: ...


class SystemMarketIntegrationRuntime:
    def __init__(
        self,
        *,
        scope: ConnectionScope,
        application: IntegrationConnectionApplication,
        mode: RuntimeMode = RuntimeMode.LIVE,
    ) -> None:
        self.scope = scope
        self.application = application
        self.mode = mode

    def create_stream(self, request: MarketStreamConnectionRequest) -> MarketStreamConnection:
        connection_id = str(request.connection_id or self._connection_id(request))
        existing = self.scope.get(connection_id)
        if existing is not None:
            return _require_stream(existing, connection_id)
        spec = self._spec(request, connection_id, transport=TransportKind.MARKET_STREAM)
        connection = self.application.connect(spec)
        self.scope.register(connection_id, connection, role="market_stream")
        return _require_stream(connection, connection_id)

    def create_data(self, request: MarketDataConnectionRequest) -> MarketDataConnection:
        spec = request.spec
        params = {**dict(request.params), "product": spec.market or "spot", "exchange": spec.venue or ""}
        connection_id = str(request.connection_id or params.get("connection_id") or f"market.{params['exchange']}.{params['product']}.data")
        existing = self.scope.get(connection_id)
        if existing is not None:
            return _require_data(existing, connection_id)
        connection = self.application.connect(self._spec_values(params, connection_id, transport=TransportKind.REST))
        self.scope.register(connection_id, connection, role="market_data")
        return _require_data(connection, connection_id)

    def resolve_stream(self, connection_id: str) -> MarketStreamConnection | None:
        value = self.scope.get(connection_id)
        return None if value is None else _require_stream(value, connection_id)

    def remove(self, connection_id: str) -> None:
        self.scope.remove(connection_id)

    def reconnect(self, connection_id: str) -> MarketStreamConnection:
        value = self.scope.reconnect(connection_id)
        if value is None:
            raise LookupError(f"market connection is not registered: {connection_id}")
        return _require_stream(value, connection_id)

    def _spec(self, request: MarketStreamConnectionRequest, connection_id: str, *, transport: TransportKind) -> IntegrationConnectionSpec:
        return self._spec_values(
            {
                "exchange": request.market.venue,
                "product": request.market.market,
                "provider": request.provider,
                "adapter": request.adapter,
                "credential": request.credential,
            },
            connection_id,
            transport=transport,
        )

    def _spec_values(self, params: dict[str, object], connection_id: str, *, transport: TransportKind) -> IntegrationConnectionSpec:
        exchange_name = str(params.get("exchange") or "").lower()
        provider_name = str(params.get("provider") or (exchange_name if exchange_name == "massive" else "")).lower()
        # OKEX is the historical spelling still present in some user-facing
        # configurations; the integration identity is canonicalized to OKX.
        exchange_ref: ExchangeRef | None = None
        provider_ref: ProviderRef | None = None
        if provider_name:
            provider_ref = ProviderRef(ProviderId(provider_name))
        else:
            exchange = ExchangeId("okx" if exchange_name == "okex" else exchange_name)
            exchange_ref = ExchangeRef(exchange)
        broker = params.get("broker")
        broker_ref = None if broker is None else BrokerRef(BrokerId(str(broker).lower()))
        credential = params.get("credential")
        return IntegrationConnectionSpec(
            connection_id=connection_id,
            route=IntegrationRoute(
                exchange=exchange_ref,
                broker=broker_ref,
                provider=provider_ref,
                adapter=None if params.get("adapter") is None else AdapterRef(str(params["adapter"])),
            ),
            product=_product(params.get("product") or "spot"),
            access=AccessScope.PUBLIC,
            transport=transport,
            credential=None if credential is None else CredentialRef(str(credential)),
            mode=RuntimeMode(str(params.get("mode") or self.mode.value)),
        )

    @staticmethod
    def _connection_id(request: MarketStreamConnectionRequest, *, suffix: str = "stream") -> str:
        product = str(request.market.market).lower()
        exchange = str(request.market.venue).lower()
        return f"market.{exchange}.{product}.{suffix}"


def _product(value: object) -> ProductFamily:
    text = str(value).strip().lower()
    if text in {"swap", "perp", "perpetual", "future", "futures", "usd_margined_futures", "usd-margined-futures"}:
        return ProductFamily.USD_M_FUTURES
    if text in {"equity", "stock", "stocks"}:
        return ProductFamily.EQUITY
    if text in {"option", "options"}:
        return ProductFamily.OPTIONS
    return ProductFamily.SPOT


def _require_stream(value: object, connection_id: str) -> MarketStreamConnection:
    if not callable(getattr(value, "subscribe", None)) or not callable(getattr(value, "unsubscribe", None)):
        raise TypeError(f"integration connection is not a MarketStreamConnection: {connection_id}")
    return value  # type: ignore[return-value]


def _require_data(value: object, connection_id: str) -> MarketDataConnection:
    if not callable(getattr(value, "latest_quote", None)) or not callable(getattr(value, "bars", None)):
        raise TypeError(f"integration connection is not a MarketDataConnection: {connection_id}")
    return value  # type: ignore[return-value]


__all__ = ["SystemMarketIntegrationRuntime"]
