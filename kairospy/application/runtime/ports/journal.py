from __future__ import annotations

from typing import Protocol


class AccountJournalSink(Protocol):
    def record_account_view(self, account_view: object, *, run_id: str | None = None, mode: str | None = None) -> None:
        ...


__all__ = ["AccountJournalSink"]
