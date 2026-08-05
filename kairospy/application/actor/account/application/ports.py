"""Consumer-owned account ports used by the ExternalAccount Actor."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from kairospy.application.support.messaging import Message
from kairospy.application.usecases.account.application.directory import AccountDirectory
from kairospy.domain.account import AccountCapability, AccountRuntimeContext, AccountCurrentView, AccountDetailView, AccountFeeSchedule, AccountMarketProfile, AssetCode
from kairospy.domain.intent import TradeIntent
from kairospy.domain.market import MarketEvent, MarketEventValue
from kairospy.domain.order import OrderState
from .commands import IntentExecutionContext
from kairospy.application.actor.risk.application.commands import (
    AssessRiskCommand,
    ConfigureRiskBudgetsCommand,
    ConsumeRiskCommand,
    ReleaseRiskCommand,
    ReserveRiskCommand,
)
from kairospy.application.usecases.risk.application.component import RiskAssessmentResult, RiskReservationChangeResult, RiskReservationResult
from kairospy.application.usecases.account.application.view_contracts import AccountViewObservation

RiskCommand = ConfigureRiskBudgetsCommand | AssessRiskCommand | ReserveRiskCommand | ReleaseRiskCommand | ConsumeRiskCommand
RiskCommandResult = None | RiskAssessmentResult | RiskReservationResult | RiskReservationChangeResult


class AccountProjectionPort(Protocol):
    @property
    def account(self) -> AccountRuntimeContext: ...
    @property
    def valuation_asset(self) -> AssetCode: ...
    @property
    def settlement_asset(self) -> AssetCode: ...
    def asset_balance(self, currency: AssetCode | str | None = None) -> Decimal: ...
    def positions(self) -> dict[str, Decimal]: ...
    def record_funding(
        self,
        *,
        occurred_at: datetime,
        currency: AssetCode | str,
        balance_delta: Decimal,
        instrument_id: str,
        reference_id: str,
    ) -> None: ...


class AccountActorViewPort(Protocol):
    def directory(self) -> AccountDirectory: ...
    def capabilities(self) -> tuple[AccountCapability, ...]: ...
    def fees(self) -> tuple[AccountFeeSchedule, ...]: ...
    def market_profiles(self) -> tuple[AccountMarketProfile, ...]: ...
    @property
    def projection(self) -> AccountProjectionPort | None: ...
    def current_view(self, context: AccountRuntimeContext, *, event_count: int = 0, last_event_time: datetime | None = None, payload: AccountViewObservation | None = None, equity_currency: AssetCode | str | None = None, latest_equity: Decimal | None = None, initial_equity: Decimal | None = None, pending_orders: tuple[OrderState, ...] = (), now: datetime | None = None) -> AccountCurrentView: ...
    def detail_view(self, context: AccountRuntimeContext, *, event_count: int = 0, last_event_time: datetime | None = None, metadata: dict[str, object] | None = None, now: datetime | None = None) -> AccountDetailView: ...
    @property
    def account(self) -> AccountRuntimeContext: ...
    @property
    def valuation_asset(self) -> AssetCode: ...
    @property
    def settlement_asset(self) -> AssetCode: ...
    def asset_balance(self, currency: AssetCode | str | None = None) -> Decimal: ...
    def positions(self) -> dict[str, Decimal]: ...
    def record_funding(
        self,
        *,
        occurred_at: datetime,
        currency: AssetCode | str,
        balance_delta: Decimal,
        instrument_id: str,
        reference_id: str,
    ) -> None: ...


class AccountEventSource(Protocol):
    is_finite: bool

    def events(self) -> AsyncIterator[Message]: ...


class ExecutionEventSource(Protocol):
    is_finite: bool

    def events(self) -> AsyncIterator[Message]: ...
    def execute_intent(self, intent: TradeIntent, context: IntentExecutionContext) -> OrderState | None: ...
    def on_market_event(self, event: MarketEvent | MarketEventValue) -> None: ...
    def cancel_order(self, order_id: str, *, at: datetime) -> OrderState: ...


class AccountConnectionManager(Protocol):
    def start_roles(self, roles: tuple[str, ...]) -> None: ...
    def stop_roles(self, roles: tuple[str, ...]) -> None: ...
    def health(self) -> Mapping[str, object]: ...


class AccountProjectorPort(Protocol):
    def on_event(self, event: Message) -> None: ...


class RiskCommandPort(Protocol):
    async def dispatch_command(self, command: RiskCommand, *, correlation_id: str | None = None, causation_id: str | None = None) -> RiskCommandResult: ...


__all__ = [
    "AccountActorViewPort",
    "AccountProjectionPort",
    "AccountEventSource",
    "ExecutionEventSource",
    "AccountConnectionManager",
    "AccountProjectorPort",
    "RiskCommandPort",
    "RiskCommand",
    "RiskCommandResult",
]
