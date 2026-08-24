"""Credential values governed by the Workspace capability."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


SecretSource = Literal["env", "file"]


@dataclass(frozen=True, slots=True)
class SecretRef:
    source: SecretSource
    id: str

    def __post_init__(self) -> None:
        identifier = self.id.strip()
        if self.source == "env":
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", identifier):
                raise ValueError(
                    "environment SecretRef id must use uppercase letters, numbers, and underscores"
                )
        elif self.source == "file":
            if not identifier or "\x00" in identifier:
                raise ValueError("file SecretRef id must be a non-empty path")
        else:
            raise ValueError(f"unsupported SecretRef source: {self.source}")
        object.__setattr__(self, "id", identifier)


__all__ = ["SecretRef", "SecretSource"]
