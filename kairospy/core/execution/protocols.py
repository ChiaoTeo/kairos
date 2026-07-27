from __future__ import annotations

from datetime import datetime
from typing import Protocol

from kairospy.core.intent import IntentJournal


class ExecutionIntentContext(Protocol):
    @property
    def now(self) -> datetime | None:
        ...

    @property
    def intents(self) -> IntentJournal:
        ...

    def view(self, key: str, default: object = None) -> object:
        ...

    def latest_data(self, *, domain: str | None = None, kind: str | None = None) -> object | None:
        ...


__all__ = ["ExecutionIntentContext"]
