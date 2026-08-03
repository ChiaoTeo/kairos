"""Dependencies consumed by the Integration infrastructure module.

These are intentionally small contracts. Concrete clocks and credential
stores are selected by composition or test fixtures; Integration does not
locate them globally.
"""

from __future__ import annotations

from typing import Protocol


class MillisecondClock(Protocol):
    def __call__(self) -> int: ...


class CredentialValueReader(Protocol):
    def value(self, credential_id: str, field: str) -> str | None: ...


__all__ = ["CredentialValueReader", "MillisecondClock"]
