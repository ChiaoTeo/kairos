"""Commands accepted by the ExternalAccount Actor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from kairospy.domain.account import AccountSegment, AccountMarketProfile
from kairospy.application.usecases.account.application.read import AccountQueryRequest, AccountQueryResult, AccountRefreshRequest, AccountRefreshResult
from kairospy.domain.reference import MarketRef
from kairospy.domain.intent import IntentJournal, TradeIntent
from kairospy.domain.intent import IntentState
from kairospy.domain.order import OrderState


@dataclass(frozen=True, slots=True)
class RefreshAccountMarketProfileCommand:
    account: AccountSegment
    market: MarketRef
    at: datetime | None = None


@dataclass(frozen=True, slots=True)
class QueryAccountCommand:
    request: AccountQueryRequest


@dataclass(frozen=True, slots=True)
class RefreshAccountCommand:
    request: AccountRefreshRequest


@dataclass(frozen=True, slots=True)
class RecordIntentsCommand:
    intents: tuple[TradeIntent, ...]
    at: datetime


@dataclass(frozen=True, slots=True)
class ExecuteIntentCommand:
    intent: TradeIntent
    context: "IntentExecutionContext"


class IntentExecutionContext(Protocol):
    now: datetime | None
    intents: IntentJournal


@dataclass(frozen=True, slots=True)
class CancelOrderCommand:
    order_id: str
    at: datetime


@dataclass(frozen=True, slots=True)
class CancelIntentCommand:
    intent_id: str
    at: datetime


@dataclass(frozen=True, slots=True)
class AccountMarketProfileUpdated:
    profile: AccountMarketProfile


AccountCommand = QueryAccountCommand | RefreshAccountCommand | RecordIntentsCommand | ExecuteIntentCommand | CancelOrderCommand | CancelIntentCommand | RefreshAccountMarketProfileCommand
AccountCommandResult = None | AccountQueryResult | AccountRefreshResult | OrderState | IntentState


__all__ = [
    "AccountMarketProfileUpdated",
    "AccountQueryCommand",
    "AccountRefreshCommand",
    "CancelIntentCommand",
    "CancelOrderCommand",
    "ExecuteIntentCommand",
    "IntentExecutionContext",
    "AccountCommand",
    "AccountCommandResult",
    "RecordIntentsCommand",
    "RefreshAccountMarketProfileCommand",
]
