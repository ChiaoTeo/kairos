"""Strongly typed results for account query and administration use cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from kairospy.application.support.query.pagination import PageResult
from kairospy.application.usecases.account.application.ports import AccountCredentialProfile
from kairospy.domain.account import AccountBalance, ExternalAccountIdentity, AccountSegment, AccountSnapshot, AccountSource, OpenOrderSnapshot, PositionSnapshot


@dataclass(frozen=True, slots=True)
class AccountBalanceRow:
    segment: AccountSegment
    balance: AccountBalance


@dataclass(frozen=True, slots=True)
class AccountBalanceError:
    segment: AccountSegment
    message: str
    error_type: str
    duration_ms: int
    diagnostic_id: str | None = None
    diagnostic_path: Path | None = None


@dataclass(frozen=True, slots=True)
class AccountSnapshotSummary:
    segment: AccountSegment
    observed_at: datetime | None
    source: AccountSource


@dataclass(frozen=True, slots=True)
class AccountBalanceResult:
    account_id: str
    broker: str
    segments: tuple[AccountSegment, ...]
    rows: tuple[AccountBalanceRow, ...]
    page: PageResult
    snapshots: tuple[AccountSnapshotSummary, ...] = ()
    errors: tuple[AccountBalanceError, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountOpenOrdersResult:
    account_id: str
    orders: tuple[OpenOrderSnapshot, ...]
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class AccountPositionRow:
    segment: AccountSegment
    position: PositionSnapshot


@dataclass(frozen=True, slots=True)
class AccountPositionsResult:
    account_id: str
    broker: str
    segments: tuple[AccountSegment, ...]
    rows: tuple[AccountPositionRow, ...]
    observed_at: datetime | None
    errors: tuple[AccountBalanceError, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountSnapshotResult:
    account_id: str
    snapshot: AccountSnapshot
    journal_path: Path


@dataclass(frozen=True, slots=True)
class AccountBindingResult:
    """Result of discovering and binding an externally owned account."""

    binding_id: str
    identity: ExternalAccountIdentity
    segments: tuple[AccountSegment, ...]
    credential_ref: str
    remote_identity: str | None = None
    source_path: Path | None = None


@dataclass(frozen=True, slots=True)
class AccountConfigurationPathResult:
    path: Path


@dataclass(frozen=True, slots=True)
class AccountInspectionResult:
    account_id: str
    broker: str
    environment: str
    credential: str | None
    remote_identity: str | None
    account_type: str | None
    observed_model: str | None
    permissions: tuple[str, ...]
    configured_segments: tuple[str, ...]
    discovered_segments: tuple[str, ...]
    profile: AccountCredentialProfile


@dataclass(frozen=True, slots=True)
class AccountListResult:
    accounts: tuple[Mapping[str, object], ...]
    count: int
    root: str


@dataclass(frozen=True, slots=True)
class AccountSchemasResult:
    schemas: Mapping[str, Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class AccountSchemaResult:
    broker: str
    venue: str
    credential_kind: str
    required_credential_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]
    credential_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AccountDetailResult:
    account: Mapping[str, object]
    lock: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class AccountLocksResult:
    locks: tuple[Mapping[str, object], ...]
    count: int
    root: str


@dataclass(frozen=True, slots=True)
class AccountLockResult:
    account: str
    account_key: str
    lock: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class AccountLockReleaseResult:
    account: str
    account_key: str
    released: bool


@dataclass(frozen=True, slots=True)
class AccountDoctorResult:
    account: Mapping[str, object]
    valid: bool
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AccountModelSwitchResult:
    account: str
    from_model: str | None
    to_model: str
    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class AccountCredentialListResult:
    credentials: tuple[Mapping[str, object], ...]
    count: int
    root: str


@dataclass(frozen=True, slots=True)
class AccountCredentialResult:
    credential: Mapping[str, object]


__all__ = [
    "AccountBalanceError",
    "AccountBindingResult",
    "AccountConfigurationPathResult",
    "AccountBalanceResult",
    "AccountBalanceRow",
    "AccountOpenOrdersResult",
    "AccountPositionRow",
    "AccountPositionsResult",
    "AccountSnapshotResult",
    "AccountSnapshotSummary",
    "AccountInspectionResult",
    "AccountListResult",
    "AccountSchemasResult",
    "AccountSchemaResult",
    "AccountDetailResult",
    "AccountLocksResult",
    "AccountLockResult",
    "AccountLockReleaseResult",
    "AccountDoctorResult",
    "AccountModelSwitchResult",
    "AccountCredentialListResult",
    "AccountCredentialResult",
]
