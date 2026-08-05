from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from kairospy.application.usecases.workspace.application.context import current_profile_name, workspace as resolve_workspace
from kairospy.application.support.launch.application.configuration import load_launch_config
from kairospy.application.usecases.account.application.configuration import AccountStore


class ConfigAdminApplication:
    def paths(self) -> dict[str, object]:
        return resolve_workspace().to_dict()

    def manifest(self) -> dict[str, object]:
        workspace = resolve_workspace()
        return {
            "workspace": workspace.to_dict(),
            "manifest": {
                "path": str(workspace.manifest.source_path) if workspace.manifest.source_path is not None else None,
                "values": dict(workspace.manifest.values),
            },
        }

    def doctor(self) -> dict[str, object]:
        workspace = resolve_workspace()
        issues: list[str] = []
        if workspace.manifest_path is None:
            issues.append(".kairos/kairos.toml was not found; using built-in defaults")
        accounts = AccountStore.load(workspace.accounts_root).list()
        for account in accounts:
            if account.environment == "live" and not account.credential_values and not account.credential and not account.credentials:
                issues.append(f"live account {account.account_id} has no credential metadata")
        return {
            "valid": not issues,
            "issues": issues,
            "workspace": workspace.to_dict(),
            "accounts": {"count": len(accounts), "root": str(workspace.accounts_root)},
            "launches": {"count": len(workspace.launch_index.list()), "path": str(workspace.launch_index.path)},
        }

    def explain(self, *, launch: str | None, config_path: Path | None) -> dict[str, object]:
        if launch is None and config_path is None:
            raise ValueError("config explain requires --launch or --config")
        if launch is not None and config_path is not None:
            raise ValueError("use either --launch or --config, not both")
        workspace = resolve_workspace()
        path = workspace.launch_index.resolve_config_path(launch) if launch is not None else config_path
        launch_config = load_launch_config(path)
        account_ref = launch_config.account_ref
        account_source = None
        if account_ref:
            try:
                account_source = str(AccountStore.load(workspace.accounts_root).get(account_ref).source_path)
            except Exception:
                account_source = None
        return {
            "target": launch or str(config_path),
            "workspace": {
                "root": str(workspace.root),
                "manifest": str(workspace.manifest_path) if workspace.manifest_path is not None else None,
            },
            "launch_config": launch_config.explain(),
            "account_ref": account_ref,
            "sources": {
                "launch_config": str(launch_config.path) if launch_config.path is not None else None,
                "workspace_manifest": str(workspace.manifest_path) if workspace.manifest_path is not None else None,
                "account": account_source,
            },
        }

    def operations(self, *, limit: int | None) -> dict[str, object]:
        workspace = resolve_workspace()
        rows = _read_jsonl(workspace.operations_path)
        if limit is not None:
            rows = rows[-limit:]
        return {"operations": rows, "count": len(rows), "path": str(workspace.operations_path)}

    def list_profiles(self) -> dict[str, object]:
        workspace = resolve_workspace()
        root = workspace.workspace_root / "profiles"
        selected = current_profile_name(workspace)
        profiles = [
            {"name": path.stem, "path": str(path), "selected": path.stem == selected}
            for path in sorted(root.glob("*.toml"))
        ]
        return {"profiles": profiles, "count": len(profiles), "root": str(root), "selected": selected}

    def use_profile(self, name: str) -> dict[str, object]:
        workspace = resolve_workspace()
        path = workspace.workspace_root / "profiles" / f"{name}.toml"
        if not path.exists():
            raise ValueError(f"profile does not exist: {path}")
        selection_path = workspace.state_root / "selection.json"
        selection_path.parent.mkdir(parents=True, exist_ok=True)
        selection_path.write_text(json.dumps({"profile": name}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        workspace.operations.append("config.profile.use", target={"profile": name}, payload={"path": path, "selection": selection_path})
        return {"profile": name, "path": str(path), "selection": str(selection_path)}

    def create_profile(self, *, name: str, source: Path | None, force: bool) -> dict[str, object]:
        workspace = resolve_workspace()
        path = workspace.workspace_root / "profiles" / f"{name}.toml"
        if path.exists() and not force:
            raise ValueError(f"profile already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if source is None:
            content = "[cli]\nformat = \"text\"\n"
        else:
            if not source.exists():
                raise ValueError(f"profile template does not exist: {source}")
            content = source.read_text(encoding="utf-8")
        path.write_text(content, encoding="utf-8")
        workspace.operations.append("config.profile.create", target={"profile": name}, payload={"path": path, "source": source})
        return {"profile": name, "path": str(path), "source": None if source is None else str(source)}


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _selected_profile(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(value, Mapping):
        return None
    selected = value.get("profile")
    return selected if isinstance(selected, str) and selected.strip() else None


__all__ = ["ConfigAdminApplication"]
