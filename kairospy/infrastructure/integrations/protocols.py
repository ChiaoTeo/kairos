from __future__ import annotations

from typing import AsyncIterator, Protocol

from kairospy.core.market import MarketEvent

from .types import IntegrationParams, OrderBookRecordStream, OrderSubmissionResponse, QuoteRecordStream, RawPayload, RawPayloadRows, RawPayloadStream, TradeRecordStream


class IntegrationParticipant(Protocol):
    name: str


class IntegrationAdapter(IntegrationParticipant, Protocol):
    """Compatibility name for registered integration participants."""


class RawMarketDataGateway(Protocol):
    def watch_ticker(
        self,
        symbol: str,
        *,
        params: IntegrationParams | None = None,
    ) -> QuoteRecordStream:
        ...

    def watch_ticker_updates(
        self,
        symbol: str,
        *,
        params: IntegrationParams | None = None,
    ) -> AsyncIterator[MarketEvent]:
        ...

    def watch_order_book(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> OrderBookRecordStream:
        ...

    def watch_order_book_updates(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> AsyncIterator[MarketEvent]:
        ...

    def watch_trades(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: IntegrationParams | None = None,
    ) -> TradeRecordStream:
        ...

    def watch_trades_updates(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: IntegrationParams | None = None,
    ) -> AsyncIterator[MarketEvent]:
        ...

    def watch_option_greeks(
        self,
        symbol: str,
        *,
        params: IntegrationParams | None = None,
    ) -> RawPayloadStream:
        ...


class RawReferenceGateway(Protocol):
    def fetch_markets(
        self,
        *,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        ...


class OrderExecutionClient(Protocol):
    def create_order(
        self,
        symbol: str,
        *,
        side: str,
        type: str,
        amount: object,
        price: object | None = None,
        params: IntegrationParams | None = None,
    ) -> OrderSubmissionResponse:
        ...

    def cancel_order(
        self,
        id: str,
        *,
        symbol: str | None = None,
        params: IntegrationParams | None = None,
    ) -> OrderSubmissionResponse:
        ...


class AccountBalanceClient(Protocol):
    def fetch_balance(self, *, params: IntegrationParams | None = None) -> RawPayload:
        ...


class OrderQueryClient(Protocol):
    def fetch_open_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        ...

    def fetch_closed_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        ...


class AccountBootstrapClient(AccountBalanceClient, OrderQueryClient, Protocol):
    """Capability required to construct an account snapshot from a broker."""


class PrivateAccountStream(Protocol):
    def watch_balance(
        self,
        *,
        params: IntegrationParams | None = None,
    ) -> RawPayloadStream:
        ...

    def watch_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayloadStream:
        ...

    def watch_my_trades(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayloadStream:
        ...


__all__ = [
    "AccountBalanceClient",
    "AccountBootstrapClient",
    "IntegrationAdapter",
    "IntegrationParticipant",
    "RawMarketDataGateway",
    "OrderExecutionClient",
    "OrderQueryClient",
    "PrivateAccountStream",
    "OrderBookRecordStream",
    "OrderSubmissionResponse",
    "QuoteRecordStream",
    "RawPayload",
    "RawPayloadRows",
    "RawPayloadStream",
    "TradeRecordStream",
    "RawReferenceGateway",
]
