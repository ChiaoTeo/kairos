"""Public account snapshot persistence port and application service."""

from __future__ import annotations

from typing import Protocol, cast

from kairospy.domain.account import AccountSnapshot


class AccountSnapshotStore(Protocol):
    def update_snapshot(self, snapshot: AccountSnapshot) -> None: ...


class AccountSnapshotService:
    def __init__(self, store: AccountSnapshotStore | None) -> None:
        self._store = store

    @classmethod
    def from_store(cls, store: object | None) -> "AccountSnapshotService | None":
        if store is None:
            return None
        return cls(cast(AccountSnapshotStore, store)) if callable(getattr(store, "update_snapshot", None)) else cls(None)

    def apply(self, snapshot: AccountSnapshot) -> None:
        if self._store is not None:
            self._store.update_snapshot(snapshot)


__all__ = ["AccountSnapshotService", "AccountSnapshotStore"]
