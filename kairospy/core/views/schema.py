from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from .hashing import view_hash


ViewOwner = Literal["system", "project", "strategy"]
ViewMutability = Literal["read_only", "runtime_writable", "strategy_writable"]
ViewPersistence = Literal["ephemeral", "checkpointed", "journaled"]


@dataclass(frozen=True, slots=True)
class ViewFieldSchema:
    name: str
    semantic: str = ""
    time_semantics: str = ""
    evidence: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("view field name is required")


@dataclass(frozen=True, slots=True)
class ViewSchema:
    key: str
    owner: ViewOwner | str
    version: str = "1"
    fields: tuple[ViewFieldSchema, ...] = ()
    mutability: ViewMutability = "read_only"
    persistence: ViewPersistence = "ephemeral"
    evidence: str = ""

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("view schema key is required")
        if not self.owner.strip():
            raise ValueError("view schema owner is required")
        if not self.version.strip():
            raise ValueError("view schema version is required")
        object.__setattr__(self, "fields", tuple(_field_schema(item) for item in self.fields))

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.fields)

    @property
    def schema_hash(self) -> str:
        return view_hash(self)


def _field_schema(value: ViewFieldSchema | Mapping[str, Any]) -> ViewFieldSchema:
    if isinstance(value, ViewFieldSchema):
        return value
    return ViewFieldSchema(**dict(value))


__all__ = [
    "ViewFieldSchema",
    "ViewMutability",
    "ViewOwner",
    "ViewPersistence",
    "ViewSchema",
]
