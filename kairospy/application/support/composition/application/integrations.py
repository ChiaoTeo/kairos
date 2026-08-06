"""Composition helpers for single-link Integration connections."""

from __future__ import annotations

from collections.abc import Mapping

from kairospy.application.usecases.market.application.requests import MarketDataSubscriptionSpec
from kairospy.infrastructure.integrations.application.connections import (
    IntegrationConnection,
    IntegrationConnectionApplication,
    IntegrationConnectionSpec,
    RuntimeMode,
)
from kairospy.application.usecases.market.application.feed import MarketStreamConnection
from kairospy.infrastructure.integrations.domain import (
    AccessScope,
    AssetType,
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
    feed_configs: Mapping[str, object] | None = None,
) -> object:
    """Build the one runtime factory; connections are created only on demand."""
    from kairospy.infrastructure.integrations.application.market_runtime import SystemMarketIntegrationRuntime

    return SystemMarketIntegrationRuntime(
        scope=scope,  # type: ignore[arg-type]
        application=application or integration_application(),
        mode=mode,
        feed_configs=feed_configs,
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


def connect_binance_futures_public(
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
            product=ProductFamily.USD_M_FUTURES,
            access=AccessScope.PUBLIC,
            transport=transport,
            credential=CredentialRef(credential) if credential else None,
            mode=mode,
        )
    )


def connect_binance_options(
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
            product=ProductFamily.OPTIONS,
            access=AccessScope.PUBLIC,
            transport=transport,
            credential=CredentialRef(credential) if credential else None,
            mode=mode,
        )
    )


def connect_binance_options_account(
    connection_id: str,
    *,
    credential: str | None = None,
    mode: RuntimeMode = RuntimeMode.LIVE,
) -> IntegrationConnection:
    return _connect_binance_options_private(
        connection_id,
        capability=IntegrationCapability.ACCOUNT_READ,
        credential=credential,
        mode=mode,
    )


def connect_binance_options_execution(
    connection_id: str,
    *,
    credential: str | None = None,
    mode: RuntimeMode = RuntimeMode.LIVE,
) -> IntegrationConnection:
    return _connect_binance_options_private(
        connection_id,
        capability=IntegrationCapability.ORDER_ENTRY,
        credential=credential,
        mode=mode,
    )


def _connect_binance_options_private(
    connection_id: str,
    *,
    capability: IntegrationCapability,
    credential: str | None,
    mode: RuntimeMode,
) -> IntegrationConnection:
    return integration_application().connect(
        IntegrationConnectionSpec(
            connection_id=connection_id,
            route=IntegrationRoute(
                exchange=ExchangeRef(ExchangeId.BINANCE),
                broker=BrokerRef(BrokerId.BINANCE),
            ),
            product=ProductFamily.OPTIONS,
            access=AccessScope.PRIVATE,
            transport=TransportKind.REST,
            capability=capability,
            credential=CredentialRef(credential) if credential else None,
            mode=mode,
        )
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


def connect_binance_equity_execution(
    connection_id: str,
    *,
    credential: str | None = None,
    mode: RuntimeMode = RuntimeMode.LIVE,
) -> IntegrationConnection:
    return integration_application().connect(
        IntegrationConnectionSpec(
            connection_id=connection_id,
            route=IntegrationRoute(broker=BrokerRef(BrokerId.BINANCE)),
            product=ProductFamily.SPOT,
            asset_type=AssetType.EQUITY,
            access=AccessScope.PRIVATE,
            transport=TransportKind.REST,
            capability=IntegrationCapability.ORDER_ENTRY,
            credential=CredentialRef(credential) if credential else None,
            mode=mode,
        )
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
            product=ProductFamily.SPOT,
            asset_type=AssetType.EQUITY,
            access=AccessScope.PUBLIC,
            transport=transport,
            credential=CredentialRef(credential) if credential else None,
            mode=mode,
        )
    )


def connect_binance_earn(
    connection_id: str,
    *,
    credential: str,
    mode: RuntimeMode = RuntimeMode.LIVE,
) -> IntegrationConnection:
    return integration_application().connect(
        IntegrationConnectionSpec(
            connection_id=connection_id,
            route=IntegrationRoute(broker=BrokerRef(BrokerId.BINANCE)),
            product=None,
            access=AccessScope.PRIVATE,
            transport=TransportKind.REST,
            capability=IntegrationCapability.EARN,
            credential=CredentialRef(credential),
            mode=mode,
        )
    )


