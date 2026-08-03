from __future__ import annotations

from typing import Protocol, cast

from kairospy.domain.account import AccountSnapshot


class AccountSnapshotStore(Protocol):
    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        ...


class AccountSnapshotService:
    def __init__(self, store: AccountSnapshotStore | None) -> None:
        self.store = store

    @classmethod
    def from_store(cls, store: object | None) -> "AccountSnapshotService | None":
        if store is None:
            return None
        update_snapshot = getattr(store, "update_snapshot", None)
        if not callable(update_snapshot):
            return cls(None)
        return cls(cast(AccountSnapshotStore, store))

    def apply(self, snapshot: AccountSnapshot) -> None:
        if self.store is None:
            return
        self.store.update_snapshot(snapshot)


__all__ = [
    "AccountSnapshotService",
    "AccountSnapshotStore",
]
