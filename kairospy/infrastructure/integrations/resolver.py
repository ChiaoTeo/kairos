from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from kairospy.application.ports import MarketDataSubscriptionSpec
from kairospy.core.account import AccountBookRef
from kairospy.infrastructure.integrations.connectors.broker.binance import (
    BinanceBroker,
    BinanceEquityBroker,
    BinanceEquityMarketDataConnector,
    BinanceEquityReferenceConnector,
)
from kairospy.infrastructure.integrations.connectors.broker.okx import OkxBroker
from kairospy.infrastructure.integrations.connectors.exchange.binance import BinanceMarketDataConnector
from kairospy.infrastructure.integrations.connectors.exchange.hyperliquid import HyperliquidMarketDataConnector
from kairospy.infrastructure.integrations.connectors.exchange.okx import OkxMarketDataConnector
from kairospy.infrastructure.integrations.connectors.provider import Massive
from kairospy.infrastructure.integrations.drivers import CcxtDriver, MassiveDriver
from kairospy.infrastructure.integrations.protocols import (
    AccountBalanceClient,
    AccountBootstrapClient,
    RawMarketDataGateway,
    OrderExecutionClient,
    OrderQueryClient,
    PrivateAccountStream,
    RawReferenceGateway,
)


ParticipantKind = Literal["exchange", "broker", "provider"]


@dataclass(frozen=True, slots=True)
class ReferenceSourceRef:
    kind: ParticipantKind
    name: str
    market: str | None = None
    book: str | None = None


@dataclass(frozen=True, slots=True)
class IntegrationResolver:
    """Central connector resolver for infrastructure integrations."""

    def market_feed(self, venue: str, *, mode_label: str = "runtime", error_type: type[Exception] = ValueError) -> RawMarketDataGateway:
        normalized = _key(venue)
        if normalized == "binance":
            return BinanceMarketDataConnector(CcxtDriver())
        if normalized == "hyperliquid":
            return HyperliquidMarketDataConnector(CcxtDriver())
        if normalized in {"okx", "okex"}:
            return OkxMarketDataConnector()
        raise error_type(f"unsupported {mode_label} market data venue: {venue}")

    def market_feed_for_subscription(
        self,
        spec: MarketDataSubscriptionSpec,
        *,
        credential: str | None = None,
        mode_label: str = "runtime",
        error_type: type[Exception] = ValueError,
    ) -> RawMarketDataGateway:
        venue = _key(str(spec.market.venue))
        market = _key(str(spec.market.market))
        if venue == "binance" and market == "equity":
            return BinanceEquityMarketDataConnector.from_credential(credential)
        return self.market_feed(venue, mode_label=mode_label, error_type=error_type)

    def broker(self, broker_name: str, credential: str | None = None, *, mode_label: str = "runtime", error_type: type[Exception] = ValueError) -> AccountBootstrapClient:
        normalized = _key(broker_name)
        if normalized == "binance":
            return BinanceBroker.from_credential(credential)
        if normalized in {"okx", "okex"}:
            return OkxBroker.from_credential(credential)
        raise error_type(f"unsupported {mode_label} broker: {broker_name}")

    def broker_for_book(
        self,
        book: AccountBookRef,
        credential: str | None = None,
        *,
        mode_label: str = "runtime",
        error_type: type[Exception] = ValueError,
    ) -> AccountBootstrapClient:
        broker = _key(str(book.broker))
        account_book = _key(str(book.book))
        if broker == "binance" and account_book == "equity":
            return BinanceEquityBroker.from_credential(credential)
        return self.broker(broker, credential, mode_label=mode_label, error_type=error_type)

    def account_balance_for_book(
        self,
        book: AccountBookRef,
        credential: str | None = None,
        *,
        mode_label: str = "runtime",
        error_type: type[Exception] = ValueError,
    ) -> AccountBalanceClient:
        return self.broker_for_book(book, credential, mode_label=mode_label, error_type=error_type)

    def order_query_for_book(
        self,
        book: AccountBookRef,
        credential: str | None = None,
        *,
        mode_label: str = "runtime",
        error_type: type[Exception] = ValueError,
    ) -> OrderQueryClient:
        return self.broker_for_book(book, credential, mode_label=mode_label, error_type=error_type)

    def account_bootstrap_for_book(
        self,
        book: AccountBookRef,
        credential: str | None = None,
        *,
        mode_label: str = "runtime",
        error_type: type[Exception] = ValueError,
    ) -> AccountBootstrapClient:
        return self.broker_for_book(book, credential, mode_label=mode_label, error_type=error_type)

    def order_execution_for_book(
        self,
        book: AccountBookRef,
        credential: str | None = None,
        *,
        mode_label: str = "runtime",
        error_type: type[Exception] = ValueError,
    ) -> OrderExecutionClient:
        broker = _key(str(book.broker))
        account_book = _key(str(book.book))
        if broker == "binance" and account_book == "equity":
            raise error_type(f"unsupported {mode_label} order execution book: {book.value}")
        return self.broker(broker, credential, mode_label=mode_label, error_type=error_type)

    def private_account_stream_for_book(
        self,
        book: AccountBookRef,
        credential: str | None = None,
        *,
        mode_label: str = "runtime",
        error_type: type[Exception] = ValueError,
    ) -> PrivateAccountStream:
        broker = _key(str(book.broker))
        account_book = _key(str(book.book))
        if broker == "binance" and account_book == "equity":
            raise error_type(f"unsupported {mode_label} private account stream book: {book.value}")
        return self.broker(broker, credential, mode_label=mode_label, error_type=error_type)

    def reference_data(self, source: ReferenceSourceRef, *, error_type: type[Exception] = ValueError) -> RawReferenceGateway:
        kind = source.kind
        name = _key(source.name)
        market = None if source.market is None else _key(source.market)
        book = None if source.book is None else _key(source.book)
        if kind == "exchange":
            if name == "binance":
                return BinanceMarketDataConnector(CcxtDriver())
            if name == "hyperliquid":
                return HyperliquidMarketDataConnector(CcxtDriver())
            if name in {"okx", "okex"}:
                return OkxMarketDataConnector()
        if kind == "broker":
            if name == "binance" and (book == "equity" or market == "equity"):
                return BinanceEquityReferenceConnector()
        if kind == "provider":
            if name == "massive":
                return Massive(MassiveDriver())
        raise error_type(f"unsupported reference source: {kind}:{source.name}")

    def provider(self, provider_name: str, *, error_type: type[Exception] = ValueError) -> RawReferenceGateway:
        return self.reference_data(ReferenceSourceRef("provider", provider_name), error_type=error_type)


DEFAULT_INTEGRATION_RESOLVER = IntegrationResolver()


def _key(value: str) -> str:
    return value.strip().lower().replace("-", "_")


__all__ = [
    "DEFAULT_INTEGRATION_RESOLVER",
    "IntegrationResolver",
    "ParticipantKind",
    "ReferenceSourceRef",
]