def connect_crypto_market(
    connection_id: str,
    *,
    venue: str,
    product: ProductFamily = ProductFamily.SPOT,
    credential: str | None = None,
    transport: TransportKind = TransportKind.MARKET_STREAM,
    mode: RuntimeMode = RuntimeMode.LIVE,
) -> IntegrationConnection:
    """Connect a CCXT-backed crypto market using the canonical venue name."""
    exchange = _canonical_crypto_exchange(venue)
    return integration_application().connect(
        IntegrationConnectionSpec(
            connection_id=connection_id,
            route=IntegrationRoute(exchange=ExchangeRef(exchange)),
            product=product,
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
            product=ProductFamily.SPOT,
            asset_type=AssetType.EQUITY,
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


def connect_massive_reference(
    connection_id: str,
    *,
    credential: str | None = None,
    mode: RuntimeMode = RuntimeMode.PAPER,
) -> IntegrationConnection:
    return integration_application().connect(
        IntegrationConnectionSpec(
            connection_id=connection_id,
            route=IntegrationRoute(provider=ProviderRef(ProviderId.MASSIVE)),
            product=None,
            access=AccessScope.PUBLIC,
            transport=TransportKind.REST,
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
    capability: IntegrationCapability = IntegrationCapability.ORDER_ENTRY,
) -> IntegrationConnection:
    return integration_application().connect(
        IntegrationConnectionSpec(
            connection_id=connection_id,
            route=IntegrationRoute(broker=BrokerRef(BrokerId.IBKR)),
            product=ProductFamily.SPOT,
            asset_type=AssetType.EQUITY,
            access=AccessScope.PRIVATE,
            transport=transport,
            credential=CredentialRef(credential) if credential else None,
            mode=mode,
            capability=capability,
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
        if not _is_crypto_exchange(venue):
            raise ValueError(f"unsupported market feed venue: {venue}")
        product = str(values.get("market") or values.get("product") or "spot").lower()
        mode = RuntimeMode(mode_label) if mode_label in {item.value for item in RuntimeMode} else RuntimeMode.LIVE
        if product == ProductFamily.OPTIONS.value and venue not in {ExchangeId.BINANCE.value}:
            raise ValueError(f"options market is not supported for venue: {venue}")
        connection = connect_crypto_market(
            str(values.get("connection_id") or f"{mode_label}.market.stream.{feed_id}"),
            venue=venue,
            product=_market_product(product),
            credential=str(values.get("credential") or default_credential) if values.get("credential") or default_credential else None,
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
    if not _is_crypto_exchange(venue):
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
    if product == ProductFamily.OPTIONS.value and venue not in {ExchangeId.BINANCE.value}:
        raise error_type(f"options market is not supported for venue: {venue}")
    connection = connect_crypto_market(
        connection_id,
        venue=venue,
        product=_market_product(product),
        credential=str(spec.params["credential"]) if spec.params.get("credential") is not None else None,
        mode=RuntimeMode.LIVE,
    )
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
        if not _is_crypto_exchange(venue):
            raise ValueError(f"unsupported market request venue: {venue}")
        mode = RuntimeMode(mode_label) if mode_label in {item.value for item in RuntimeMode} else RuntimeMode.LIVE
        product = str(values.get("market") or values.get("product") or "spot").lower()
        connection = connect_crypto_market(
            str(values.get("connection_id") or f"{mode_label}.market.request.{feed_id}"),
            venue=venue,
            product=_market_product(product),
            credential=str(values.get("credential") or default_credential) if values.get("credential") or default_credential else None,
            transport=TransportKind.REST,
            mode=mode,
        )
        result[connection.identity.connection_id] = connection
    return result


def _canonical_crypto_exchange(venue: str) -> ExchangeId:
    value = str(venue).strip().lower()
    if value in {"okex", "ouyi"}:
        value = ExchangeId.OKX.value
    if value not in {ExchangeId.BINANCE.value, ExchangeId.OKX.value, ExchangeId.HYPERLIQUID.value}:
        raise ValueError(f"unsupported crypto exchange: {venue}")
    return ExchangeId(value)


def _is_crypto_exchange(venue: str) -> bool:
    try:
        _canonical_crypto_exchange(venue)
    except ValueError:
        return False
    return True


def _market_product(value: str) -> ProductFamily:
    if value in {ProductFamily.OPTIONS.value, "option"}:
        return ProductFamily.OPTIONS
    if value in {ProductFamily.USD_M_FUTURES.value, "futures", "future", "swap", "perpetual", "perpetuals"}:
        return ProductFamily.USD_M_FUTURES
    if value == ProductFamily.COIN_M_FUTURES.value:
        return ProductFamily.COIN_M_FUTURES
    if value in {"equity", "stock", "stocks"}:
        return ProductFamily.SPOT
    return ProductFamily.SPOT


__all__ = ["configured_market_feed_for_subscription", "connect_binance_earn", "connect_binance_equity", "connect_binance_futures_public", "connect_binance_options", "connect_binance_options_account", "connect_binance_options_execution", "connect_binance_spot_account", "connect_binance_spot_execution", "connect_binance_spot_public", "connect_crypto_market", "connect_ibkr", "connect_massive_options", "connect_massive_reference", "connect_massive_stocks", "integration_application", "market_integration_runtime", "market_request_connections", "market_stream_connections"]
