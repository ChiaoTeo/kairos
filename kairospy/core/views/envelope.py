from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .hashing import view_hash


@dataclass(frozen=True, slots=True)
class ViewEnvelope:
    key: str
    schema_version: str
    owner: str
    payload: Any
    as_of: datetime | None = None
    available_time: datetime | None = None
    payload_hash: str = ""

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.schema_version.strip() or not self.owner.strip():
            raise ValueError("view envelope identity fields are required")
        if self.as_of is not None and self.as_of.tzinfo is None:
            raise ValueError("view as_of must be timezone-aware")
        if self.available_time is not None and self.available_time.tzinfo is None:
            raise ValueError("view available_time must be timezone-aware")
        if not self.payload_hash:
            object.__setattr__(self, "payload_hash", view_hash(self.payload))

    @property
    def view_hash(self) -> str:
        return view_hash(
            {
                "key": self.key,
                "schema_version": self.schema_version,
                "owner": self.owner,
                "as_of": self.as_of,
                "available_time": self.available_time,
                "payload_hash": self.payload_hash,
            }
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "schema_version": self.schema_version,
            "owner": self.owner,
            "as_of": self.as_of.isoformat() if self.as_of is not None else None,
            "available_time": self.available_time.isoformat() if self.available_time is not None else None,
            "payload_hash": self.payload_hash,
            "view_hash": self.view_hash,
        }


__all__ = ["ViewEnvelope"]
