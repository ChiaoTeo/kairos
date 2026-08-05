from __future__ import annotations

from pathlib import Path

from kairospy.application.support.launch.application.control import LaunchControl
from kairospy.application.usecases.workspace.application.context import current_cwd, workspace as resolve_workspace, workspace_config
from kairospy.application.usecases.workspace.domain.workspace import OperationJournal
from kairospy.application.usecases.account.application.configuration import AccountStore


class ProjectAdminApplication:
    def init(self, project_name: str, *, force: bool = False) -> str:
        project_root = Path(project_name).expanduser()
        if not project_root.is_absolute():
            project_root = (current_cwd() or Path.cwd()).joinpath(project_root)
        project_root = project_root.resolve()
        kairos_root = project_root / ".kairos"
        config_path = kairos_root / "kairos.toml"
        if config_path.exists() and not force:
            raise ValueError(f"Kairos project already exists: {config_path}")
        kairos_root.mkdir(parents=True, exist_ok=True)
        for directory in ("accounts", "state", "launches", "data", "reference", "orders/journals"):
            (kairos_root / directory).mkdir(parents=True, exist_ok=True)
        config_path.write_text(_workspace_manifest(project_root.name), encoding="utf-8")
        OperationJournal(kairos_root / "state" / "operations.jsonl").append(
            "project.init",
            target={"project": project_root.name},
            payload={"root": project_root, "manifest": config_path},
        )
        return str(project_root)

    def status(self) -> dict[str, object]:
        workspace = resolve_workspace()
        return {
            "root": str(workspace.root),
            "manifest": str(workspace.manifest_path) if workspace.manifest_path is not None else None,
            "timezone": workspace.manifest.timezone_name,
            "language": workspace.manifest.language,
            "workspace_root": str(workspace.workspace_root),
            "accounts": len(AccountStore.load(workspace.accounts_root).list()),
            "launches": len(workspace.launch_index.list()),
            "market_datasets": _count_files(workspace.data_root, (".jsonl", ".json", ".parquet", ".csv")),
            "reference_root": str(workspace.reference_root),
        }

    def doctor(self) -> dict[str, object]:
        workspace = resolve_workspace()
        issues: list[str] = []
        if workspace.manifest_path is None:
            issues.append(".kairos/kairos.toml was not found; using built-in defaults")
        for path in (workspace.accounts_root, workspace.launch_root, workspace.data_root, workspace.reference_root):
            if not path.exists():
                issues.append(f"workspace directory does not exist: {path}")
        return {"valid": not issues, "issues": issues, "workspace": workspace.to_dict()}

    def surface_snapshot(self, *, stale_after_seconds: float = 5.0) -> dict[str, object]:
        config = workspace_config()
        launches = tuple(
            status.to_dict()
            for status in LaunchControl(config.resolve_path(".kairos/launches")).list(
                stale_after_seconds=stale_after_seconds,
            )
        )
        return {
            "project_name": config.project_name or config.root.name,
            "timezone": config.timezone_name,
            "language": config.language,
            "root": config.root,
            "data_root": config.data_root,
            "reference_root": config.reference_root,
            "launches": launches,
        }


def _workspace_manifest(project_name: str) -> str:
    return "\n".join(
        [
            "schema_version = 1",
            "",
            "[project]",
            f'name = "{project_name}"',
            'timezone = "UTC"',
            'language = "en"',
            "",
            "[data]",
            'storage_format = "parquet"',
            "",
            "[cli]",
            'format = "text"',
            "launch_control = true",
            "",
        ]
    )


def _count_files(root: Path, suffixes: tuple[str, ...]) -> int:
    if not root.exists():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file() and path.suffix in suffixes)


__all__ = ["ProjectAdminApplication"]
