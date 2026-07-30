from __future__ import annotations

from datetime import datetime
from typing import Protocol

from kairospy.core.intent import IntentJournal
from kairospy.core.market import MarketViewReader


class ExecutionIntentContext(Protocol):
    @property
    def now(self) -> datetime | None:
        ...

    @property
    def intents(self) -> IntentJournal:
        ...

    def view(self, key: str, default: object = None) -> object:
        ...

    @property
    def market(self) -> MarketViewReader:
        ...

    def latest_data(self, *, domain: str | None = None, kind: str | None = None) -> object | None:
        ...


__all__ = ["ExecutionIntentContext"]
