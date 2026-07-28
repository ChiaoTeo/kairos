from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping


INTEGRATION_DOMAIN_ACCOUNT = "account"
INTEGRATION_DOMAIN_ORDER = "order"
INTEGRATION_DOMAIN_REFERENCE = "reference"


@dataclass(frozen=True, slots=True)
class IntegrationAccountUpdate:
    account_id: str
    observed_at: datetime
    fields: Mapping[str, object]
    source: str = ""
    identity: str | None = None
    sequence: int | None = None
    metadata: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.account_id.strip():
            raise ValueError("account update account_id is required")
        _require_aware_time(self.observed_at, "account update observed_at")
        if self.sequence is not None and self.sequence < 1:
            raise ValueError("account update sequence must be positive")
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class IntegrationOrderUpdate:
    order_id: str
    observed_at: datetime
    fields: Mapping[str, object]
    source: str = ""
    identity: str | None = None
    sequence: int | None = None
    metadata: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise ValueError("order update order_id is required")
        _require_aware_time(self.observed_at, "order update observed_at")
        if self.sequence is not None and self.sequence < 1:
            raise ValueError("order update sequence must be positive")
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class IntegrationReferenceUpdate:
    entity_type: str
    entity_id: str
    observed_at: datetime
    fields: Mapping[str, object]
    source: str = ""
    sequence: int | None = None
    metadata: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        _require_identity(self.entity_type, self.entity_id)
        _require_aware_time(self.observed_at, "reference update observed_at")
        if self.sequence is not None and self.sequence < 1:
            raise ValueError("reference update sequence must be positive")
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


def _require_identity(kind: str, value: str) -> None:
    if not kind.strip() or not value.strip():
        raise ValueError("integration update identity fields are required")


def _require_aware_time(value: datetime, label: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")


__all__ = [
    "INTEGRATION_DOMAIN_ACCOUNT",
    "INTEGRATION_DOMAIN_ORDER",
    "INTEGRATION_DOMAIN_REFERENCE",
    "IntegrationAccountUpdate",
    "IntegrationOrderUpdate",
    "IntegrationReferenceUpdate",
]
