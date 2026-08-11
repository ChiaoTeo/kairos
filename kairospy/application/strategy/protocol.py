from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol
from kairospy.strategy import StrategyLogger

from kairospy.strategy import (
    EventEnvelope,
    StrategyBase,
    StrategyContextProtocol,
    StrategyProtocol,
    PairArbitrageRequest,
    PortfolioRebalanceRequest,
    QuoteProvisioningRequest,
    QuoteRefreshRequest,
    SubscriptionRequest,
    TargetPositionRequest,
)
from .domain.messages import (
    CommandHandle,
    ContextRequest,
    MarketSubscriptionRequest,
    SnapshotEnvelope,
    StrategySignal,
    PairArbitrageRequest,
    PortfolioRebalanceRequest,
    TargetPositionRequest,
)


Strategy = StrategyProtocol


class ContextBus(Protocol):
    def submit(self, request: ContextRequest) -> CommandHandle: ...
    def publish_signal(self, signal: StrategySignal) -> CommandHandle: ...
    def status(self, request_id: str) -> CommandHandle: ...


class MarketCommandPort(Protocol):
    def subscribe(
        self,
        request: MarketSubscriptionRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle: ...
    def unsubscribe(
        self,
        subscription: object,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle: ...


class IntentCommandPort(Protocol):
    def target_position(
        self,
        request: TargetPositionRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle: ...
    def pair_arbitrage(
        self,
        request: PairArbitrageRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle: ...
    def portfolio_rebalance(
        self,
        request: PortfolioRebalanceRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle: ...
    def quote_provisioning(
        self,
        request: QuoteProvisioningRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle: ...
    def refresh_quote(
        self,
        request: QuoteRefreshRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle: ...
    def publish(self, signal: StrategySignal) -> CommandHandle: ...


class SnapshotReader(Protocol):
    def read(self, view_key: str) -> SnapshotEnvelope: ...


class EventStream(Protocol):
    stream_id: str

    def can_join(self, event_sequence: int) -> bool: ...
    def events(self, after_sequence: int = 0) -> AsyncIterator[EventEnvelope]: ...


class LifecycleJournal(Protocol):
    def append(self, record: object) -> None: ...
