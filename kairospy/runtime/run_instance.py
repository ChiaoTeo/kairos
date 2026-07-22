from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class RunInstance:
    run_id: str
    root: Path
    manifest_path: Path
    workspace_snapshot_path: Path
    resolved_config_path: Path | None = None


class RunManifestBuilder:
    def build(
        self,
        *,
        run_id: str,
        mode: str,
        status: str,
        project_config_path: str | Path,
        project_config_hash: str,
        run_config_path: str | Path | None,
        run_config_hash: str | None,
        workspace_name: str,
        workspace_root: str | Path,
        workspace_snapshot_artifact: str | Path,
        workspace_snapshot_hash: str,
        strategy: Mapping[str, Any],
        params_hash: str,
        config_hash: str,
        bindings: Mapping[str, Any],
        guards: Mapping[str, Any],
        started_at: str,
        finished_at: str,
        artifacts: Mapping[str, Any],
        runtime_launch: Mapping[str, Any] | None = None,
        run_result: Mapping[str, Any] | None = None,
        resolved_config_artifact: str | Path | None = None,
    ) -> dict[str, Any]:
        manifest: dict[str, Any] = {
            "product": "run",
            "kind": "run.manifest",
            "schema_version": 2,
            "run_id": run_id,
            "mode": mode,
            "status": status,
            "project_config": {
                "path": str(project_config_path),
                "hash": project_config_hash,
            },
            "project_config_hash": project_config_hash,
            "run_config": {
                "path": str(run_config_path) if run_config_path is not None else None,
                "hash": run_config_hash,
            },
            "workspace": {
                "name": workspace_name,
                "root": str(workspace_root),
                "snapshot_artifact": str(workspace_snapshot_artifact),
                "snapshot_hash": workspace_snapshot_hash,
            },
            "strategy": dict(strategy),
            "params_hash": params_hash,
            "config_hash": config_hash,
            "bindings": dict(bindings),
            "guards": dict(guards),
            "started_at": started_at,
            "finished_at": finished_at,
            "artifacts": dict(artifacts),
        }
        if resolved_config_artifact is not None:
            manifest["run_config"]["resolved_config_artifact"] = str(resolved_config_artifact)
        if runtime_launch is not None:
            manifest["runtime_launch"] = dict(runtime_launch)
        if run_result is not None:
            manifest["run_result"] = dict(run_result)
        return manifest
