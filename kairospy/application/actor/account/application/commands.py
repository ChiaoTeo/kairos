"""Commands accepted by the Account Actor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.domain.account import AccountBookRef, AccountMarketProfile
from kairospy.domain.reference import MarketRef
from kairospy.domain.intent import TradeIntent


@dataclass(frozen=True, slots=True)
class RefreshAccountMarketProfileCommand:
    account: AccountBookRef
    market: MarketRef
    at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RecordIntentsCommand:
    intents: tuple[object, ...]
    at: datetime


@dataclass(frozen=True, slots=True)
class ExecuteIntentCommand:
    intent: TradeIntent
    context: object


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


__all__ = [
    "AccountMarketProfileUpdated",
    "CancelIntentCommand",
    "CancelOrderCommand",
    "ExecuteIntentCommand",
    "RecordIntentsCommand",
    "RefreshAccountMarketProfileCommand",
]
