"""Composition helpers for single-link Integration connections."""

from __future__ import annotations

from collections.abc import Mapping

from kairospy.application.usecases.market.domain.subscriptions import MarketDataSubscriptionSpec
from kairospy.infrastructure.integrations.application.connections import (
    IntegrationConnection,
    IntegrationConnectionApplication,
    IntegrationConnectionSpec,
    RuntimeMode,
)
from kairospy.application.usecases.market.application.feed import MarketStreamConnection
from kairospy.infrastructure.integrations.domain import (
    AccessScope,
    BrokerId,
    BrokerRef,
    CredentialRef,
    ExchangeId,
    ExchangeRef,
    IntegrationCapability,
    ProductFamily,
    ProviderId,
    ProviderRef,
    TransportKind,
    IntegrationRoute,
)


def integration_application() -> IntegrationConnectionApplication:
    from kairospy.infrastructure.integrations.application.assembly import DefaultIntegrationConnectionApplication

    return DefaultIntegrationConnectionApplication()


def market_integration_runtime(
    scope: object,
    *,
    application: IntegrationConnectionApplication | None = None,
    mode: RuntimeMode = RuntimeMode.LIVE,
) -> object:
    """Build the one runtime factory; connections are created only on demand."""
    from kairospy.infrastructure.integrations.application.market_runtime import SystemMarketIntegrationRuntime

    return SystemMarketIntegrationRuntime(
        scope=scope,  # type: ignore[arg-type]
        application=application or integration_application(),
        mode=mode,
    )


def connect_binance_spot_public(
    connection_id: str,
    *,
    credential: str | None = None,
    transport: TransportKind = TransportKind.REST,
    mode: RuntimeMode = RuntimeMode.LIVE,
) -> IntegrationConnection:
    return _connect_binance_spot(
        connection_id,
        route=IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE)),
        access=AccessScope.PUBLIC,
        capability=IntegrationCapability.MARKET_STREAM if transport is TransportKind.MARKET_STREAM else IntegrationCapability.MARKET_DATA,
        credential=credential,
        transport=transport,
        mode=mode,
    )


def connect_binance_spot_account(
    connection_id: str,
    *,
    credential: str | None = None,
    transport: TransportKind = TransportKind.REST,
    mode: RuntimeMode = RuntimeMode.LIVE,
) -> IntegrationConnection:
    return _connect_binance_spot(
        connection_id,
        route=IntegrationRoute(broker=BrokerRef(BrokerId.BINANCE)),
        access=AccessScope.PRIVATE,
        capability=IntegrationCapability.ACCOUNT_STREAM if transport is TransportKind.USER_STREAM else IntegrationCapability.ACCOUNT_READ,
        credential=credential,
        transport=transport,
        mode=mode,
    )


def connect_binance_spot_execution(
    connection_id: str,
    *,
    credential: str | None = None,
    transport: TransportKind = TransportKind.REST,
    mode: RuntimeMode = RuntimeMode.LIVE,
) -> IntegrationConnection:
    return _connect_binance_spot(
        connection_id,
        route=IntegrationRoute(broker=BrokerRef(BrokerId.BINANCE)),
        access=AccessScope.PRIVATE,
        capability=IntegrationCapability.ORDER_ENTRY if transport is TransportKind.REST else IntegrationCapability.EXECUTION_STREAM,
        credential=credential,
        transport=transport,
        mode=mode,
    )


def _connect_binance_spot(
    connection_id: str,
    *,
    route: IntegrationRoute,
    access: AccessScope,
    capability: IntegrationCapability,
    credential: str | None,
    transport: TransportKind,
    mode: RuntimeMode,
) -> IntegrationConnection:
    return integration_application().connect(
        IntegrationConnectionSpec(
            connection_id=connection_id,
            route=route,
            product=ProductFamily.SPOT,
            access=access,
            transport=transport,
            capability=capability,
            credential=CredentialRef(credential) if credential else None,
            mode=mode,
        )
    )


def connect_binance_equity(
    connection_id: str,
    *,
    credential: str | None = None,
    transport: TransportKind = TransportKind.MARKET_STREAM,
    mode: RuntimeMode = RuntimeMode.LIVE,
) -> IntegrationConnection:
    return integration_application().connect(
        IntegrationConnectionSpec(
            connection_id=connection_id,
            route=IntegrationRoute(exchange=ExchangeRef(ExchangeId.BINANCE)),
            product=ProductFamily.EQUITY,
            access=AccessScope.PUBLIC,
            transport=transport,
            credential=CredentialRef(credential) if credential else None,
            mode=mode,
        )
    )


