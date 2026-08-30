"""Typed Account-owned configuration evidence and launch snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TypeAlias

from kairospy.primitives.account import AccountIdRead, SegmentKeyRead


AccountVerificationStatus: TypeAlias = Literal[
    "pending", "verified", "failed", "retest_required"
]
AccountAccessPurpose: TypeAlias = Literal["account-read", "order-trade"]


@dataclass(frozen=True, slots=True)
class AccountVerification:
    status: AccountVerificationStatus
    last_tested_at: str | None
    tested: tuple[str, ...]
    not_tested: tuple[str, ...]
    capabilities: tuple[str, ...]
    segments: tuple[SegmentKeyRead, ...]
    error_category: str | None
    tested_configuration_hash: str | None
    current_configuration_hash: str

    def to_json_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "verification_status": self.status,
            "last_tested_at": self.last_tested_at,
            "tested": list(self.tested),
            "not_tested": list(self.not_tested),
            "tested_configuration_hash": self.tested_configuration_hash,
            "current_configuration_hash": self.current_configuration_hash,
        }
        if self.last_tested_at is not None:
            result.update(
                {
                    "capabilities": list(self.capabilities),
                    "segments": list(self.segments),
                    "error_category": self.error_category,
                }
            )
        return result


@dataclass(frozen=True, slots=True)
class AccountAccessBinding:
    purpose: AccountAccessPurpose
    credential_id: str
    enabled: bool
    observed_permissions: tuple[str, ...]

    def to_json_dict(self) -> dict[str, object]:
        return {
            "purpose": self.purpose,
            "credential_id": self.credential_id,
            "enabled": self.enabled,
            "observed_permissions": list(self.observed_permissions),
        }


@dataclass(frozen=True, slots=True)
class AccountCredentialIdentity:
    credential_id: str
    provider: str | None = None
    role: str | None = None
    fields: tuple[str, ...] = ()
    missing: bool = False

    def to_json_dict(self) -> dict[str, object]:
        if self.missing:
            return {"credential_id": self.credential_id, "missing": True}
        return {
            "credential_id": self.credential_id,
            "provider": self.provider,
            "role": self.role,
            "fields": list(self.fields),
        }


@dataclass(frozen=True, slots=True)
class AccountResourceSnapshot:
    account_id: AccountIdRead
    broker: str | None
    integration_provider: str | None
    environment: str | None
    masked_remote_identity: str | None
    segments: tuple[SegmentKeyRead, ...]
    permissions: Mapping[str, str]
    credential_identities: tuple[AccountCredentialIdentity, ...]
    verification: AccountVerification
    resource_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "permissions", MappingProxyType(dict(self.permissions))
        )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "broker": self.broker,
            "integration_provider": self.integration_provider,
            "environment": self.environment,
            "masked_remote_identity": self.masked_remote_identity,
            "segments": list(self.segments),
            "permissions": dict(self.permissions),
            "credential_identities": [
                identity.to_json_dict() for identity in self.credential_identities
            ],
            "verification": self.verification.to_json_dict(),
            "resource_hash": self.resource_hash,
        }


__all__ = [
    "AccountAccessBinding",
    "AccountAccessPurpose",
    "AccountCredentialIdentity",
    "AccountResourceSnapshot",
    "AccountVerification",
    "AccountVerificationStatus",
]
