"""Typed observations used to build account read-side views."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class AccountViewObservation:
    equity: Decimal | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


__all__ = ["AccountViewObservation"]
