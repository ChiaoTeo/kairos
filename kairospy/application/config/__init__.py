"""Workspace-owned configuration use cases."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..workspace import Workspace


@dataclass(frozen=True, slots=True)
class ConfigApplication:
    workspace: Workspace

    def paths(self) -> dict[str, str]:
        paths = self.workspace.paths
        return {
            "root": str(paths.root), "manifest": str(paths.manifest),
            "config": str(paths.config), "state": str(paths.state),
            "run": str(paths.run), "logs": str(paths.logs),
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
                result[str(path.relative_to(self.workspace.paths.config))] = self._read(path)
        return result

    def doctor(self) -> dict[str, Any]:
        issues: list[str] = []
        try:
            manifest = self.manifest()
            if manifest.get("version") != 1:
                issues.append("workspace manifest version must be 1")
        except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
            issues.append(f"invalid workspace manifest: {error}")
        missing = [str(path) for path in (self.workspace.paths.config, self.workspace.paths.state,
                                           self.workspace.paths.run, self.workspace.paths.logs,
                                           self.workspace.paths.launches) if not path.is_dir()]
        return {"ok": not issues and not missing, "issues": issues, "missing_directories": missing}

    def explain(self, name: str) -> dict[str, Any]:
        path = self._config_path(name)
        return {"name": name, "path": str(path), "exists": path.exists(), "value": self._read(path) if path.exists() else {}}

    def operations(self) -> list[str]:
        return ["paths", "show", "manifest", "doctor", "explain", "profile list", "profile use", "profile create"]

    def profiles(self) -> list[str]:
        root = self.workspace.paths.config / "profiles"
        return sorted(path.stem for path in root.glob("*.toml")) if root.is_dir() else []

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
        return value if isinstance(value, dict) else {"value": value}


__all__ = ["ConfigApplication"]
