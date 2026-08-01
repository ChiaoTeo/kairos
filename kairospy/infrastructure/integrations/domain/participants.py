from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ParticipantRole = Literal["exchange", "broker", "provider"]


@dataclass(frozen=True, slots=True)
class ParticipantRef:
    role: ParticipantRole
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", integration_key(self.name))


def integration_key(value: object) -> str:
    key = str(value).strip().lower().replace("-", "_")
    if not key:
        raise ValueError("integration identity cannot be empty")
    return key


__all__ = ["ParticipantRef", "ParticipantRole", "integration_key"]
