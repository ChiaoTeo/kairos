"""Minimal resource ports consumed by system-facing facades."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from kairospy.application.usecases.market.application.commands import DriverName
from kairospy.domain.account import AccountSegment
from kairospy.application.usecases.account.protocol import AccountReadPort


@dataclass(frozen=True, slots=True)
class AccountCredentialProfile:
    """Normalized credential/account discovery facts.

    Vendor payloads are intentionally converted to this value at the
    composition boundary.  Account use cases should reason about these
    facts, not about an SDK response shape.
    """

    remote_identity: str | None = None
    account_type: str | None = None
    permissions: frozenset[str] = frozenset()
    segments: tuple[str, ...] = ()
    attributes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "permissions", frozenset(self.permissions))
        object.__setattr__(self, "segments", tuple(self.segments))
        object.__setattr__(self, "attributes", dict(self.attributes))

    def to_dict(self) -> dict[str, object]:
        return {
            "remote_identity": self.remote_identity,
            "account_type": self.account_type,
            "permissions": sorted(self.permissions),
            "segments": list(self.segments),
            "attributes": dict(self.attributes),
        }


class AccountCommandResources(Protocol):
    def credential_profile(self, segment: AccountSegment, driver_name: DriverName, *, credential: str | None = None) -> AccountCredentialProfile: ...
    def account_reader(self, segment: AccountSegment, driver_name: DriverName, *, credential: str | None = None) -> AccountReadPort: ...


__all__ = ["AccountCommandResources", "AccountCredentialProfile"]
