"""Public account snapshot persistence port and application service."""

from __future__ import annotations

from kairospy.domain.account import AccountSnapshot
from kairospy.application.usecases.account.protocol import AccountSnapshotStore


class AccountSnapshotService:
    def __init__(self, store: AccountSnapshotStore | None) -> None:
        self._store = store

    @classmethod
    def from_store(cls, store: AccountSnapshotStore | None) -> "AccountSnapshotService | None":
        if store is None:
            return None
        return cls(store)

    def apply(self, snapshot: AccountSnapshot) -> None:
        if self._store is not None:
            self._store.update_snapshot(snapshot)


__all__ = ["AccountSnapshotService", "AccountSnapshotStore"]
