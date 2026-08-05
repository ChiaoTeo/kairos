"""Public workspace lease capabilities used by runtime and CLI composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from kairospy.application.usecases.workspace.domain.workspace.account_locks import (
    AccountLease,
    AccountLeaseError,
    AccountLeaseManager,
    AccountLeaseSubject,
)
from kairospy.application.usecases.workspace.application.context import workspace as resolve_workspace


@dataclass(frozen=True, slots=True)
class WorkspaceLeaseListResult:
    leases: tuple[Mapping[str, object], ...]
    count: int
    root: str


@dataclass(frozen=True, slots=True)
class WorkspaceLeaseStatusResult:
    account_key: str
    lease: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class WorkspaceLeaseReleaseResult:
    account_key: str
    released: bool


class WorkspaceAccountLeaseApplication:
    """Own explicit inspection and release of workspace account leases."""

    def list(self) -> WorkspaceLeaseListResult:
        workspace = resolve_workspace()
        leases = tuple(lease.to_dict() for lease in workspace.account_locks.list())
        return WorkspaceLeaseListResult(leases, len(leases), str(workspace.account_locks.root))

    def status(self, subject: AccountLeaseSubject) -> WorkspaceLeaseStatusResult:
        lease = resolve_workspace().account_locks.get(subject.key)
        return WorkspaceLeaseStatusResult(subject.key, None if lease is None else lease.to_dict())

    def release(self, subject: AccountLeaseSubject, *, stale_only: bool, force: bool) -> WorkspaceLeaseReleaseResult:
        workspace = resolve_workspace()
        released = workspace.account_locks.release(subject.key, force=force, stale_only=stale_only)
        if released:
            workspace.operations.append("account.lock.release.manual", target={"account": subject.account_id}, payload={"stale_only": stale_only, "force": force})
        return WorkspaceLeaseReleaseResult(subject.key, released)

__all__ = [
    "AccountLease",
    "AccountLeaseError",
    "AccountLeaseManager",
    "AccountLeaseSubject",
    "WorkspaceAccountLeaseApplication",
    "WorkspaceLeaseListResult",
    "WorkspaceLeaseReleaseResult",
    "WorkspaceLeaseStatusResult",
]
