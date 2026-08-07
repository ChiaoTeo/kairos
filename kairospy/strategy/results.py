from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Business-level result returned by a strategy capability request."""

    request_id: str
    status: str
    result: Mapping[str, object] = field(default_factory=dict)
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("request_id is required")
        if not self.status.strip():
            raise ValueError("status is required")
        object.__setattr__(self, "result", MappingProxyType(dict(self.result)))
