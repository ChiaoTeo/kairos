from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from kairospy.infrastructure.configuration import KairosProjectConfig, PROJECT_STATE_DIR

from .model import WorkspaceBinding, WorkspaceManifest


class WorkspaceRepository:
    def __init__(self, project_root: str | Path = ".") -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.root = self.project_root / PROJECT_STATE_DIR / "workspace"

    @classmethod
    def discover(cls, start: str | Path = ".") -> "WorkspaceRepository":
        config = KairosProjectConfig.discover(start)
        return cls(config.root)

    def path(self, name: str) -> Path:
        _validate_name(name)
        return self.root / name

    def manifest_path(self, name: str) -> Path:
        return self.path(name) / "workspace.json"

    def create(self, name: str) -> "Workspace":
        path = self.path(name)
        path.mkdir(parents=True, exist_ok=True)
        (path / "data" / "snapshots").mkdir(parents=True, exist_ok=True)
        (path / "data" / "cache").mkdir(parents=True, exist_ok=True)
        (path / "artifacts").mkdir(parents=True, exist_ok=True)
        manifest_path = self.manifest_path(name)
        if manifest_path.exists():
            return self.open(name)
        manifest = WorkspaceManifest.create(name, path)
        self._write(manifest)
        self._write_aliases(manifest)
        return Workspace(self, manifest)

    def open(self, name: str) -> "Workspace":
        payload = json.loads(self.manifest_path(name).read_text(encoding="utf-8"))
        return Workspace(self, WorkspaceManifest.from_dict(payload))

    def open_or_create(self, name: str) -> "Workspace":
        return self.open(name) if self.manifest_path(name).exists() else self.create(name)

    def list(self) -> tuple["Workspace", ...]:
        if not self.root.exists():
            return ()
        workspaces = []
        for manifest_path in sorted(self.root.glob("*/workspace.json")):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                workspaces.append(Workspace(self, WorkspaceManifest.from_dict(payload)))
            except (OSError, json.JSONDecodeError, KeyError, ValueError):
                continue
        return tuple(workspaces)

    def save(self, manifest: WorkspaceManifest) -> WorkspaceManifest:
        updated = replace(manifest, updated_at=datetime.now(timezone.utc).isoformat())
        self._write(updated)
        self._write_aliases(updated)
        return updated

    def _write(self, manifest: WorkspaceManifest) -> None:
        path = self.manifest_path(manifest.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _write_aliases(self, manifest: WorkspaceManifest) -> None:
        path = self.path(manifest.name) / "data" / "aliases.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        aliases = {
            name: binding.to_dict()
            for name, binding in sorted(manifest.bindings.items())
        }
        path.write_text(json.dumps({"schema_version": 1, "bindings": aliases}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class WorkspaceData:
    def __init__(self, workspace: "Workspace") -> None:
        self.workspace = workspace

    def get(self, name: str):
        binding = self.workspace.binding(name)
        if binding.metadata.get("view") not in {"history", "both"} and binding.kind != "dataset":
            raise ValueError(f"workspace binding {name!r} is not a historical dataset")
        from kairospy.data import DatasetClient

        return DatasetClient(self.workspace.data_root).query(binding.dataset)

    def live(self, name: str) -> WorkspaceBinding:
        binding = self.workspace.binding(name)
        if binding.metadata.get("view") not in {"live", "both"} and binding.kind != "live_view":
            raise ValueError(f"workspace binding {name!r} is not a live view")
        return binding


class Workspace:
    def __init__(self, repository: WorkspaceRepository, manifest: WorkspaceManifest) -> None:
        self.repository = repository
        self.manifest = manifest
        self.data = WorkspaceData(self)

    @classmethod
    def open_or_create(cls, name: str, *, start: str | Path = ".") -> "Workspace":
        return WorkspaceRepository.discover(start).open_or_create(name)

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def root(self) -> Path:
        return self.repository.path(self.name)

    @property
    def data_root(self) -> Path:
        try:
            config = KairosProjectConfig.discover(self.repository.project_root)
            return config.relative_path("paths.lake_root", f"{PROJECT_STATE_DIR}/data")
        except Exception:
            return self.repository.project_root / PROJECT_STATE_DIR / "data"

    def attach(
        self,
        name: str,
        *,
        dataset: str | None = None,
        stream: str | None = None,
        view: str = "both",
        instruments: tuple[str, ...] = (),
        fields: tuple[str, ...] = (),
        freshness_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceBinding:
        if view not in {"history", "live", "both"}:
            raise ValueError("workspace attachment view must be history, live, or both")
        if stream is None and dataset is None:
            raise ValueError("workspace attachment requires a Data Stream or Dataset")
        stream_id = str(stream or dataset)
        dataset_id = str(dataset or stream_id)
        template = "{space}" in stream_id
        current = self.manifest.bindings.get(name)
        if current is not None and current.dataset != dataset_id:
            raise ValueError(f"workspace attachment {name!r} already points to {current.dataset!r}")
        payload = {
            "view": _merge_view(str(current.metadata.get("view") or view), view) if current is not None else view,
            **({"template": True} if template else {}),
            **({"instruments": list(instruments)} if instruments else {}),
            **({"fields": list(fields)} if fields else {}),
            **({"freshness_seconds": freshness_seconds} if freshness_seconds is not None else {}),
            **(metadata or {}),
        }
        binding = WorkspaceBinding(name=name, kind="attachment", dataset=dataset_id, stream=stream_id, metadata=payload)
        return self._save_binding(binding)

    def binding(self, name: str) -> WorkspaceBinding:
        try:
            return self.manifest.bindings[name]
        except KeyError as error:
            raise KeyError(f"workspace {self.name!r} has no data binding {name!r}") from error

    def artifact(self, name: str) -> Path:
        if not name.strip() or "/" in name or ".." in name:
            raise ValueError("workspace artifact name must be a simple relative name")
        path = self.root / "artifacts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "workspace": self.name,
            "bindings": {
                name: binding.to_dict()
                for name, binding in sorted(self.manifest.bindings.items())
            },
            "params": self.manifest.params,
        }

    def _save_binding(self, binding: WorkspaceBinding) -> WorkspaceBinding:
        bindings = dict(self.manifest.bindings)
        bindings[binding.name] = binding
        self.manifest = self.repository.save(replace(self.manifest, bindings=bindings))
        return binding


def _validate_name(name: str) -> None:
    if not name.strip():
        raise ValueError("workspace name is required")
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("workspace name must be a simple directory name")


def _merge_view(first: str, second: str) -> str:
    values = {first, second}
    if "both" in values or values == {"history", "live"}:
        return "both"
    if "live" in values:
        return "live"
    return "history"
