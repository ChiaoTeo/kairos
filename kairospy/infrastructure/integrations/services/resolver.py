from __future__ import annotations

from dataclasses import dataclass

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
from kairospy.infrastructure.integrations.connectors.provider.massive import Massive
from kairospy.infrastructure.integrations.drivers import CcxtDriver, MassiveDriver
from kairospy.infrastructure.integrations.domain import ParticipantRef, ProductLine, integration_key
from kairospy.infrastructure.integrations.domain.bindings import ParticipantKind, ReferenceSourceRef
from kairospy.infrastructure.integrations.protocols import (
    AccountBalanceClient,
    AccountBootstrapClient,
    RawMarketDataGateway,
    OrderExecutionClient,
    OrderQueryClient,
    PrivateAccountStream,
    RawReferenceGateway,
)


@dataclass(frozen=True, slots=True)
class IntegrationResolver:
    """Central connector resolver for infrastructure integrations."""

    def market_feed(self, venue: str, *, mode_label: str = "runtime", error_type: type[Exception] = ValueError) -> RawMarketDataGateway:
        participant = ParticipantRef("exchange", venue)
        if participant.name == "binance":
            return BinanceMarketDataConnector(CcxtDriver())
        if participant.name == "hyperliquid":
            return HyperliquidMarketDataConnector(CcxtDriver())
        if participant.name in {"okx", "okex"}:
            return OkxMarketDataConnector()
        raise error_type(f"unsupported {mode_label} market data venue: {venue}")

    def market_feed_for_market(
        self,
        venue: str,
        market: str,
        *,
        credential: str | None = None,
        mode_label: str = "runtime",
        error_type: type[Exception] = ValueError,
    ) -> RawMarketDataGateway:
        participant = ParticipantRef("exchange", venue)
        product_line = ProductLine(market)
        if participant.name == "binance" and product_line.value == "equity":
            return BinanceEquityMarketDataConnector.from_credential(credential)
        return self.market_feed(participant.name, mode_label=mode_label, error_type=error_type)

    def broker(self, broker_name: str, credential: str | None = None, *, mode_label: str = "runtime", error_type: type[Exception] = ValueError) -> AccountBootstrapClient:
        participant = ParticipantRef("broker", broker_name)
        if participant.name == "binance":
            return BinanceBroker.from_credential(credential)
        if participant.name in {"okx", "okex"}:
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
        broker = ParticipantRef("broker", book.broker)
        account_book = ProductLine(book.book)
        if broker.name == "binance" and account_book.value == "equity":
            return BinanceEquityBroker.from_credential(credential)
        return self.broker(broker.name, credential, mode_label=mode_label, error_type=error_type)

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
        broker = ParticipantRef("broker", book.broker)
        account_book = ProductLine(book.book)
        if broker.name == "binance" and account_book.value == "equity":
            raise error_type(f"unsupported {mode_label} order execution book: {book.value}")
        return self.broker(broker.name, credential, mode_label=mode_label, error_type=error_type)

    def private_account_stream_for_book(
        self,
        book: AccountBookRef,
        credential: str | None = None,
        *,
        mode_label: str = "runtime",
        error_type: type[Exception] = ValueError,
    ) -> PrivateAccountStream:
        broker = ParticipantRef("broker", book.broker)
        account_book = ProductLine(book.book)
        if broker.name == "binance" and account_book.value == "equity":
            raise error_type(f"unsupported {mode_label} private account stream book: {book.value}")
        return self.broker(broker.name, credential, mode_label=mode_label, error_type=error_type)

    def reference_data(self, source: ReferenceSourceRef, *, error_type: type[Exception] = ValueError) -> RawReferenceGateway:
        kind = source.kind
        name = source.name
        market = source.market
        book = source.book
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
    return integration_key(value)


__all__ = [
    "DEFAULT_INTEGRATION_RESOLVER",
    "IntegrationResolver",
    "ParticipantKind",
    "ReferenceSourceRef",
]
