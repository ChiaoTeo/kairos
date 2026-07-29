from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import Any, Mapping
import json
import tomllib

from kairospy.application.system.workspace import KairosWorkspace
from kairospy.config import find_manifest_path, load_config


_cwd: ContextVar[tuple[Path, Path] | None] = ContextVar("kairos_facade_cwd", default=None)
_profile: ContextVar[tuple[str, Path] | None] = ContextVar("kairos_facade_profile", default=None)


class ProjectNotFound(ValueError):
    def __init__(self, start: str | Path | None = None) -> None:
        base = Path.cwd() if start is None else Path(start)
        self.start = base.expanduser().resolve()
        super().__init__(
            f"No Kairos project found from {self.start}. "
            f"Run `kairospy project init {self.start}` first."
        )


def set_cli_context(*, cwd: str | Path | None, profile: str | None = None) -> None:
    _cwd.set(None if cwd is None else (Path(cwd).expanduser().resolve(), Path.cwd().resolve()))
    _profile.set(None if profile is None else (profile, Path.cwd().resolve()))


def current_cwd() -> Path | None:
    value = _cwd.get()
    if value is None:
        return None
    cwd, origin = value
    if origin != Path.cwd().resolve():
        return None
    return cwd


def current_profile_name(workspace: KairosWorkspace | None = None) -> str | None:
    explicit = _profile.get()
    current_process_cwd = Path.cwd().resolve()
    if explicit is not None:
        name, origin = explicit
        if origin == current_process_cwd:
            return name
    if workspace is None:
        workspace = globals()["workspace"]()
    return _selected_profile(workspace.state_root / "selection.json")


def workspace() -> KairosWorkspace:
    start = current_cwd()
    if find_manifest_path(start) is None:
        raise ProjectNotFound(start)
    return KairosWorkspace.resolve(start)


def workspace_config():
    start = current_cwd()
    path = find_manifest_path(start)
    if path is None:
        raise ProjectNotFound(start)
    return load_config(path)


def ensure_project_exists(start: str | Path | None = None) -> None:
    if find_manifest_path(start) is None:
        raise ProjectNotFound(start)


def profile_values(name: str | None = None) -> Mapping[str, Any]:
    workspace = globals()["workspace"]()
    profile_name = name or current_profile_name(workspace)
    if profile_name is None:
        return {}
    path = workspace.workspace_root / "profiles" / f"{profile_name}.toml"
    if not path.exists():
        raise ValueError(f"profile does not exist: {path}")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def profile_cli_format() -> str | None:
    values = profile_values()
    cli = values.get("cli")
    if not isinstance(cli, Mapping):
        return None
    value = cli.get("format")
    return value if isinstance(value, str) and value else None


def workspace_cli_format() -> str | None:
    values = workspace_config().values
    cli = values.get("cli")
    if not isinstance(cli, Mapping):
        return None
    value = cli.get("format")
    return value if isinstance(value, str) and value else None


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


__all__ = [
    "ProjectNotFound",
    "current_cwd",
    "current_profile_name",
    "ensure_project_exists",
    "profile_cli_format",
    "profile_values",
    "set_cli_context",
    "workspace",
    "workspace_cli_format",
    "workspace_config",
]
