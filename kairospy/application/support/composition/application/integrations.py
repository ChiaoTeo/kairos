"""Composition helpers for single-link Integration connections."""

from __future__ import annotations

from collections.abc import Mapping

from kairospy.application.usecases.market.domain.subscriptions import MarketDataSubscriptionSpec
from kairospy.infrastructure.integrations.application.assembly import DefaultIntegrationConnectionApplication
from kairospy.infrastructure.integrations.application.connections import (
    IntegrationConnection,
    IntegrationConnectionApplication,
    IntegrationConnectionSpec,
    RuntimeMode,
)
from kairospy.infrastructure.integrations.application.market import MarketDataConnection, MarketStreamConnection
from kairospy.infrastructure.integrations.domain import (
    AccessScope,
    BrokerId,
    BrokerRef,
    CredentialRef,
    ExchangeId,
    ExchangeRef,
    ParticipantRef,
    ProductFamily,
    TransportKind,
)


def integration_application() -> IntegrationConnectionApplication:
    return DefaultIntegrationConnectionApplication()


def connect_binance_spot(
    connection_id: str,
    *,
    credential: str | None = None,
    market: bool = False,
    account: bool = False,
    execution: bool = False,
    transport: TransportKind | None = None,
    mode: RuntimeMode = RuntimeMode.LIVE,
) -> IntegrationConnection:
    """Connect one Binance Spot link.

    The boolean arguments remain only as a short-lived composition convenience;
    they select one link and never combine routes into one connection.
    """

    del execution
    if sum((market, account)) != 1:
        raise ValueError("choose exactly one Binance Spot link purpose")
    if market:
        participant = ExchangeRef(ExchangeId.BINANCE).participant
        access = AccessScope.PUBLIC
        selected_transport = transport or TransportKind.REST
    else:
        participant = BrokerRef(BrokerId.BINANCE).participant
        access = AccessScope.PRIVATE
        selected_transport = transport or TransportKind.REST
    return integration_application().connect(
        IntegrationConnectionSpec(
            connection_id=connection_id,
            participant=participant,
            product=ProductFamily.SPOT,
            access=access,
            transport=selected_transport,
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
            participant=ExchangeRef(ExchangeId.BINANCE).participant,
            product=ProductFamily.EQUITY,
            access=AccessScope.PUBLIC,
            transport=transport,
            credential=CredentialRef(credential) if credential else None,
            mode=mode,
        )
    )


def market_stream_connections(
    feeds: Mapping[str, object],
    *,
    mode_label: str,
    application: IntegrationConnectionApplication | None = None,
) -> Mapping[str, MarketStreamConnection]:
    del application
    result: dict[str, MarketStreamConnection] = {}
    for feed_id, feed in feeds.items():
        values = dict(getattr(feed, "values", None) or {})
        venue = str(values.get("venue") or getattr(feed, "feed_id", feed_id)).lower()
        if venue != ExchangeId.BINANCE.value:
            raise ValueError(f"unsupported Binance Spot market feed venue: {venue}")
        product = str(values.get("market") or values.get("product") or "spot").lower()
        mode = RuntimeMode(mode_label) if mode_label in {item.value for item in RuntimeMode} else RuntimeMode.LIVE
        if product == ProductFamily.EQUITY.value:
            connection = connect_binance_equity(
                str(values.get("connection_id") or f"{mode_label}.market.stream.{feed_id}"),
                credential=str(values["credential"]) if values.get("credential") is not None else None,
                mode=mode,
            )
        else:
            connection = connect_binance_spot(
                str(values.get("connection_id") or f"{mode_label}.market.stream.{feed_id}"),
                market=True,
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
    if venue != ExchangeId.BINANCE.value:
        raise error_type(f"unsupported Binance market feed venue: {venue}")
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
        connection = connect_binance_spot(connection_id, market=True, transport=TransportKind.MARKET_STREAM, mode=RuntimeMode.LIVE)
    return connection  # type: ignore[return-value]


def market_request_connections(
    feeds: Mapping[str, object],
    *,
    mode_label: str,
    application: IntegrationConnectionApplication | None = None,
) -> Mapping[str, object]:
    del application
    result: dict[str, IntegrationConnection] = {}
    for feed_id, feed in feeds.items():
        values = dict(getattr(feed, "values", None) or {})
        venue = str(values.get("venue") or getattr(feed, "feed_id", feed_id)).lower()
        if venue != ExchangeId.BINANCE.value:
            raise ValueError(f"unsupported Binance market feed venue: {venue}")
        mode = RuntimeMode(mode_label) if mode_label in {item.value for item in RuntimeMode} else RuntimeMode.LIVE
        product = str(values.get("market") or values.get("product") or "spot").lower()
        if product == ProductFamily.EQUITY.value:
            connection = connect_binance_equity(
                str(values.get("connection_id") or f"{mode_label}.market.request.{feed_id}"),
                credential=str(values["credential"]) if values.get("credential") is not None else None,
                transport=TransportKind.REST,
                mode=mode,
            )
        else:
            connection = connect_binance_spot(
                str(values.get("connection_id") or f"{mode_label}.market.request.{feed_id}"),
                market=True,
                transport=TransportKind.REST,
                mode=mode,
            )
        result[connection.identity.connection_id] = connection
    return result


__all__ = ["configured_market_feed_for_subscription", "connect_binance_equity", "connect_binance_spot", "integration_application", "market_request_connections", "market_stream_connections"]
