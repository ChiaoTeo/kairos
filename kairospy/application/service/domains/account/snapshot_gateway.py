from __future__ import annotations

from kairospy.core.account import AccountContext, AccountSnapshot


class SnapshotAccountGateway:
    def __init__(self, snapshots: dict[AccountContext, AccountSnapshot]) -> None:
        self._snapshots = dict(snapshots)

    def account_snapshot(self, context: AccountContext) -> AccountSnapshot:
        try:
            return self._snapshots[context]
        except KeyError as error:
            raise LookupError(context.value) from error


__all__ = ["SnapshotAccountGateway"]
