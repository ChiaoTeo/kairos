"""Account-facing orchestration for workspace-owned trading leases."""

from __future__ import annotations

from kairospy.application.usecases.account.application.configuration import AccountConfigurationError, AccountRecord, AccountStore
from kairospy.application.usecases.account.application.results import AccountLockReleaseResult, AccountLockResult, AccountLocksResult
from kairospy.application.usecases.workspace.application.context import workspace as resolve_workspace
from kairospy.application.usecases.workspace.application.leases import AccountLeaseSubject, WorkspaceAccountLeaseApplication


class AccountLeaseApplication:
    """Adapt account identifiers to the workspace lease application boundary."""

    def __init__(self) -> None:
        self._workspace_leases = WorkspaceAccountLeaseApplication()

    def list(self) -> AccountLocksResult:
        result = self._workspace_leases.list()
        return AccountLocksResult(result.leases, result.count, result.root)

    def status(self, account_id: str) -> AccountLockResult:
        account = _account(account_id)
        result = self._workspace_leases.status(_subject(account))
        return AccountLockResult(account.account_id, result.account_key, result.lease)

    def release(self, account_id: str, *, stale_only: bool, force: bool) -> AccountLockReleaseResult:
        account = _account(account_id)
        result = self._workspace_leases.release(_subject(account), stale_only=stale_only, force=force)
        return AccountLockReleaseResult(account.account_id, result.account_key, result.released)


def _account(account_id: str) -> AccountRecord:
    try:
        return AccountStore.load(resolve_workspace().accounts_root).get(account_id)
    except AccountConfigurationError as error:
        raise ValueError(str(error)) from error


def _subject(account: AccountRecord) -> AccountLeaseSubject:
    return AccountLeaseSubject(str(account.identity.broker), str(account.identity.account_id))


__all__ = ["AccountLeaseApplication"]
