"""Workspace-owned configuration use cases."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from kairospy.system.domain.workspace import Workspace
from .references import ConfigurationReferenceApplication
from .resource_lifecycle import WorkspaceResourceLifecycleApplication


_SECRET_KEYS = frozenset(
    {
        "api_key",
        "api_secret",
        "authorization",
        "password",
        "private_key",
        "secret",
        "signing_secret",
        "token",
    }
)


@dataclass(frozen=True, slots=True)
class ConfigApplication:
    workspace: Workspace

    def paths(self) -> dict[str, str]:
        paths = self.workspace.paths
        return {
            "root": str(paths.root),
            "manifest": str(paths.manifest),
            "config": str(paths.config),
            "state": str(paths.state),
            "run": str(paths.run),
            "logs": str(paths.logs),
            "launches": str(paths.launches),
            "cli_format": self.workspace.cli_format,
        }

    def manifest(self) -> dict[str, Any]:
        return tomllib.loads(self.workspace.paths.manifest.read_text(encoding="utf-8"))

    def show(self, name: str | None = None) -> dict[str, Any]:
        if name is not None:
            path = self._config_path(name)
            return self._read(path)
        result: dict[str, Any] = {}
        for path in sorted(self.workspace.paths.config.rglob("*")):
            if path.is_file() and path.suffix == ".toml":
                result[str(path.relative_to(self.workspace.paths.config))] = self._read(
                    path
                )
        return result

    def doctor(self) -> dict[str, Any]:
        issues: list[str] = []
        try:
            manifest = self.manifest()
            if manifest.get("version") != 1:
                issues.append("workspace manifest version must be 1")
        except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
            issues.append(f"invalid workspace manifest: {error}")
        missing = [
            str(path)
            for path in (
                self.workspace.paths.config,
                self.workspace.paths.state,
                self.workspace.paths.run,
                self.workspace.paths.logs,
                self.workspace.paths.launches,
            )
            if not path.is_dir()
        ]
        launches: list[dict[str, Any]] = []
        from kairospy.system.apps.workspace.services.templates import (
            project_template_status,
        )

        templates = project_template_status(self.workspace)
        missing_template_files: list[tuple[str, str]] = []
        for installation in templates:
            files = installation.get("files")
            if not isinstance(files, list):
                continue
            for file in files:
                if isinstance(file, Mapping) and file.get("status") == "missing":
                    missing_template_files.append(
                        (str(installation["installation_id"]), str(file["path"]))
                    )
        issues.extend(
            f"template {installation_id}: generated file is missing: {path}"
            for installation_id, path in missing_template_files
        )
        launch_root = self.workspace.paths.config / "launches"
        if launch_root.is_dir():
            from kairospy.system.apps.launch.application import (
                LaunchConfigurationApplication,
            )

            application = LaunchConfigurationApplication()
            account_ids = self._configured_account_ids()
            for path in sorted(launch_root.glob("*.toml")):
                report = application.validate(
                    path, workspace_root=self.workspace.paths.root
                )
                readiness_issues: list[str] = []
                if report["valid"]:
                    config = application.load(
                        path, workspace_root=self.workspace.paths.root
                    )
                    readiness_issues.extend(
                        f"account {account_ref!r} is not configured"
                        for account_ref in config.account_refs
                        if account_ref not in account_ids
                    )
                    replay = config.plan().backtest_replay_file
                    if replay is not None and not replay.is_file():
                        readiness_issues.append(f"replay file does not exist: {replay}")
                launch = {
                    "launch_id": path.stem,
                    "path": str(path),
                    "valid": bool(report["valid"]),
                    "issues": list(report["issues"]),
                    "ready": bool(report["valid"]) and not readiness_issues,
                    "readiness_issues": readiness_issues,
                }
                launches.append(launch)
                issues.extend(
                    f"launch {path.stem}: {issue}" for issue in report["issues"]
                )
                issues.extend(
                    f"launch {path.stem}: {issue}" for issue in readiness_issues
                )
        ready_launches = [launch for launch in launches if launch["ready"]]
        invalid_launches = [launch for launch in launches if not launch["valid"]]
        unready_launches = [
            launch for launch in launches if launch["valid"] and not launch["ready"]
        ]
        if missing_template_files:
            next_steps = [
                "restore the missing launch resources and user-owned template "
                "files, or remove the stale installation record, then run "
                "'kairos project doctor'"
            ]
        elif invalid_launches:
            next_steps = [
                f"kairos launch diagnose validate {launch['launch_id']}"
                for launch in invalid_launches
            ]
        elif unready_launches:
            next_steps = [
                "restore the missing launch resources listed in issues, then run 'kairos project doctor'"
            ]
        elif ready_launches:
            next_steps = [f"kairos launch start {ready_launches[0]['launch_id']}"]
        else:
            next_steps = ["kairos project scaffold --template backtest"]
        return {
            "ok": not issues and not missing,
            "ready": bool(ready_launches) and not issues and not missing,
            "issues": issues,
            "missing_directories": missing,
            "launches": launches,
            "templates": templates,
            "next_steps": next_steps,
        }

    def _configured_account_ids(self) -> set[str]:
        root = self.workspace.paths.account_config().parent
        result: set[str] = set()
        if not root.is_dir():
            return result
        for path in sorted(root.glob("*.toml")):
            try:
                value = self._read(path)
            except (OSError, ValueError, tomllib.TOMLDecodeError):
                continue
            account = value.get("account")
            if isinstance(account, Mapping):
                account_id = account.get("id", path.stem)
                if isinstance(account_id, str) and account_id.strip():
                    result.add(account_id.strip())
            accounts = value.get("accounts")
            if isinstance(accounts, Mapping):
                result.update(str(account_id) for account_id in accounts)
        return result

    def explain(self, name: str) -> dict[str, Any]:
        path = self._config_path(name)
        return {
            "name": name,
            "path": str(path),
            "exists": path.exists(),
            "value": self._read(path) if path.exists() else {},
        }

    def operations(self) -> list[str]:
        return [
            "paths",
            "show",
            "manifest",
            "doctor",
            "explain",
            "profile list",
            "profile use",
            "profile create",
            "agent status",
            "agent setup",
        ]

    def profiles(self) -> list[str]:
        root = self.workspace.paths.config / "profiles"
        return (
            sorted(path.stem for path in root.glob("*.toml")) if root.is_dir() else []
        )

    def create_profile(self, name: str) -> Path:
        if not name or "/" in name or "\\" in name:
            raise ValueError("profile name must be a path-safe value")
        root = self.workspace.paths.config / "profiles"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{name}.toml"
        if path.exists():
            raise FileExistsError(path)
        path.write_text("version = 1\n", encoding="utf-8")
        return path

    def use_profile(self, name: str) -> Path:
        if name not in self.profiles():
            raise FileNotFoundError(f"profile does not exist: {name}")
        path = self.workspace.paths.config / "active-profile"
        path.write_text(name + "\n", encoding="utf-8")
        return path

    def _config_path(self, name: str) -> Path:
        if not name or any(part in {".", ".."} for part in Path(name).parts):
            raise ValueError("invalid config name")
        path = (self.workspace.paths.config / name).resolve()
        path.relative_to(self.workspace.paths.config.resolve())
        if path.suffix == ".json":
            raise ValueError("configuration must use TOML; JSON is output-only")
        if path.suffix != ".toml":
            path = path.with_suffix(".toml")
        return path

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        result = value if isinstance(value, dict) else {"value": value}
        return _redact_secrets(result)


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "***" if str(key).lower() in _SECRET_KEYS else _redact_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


__all__ = [
    "ConfigApplication",
    "ConfigurationReferenceApplication",
    "WorkspaceResourceLifecycleApplication",
]