def connect_massive_stocks(
    connection_id: str,
    *,
    credential: str | None = None,
    transport: TransportKind = TransportKind.MARKET_STREAM,
    mode: RuntimeMode = RuntimeMode.LIVE,
) -> IntegrationConnection:
    return integration_application().connect(
        IntegrationConnectionSpec(
            connection_id=connection_id,
            route=IntegrationRoute(provider=ProviderRef(ProviderId.MASSIVE)),
            product=ProductFamily.EQUITY,
            access=AccessScope.PUBLIC,
            transport=transport,
            credential=CredentialRef(credential) if credential else None,
            mode=mode,
        )
    )


def connect_massive_options(
    connection_id: str,
    *,
    credential: str | None = None,
    transport: TransportKind = TransportKind.MARKET_STREAM,
    mode: RuntimeMode = RuntimeMode.LIVE,
) -> IntegrationConnection:
    return integration_application().connect(
        IntegrationConnectionSpec(
            connection_id=connection_id,
            route=IntegrationRoute(provider=ProviderRef(ProviderId.MASSIVE)),
            product=ProductFamily.OPTIONS,
            access=AccessScope.PUBLIC,
            transport=transport,
            credential=CredentialRef(credential) if credential else None,
            mode=mode,
        )
    )


def connect_ibkr(
    connection_id: str,
    *,
    credential: str | None = None,
    transport: TransportKind = TransportKind.REQUEST_API,
    mode: RuntimeMode = RuntimeMode.PAPER,
) -> IntegrationConnection:
    return integration_application().connect(
        IntegrationConnectionSpec(
            connection_id=connection_id,
            route=IntegrationRoute(broker=BrokerRef(BrokerId.IBKR)),
            product=ProductFamily.EQUITY,
            access=AccessScope.PRIVATE,
            transport=transport,
            credential=CredentialRef(credential) if credential else None,
            mode=mode,
        )
    )


def market_stream_connections(
    feeds: Mapping[str, object],
    *,
    mode_label: str,
    default_credential: str | None = None,
    application: IntegrationConnectionApplication | None = None,
) -> Mapping[str, MarketStreamConnection]:
    del application
    result: dict[str, MarketStreamConnection] = {}
    for feed_id, feed in feeds.items():
        values = dict(getattr(feed, "values", None) or {})
        venue = str(values.get("venue") or getattr(feed, "feed_id", feed_id)).lower()
        if venue == ProviderId.MASSIVE.value:
            connector = connect_massive_options if str(values.get("market") or values.get("product") or "equity").lower() in {"option", "options"} else connect_massive_stocks
            connection = connector(
                str(values.get("connection_id") or f"{mode_label}.market.stream.{feed_id}"),
                credential=str(values.get("credential") or default_credential) if values.get("credential") or default_credential else None,
                mode=mode,
            )
            result[connection.identity.connection_id] = connection  # type: ignore[assignment]
            continue
        if venue != ExchangeId.BINANCE.value:
            raise ValueError(f"unsupported market feed venue: {venue}")
        product = str(values.get("market") or values.get("product") or "spot").lower()
        mode = RuntimeMode(mode_label) if mode_label in {item.value for item in RuntimeMode} else RuntimeMode.LIVE
        if product == ProductFamily.EQUITY.value:
            connection = connect_binance_equity(
                str(values.get("connection_id") or f"{mode_label}.market.stream.{feed_id}"),
                credential=str(values.get("credential") or default_credential) if values.get("credential") or default_credential else None,
                mode=mode,
            )
        else:
            connection = connect_binance_spot_public(
                str(values.get("connection_id") or f"{mode_label}.market.stream.{feed_id}"),
                transport=TransportKind.MARKET_STREAM,
                mode=mode,
            )
        result[connection.identity.connection_id] = connection  # type: ignore[assignment]
    return result


