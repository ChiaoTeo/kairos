from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CredentialRef:
    id: str

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("credential id is required")


__all__ = ["CredentialRef"]
