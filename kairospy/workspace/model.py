from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkspaceBinding:
    name: str
    kind: str
    dataset: str
    stream: str | None = None
    release_id: str | None = None
    content_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("workspace binding name is required")
        if self.kind not in {"dataset", "live_view", "attachment"}:
            raise ValueError(f"unsupported workspace binding kind: {self.kind!r}")
        if not self.dataset.strip():
            raise ValueError("workspace binding dataset is required")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value not in (None, {}, ())}


@dataclass(frozen=True, slots=True)
class WorkspaceManifest:
    name: str
    root: Path
    created_at: str
    updated_at: str
    bindings: dict[str, WorkspaceBinding] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("workspace name is required")

    @classmethod
    def create(cls, name: str, root: Path) -> "WorkspaceManifest":
        now = datetime.now(timezone.utc).isoformat()
        return cls(name=name, root=root, created_at=now, updated_at=now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "name": self.name,
            "root": str(self.root),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "bindings": {
                name: binding.to_dict()
                for name, binding in sorted(self.bindings.items())
            },
            "params": self.params,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkspaceManifest":
        bindings = {
            name: WorkspaceBinding(
                name=str(value.get("name") or name),
                kind=str(value["kind"]),
                dataset=str(value["dataset"]),
                stream=str(value["stream"]) if value.get("stream") is not None else None,
                release_id=value.get("release_id"),
                content_hash=value.get("content_hash"),
                metadata=dict(value.get("metadata", {})),
            )
            for name, value in dict(payload.get("bindings", {})).items()
        }
        return cls(
            name=str(payload["name"]),
            root=Path(str(payload["root"])),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            bindings=bindings,
            params=dict(payload.get("params", {})),
        )