def configured_market_feed_for_subscription(
    spec: MarketDataSubscriptionSpec,
    *,
    feeds: Mapping[str, object] | None = None,
    mode_label: str = "live",
    error_type: type[Exception] = ValueError,
    application: IntegrationConnectionApplication | None = None,
) -> MarketStreamConnection:
    del application
    venue = str(spec.market.venue).lower()
    if venue == ProviderId.MASSIVE.value:
        connection_id = str(spec.params.get("connection_id") or f"{mode_label}.market.stream.{venue}")
        credential = spec.params.get("credential")
        if feeds:
            for feed in feeds.values():
                values = dict(getattr(feed, "values", None) or {})
                if str(values.get("venue") or getattr(feed, "feed_id", "")).lower() == venue:
                    connection_id = str(values.get("connection_id") or connection_id)
                    credential = credential or values.get("credential")
                    break
        connector = connect_massive_options if str(spec.market.market).lower() in {"option", "options"} else connect_massive_stocks
        return connector(
            connection_id,
            credential=str(credential) if credential is not None else None,
            mode=RuntimeMode(mode_label),
        )  # type: ignore[return-value]
    if venue != ExchangeId.BINANCE.value:
        raise error_type(f"unsupported market feed venue: {venue}")
    connection_id = str(spec.params.get("connection_id") or f"{mode_label}.market.stream.{venue}")
    if feeds:
        for feed in feeds.values():
            values = dict(getattr(feed, "values", None) or {})
            if str(values.get("venue") or getattr(feed, "feed_id", "")).lower() == venue:
                connection_id = str(values.get("connection_id") or connection_id)
                if spec.params.get("credential") is None and values.get("credential") is not None:
                    spec = MarketDataSubscriptionSpec(spec.market, spec.selectors, identity=spec.identity, params={**spec.params, "credential": values["credential"]})
                break
    product = str(spec.market.market).lower()
    if product == ProductFamily.EQUITY.value:
        connection = connect_binance_equity(
            connection_id,
            credential=str(spec.params["credential"]) if spec.params.get("credential") is not None else None,
            mode=RuntimeMode.LIVE,
        )
    else:
        connection = connect_binance_spot_public(connection_id, transport=TransportKind.MARKET_STREAM, mode=RuntimeMode.LIVE)
    return connection  # type: ignore[return-value]


def market_request_connections(
    feeds: Mapping[str, object],
    *,
    mode_label: str,
    default_credential: str | None = None,
    application: IntegrationConnectionApplication | None = None,
) -> Mapping[str, object]:
    del application
    result: dict[str, IntegrationConnection] = {}
    for feed_id, feed in feeds.items():
        values = dict(getattr(feed, "values", None) or {})
        venue = str(values.get("venue") or getattr(feed, "feed_id", feed_id)).lower()
        if venue == ProviderId.MASSIVE.value:
            connection = connect_massive_stocks(
                str(values.get("connection_id") or f"{mode_label}.market.request.{feed_id}"),
                credential=str(values.get("credential") or default_credential) if values.get("credential") or default_credential else None,
                transport=TransportKind.MARKET_STREAM,
                mode=mode,
            )
            result[connection.identity.connection_id] = connection
            continue
        if venue != ExchangeId.BINANCE.value:
            raise ValueError(f"unsupported market request venue: {venue}")
        mode = RuntimeMode(mode_label) if mode_label in {item.value for item in RuntimeMode} else RuntimeMode.LIVE
        product = str(values.get("market") or values.get("product") or "spot").lower()
        if product == ProductFamily.EQUITY.value:
            connection = connect_binance_equity(
                str(values.get("connection_id") or f"{mode_label}.market.request.{feed_id}"),
                credential=str(values.get("credential") or default_credential) if values.get("credential") or default_credential else None,
                transport=TransportKind.REST,
                mode=mode,
            )
        else:
            connection = connect_binance_spot_public(
                str(values.get("connection_id") or f"{mode_label}.market.request.{feed_id}"),
                transport=TransportKind.REST,
                mode=mode,
            )
        result[connection.identity.connection_id] = connection
    return result


__all__ = ["configured_market_feed_for_subscription", "connect_binance_equity", "connect_binance_spot_account", "connect_binance_spot_execution", "connect_binance_spot_public", "connect_ibkr", "connect_massive_options", "connect_massive_stocks", "integration_application", "market_integration_runtime", "market_request_connections", "market_stream_connections"]
