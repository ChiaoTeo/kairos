from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.support.launch.application.sources import AsyncEventSource, ClockEventSource, CsvEventSource, IntervalClockSource, IterableEventSource, RealtimeClockSource
from kairospy.application.usecases.strategy.application.entrypoint import StrategyEntrypoint, load_strategy_entrypoint
from kairospy.application.usecases.workspace.domain.workspace import KairosWorkspace
from kairospy.application.support.launch.application.configuration import ConfigError, LaunchConfig, load_launch_config
from kairospy.application.usecases.workspace.domain.config import CONFIG_FILENAME, find_manifest_path


@dataclass(frozen=True, slots=True)
class SourceTools:
    env: "LaunchEnvironment"

    def csv_events(self, path: str | Path, **kwargs: object) -> CsvEventSource:
        kwargs.setdefault("default_timezone", self.env.timezone)
        return CsvEventSource(self.env.path(path), **kwargs)

    def iterable(self, source_id: str, events: object) -> IterableEventSource:
        return IterableEventSource(source_id, events)  # type: ignore[arg-type]

    def async_events(self, source_id: str, events: object, *, limit: int | None = None) -> AsyncEventSource:
        return AsyncEventSource(source_id, events, limit=limit)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ClockTools:
    env: "LaunchEnvironment"

    def ticks(self, source_id: str, times: object, **kwargs: object) -> ClockEventSource:
        kwargs.setdefault("default_timezone", self.env.timezone)
        return ClockEventSource(source_id, times, **kwargs)  # type: ignore[arg-type]

    def interval(self, source_id: str, *, start: object, end: object, every: object, **kwargs: object) -> IntervalClockSource:
        kwargs.setdefault("default_timezone", self.env.timezone)
        return IntervalClockSource(source_id, start=start, end=end, every=every, **kwargs)  # type: ignore[arg-type]

    def realtime(self, source_id: str, *, every: object, limit: int | None = None, start_immediately: bool = False, **kwargs: object) -> RealtimeClockSource:
        return RealtimeClockSource(source_id, every=every, limit=limit, start_immediately=start_immediately, **kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class LaunchEnvironment:
    launch_config: LaunchConfig
    run_group_dir: Path
    instance_dir: Path
    launch_instance_id: str
    timezone_name: str
    language: str
    params: Mapping[str, object]
    strategy_params: Mapping[str, object]
    normalized_config: Mapping[str, object]

    @classmethod
    def from_config(
        cls,
        path: str | Path,
        *,
        params: Mapping[str, object] | None = None,
        strategy_params: Mapping[str, object] | None = None,
        instance_dir: str | Path | None = None,
    ) -> "LaunchEnvironment":
        config_path = resolve_config(path)
        launch_config = load_launch_config(config_path)
        workspace = KairosWorkspace.resolve(config_path)
        merged_params = {**_params(launch_config.values), **dict(params or {})}
        merged_strategy_params = {**_strategy_params(launch_config.values), **dict(strategy_params or {})}
        group_directory = _launch_group_directory(launch_config)
        directory, instance_id = _instance_directory(launch_config, group_directory, instance_dir)
        timezone_name = workspace.manifest.timezone_name
        language = workspace.manifest.language
        normalized_config = {
            "launch": {
                "id": launch_config.launch_id,
                "mode": launch_config.mode,
                "strategy": launch_config.strategy,
                "launch_instance_id": instance_id,
            },
            "project": {
                "timezone": timezone_name,
                "language": language,
            },
            "params": dict(merged_params),
            "strategy": {"params": dict(merged_strategy_params)},
            launch_config.mode: dict(_mode_config(launch_config)),
        }
        return cls(
            launch_config=launch_config,
            run_group_dir=group_directory,
            instance_dir=directory,
            launch_instance_id=instance_id,
            timezone_name=timezone_name,
            language=language,
            params=MappingProxyType(merged_params),
            strategy_params=MappingProxyType(merged_strategy_params),
            normalized_config=normalized_config,
        )

    @classmethod
    def from_run(
        cls,
        name_or_path: str | Path,
        *,
        params: Mapping[str, object] | None = None,
        strategy_params: Mapping[str, object] | None = None,
        instance_dir: str | Path | None = None,
    ) -> "LaunchEnvironment":
        return cls.from_config(
            name_or_path,
            params=params,
            strategy_params=strategy_params,
            instance_dir=instance_dir,
        )

    @classmethod
    def open(
        cls,
        name_or_path: str | Path,
        *,
        params: Mapping[str, object] | None = None,
        strategy_params: Mapping[str, object] | None = None,
        instance_dir: str | Path | None = None,
    ) -> "LaunchEnvironment":
        return cls.from_run(
            name_or_path,
            params=params,
            strategy_params=strategy_params,
            instance_dir=instance_dir,
        )

    @property
    def root(self) -> Path:
        return self.launch_config.root

    @property
    def launch_id(self) -> str:
        return self.launch_config.launch_id

    @property
    def mode(self) -> RuntimeMode:
        return RuntimeMode(self.launch_config.mode)

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def sources(self) -> SourceTools:
        return SourceTools(self)

    @property
    def clocks(self) -> ClockTools:
        return ClockTools(self)

    def path(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else (self.root / path).resolve()

    def parse_time(self, value: object) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=self.timezone)
        return parsed

    def load_strategy(self, ref: str | None = None) -> StrategyEntrypoint:
        strategy_ref = ref or self.launch_config.strategy
        if strategy_ref is None:
            raise ValueError("strategy entrypoint is required")
        return load_strategy_entrypoint(
            strategy_ref,
            root=self.root,
            env=self,
            params=self.strategy_params,
        )

def _params(values: Mapping[str, object]) -> dict[str, object]:
    value = values.get("params")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("[params] must be a table")
    return dict(value)


def _strategy_params(values: Mapping[str, object]) -> dict[str, object]:
    strategy = values.get("strategy")
    if strategy is None:
        return {}
    if not isinstance(strategy, Mapping):
        raise ValueError("[strategy] must be a table")
    params = strategy.get("params")
    if params is None:
        return {}
    if not isinstance(params, Mapping):
        raise ValueError("[strategy.params] must be a table")
    return dict(params)


def _mode_config(launch_config: LaunchConfig) -> Mapping[str, object]:
    value = launch_config.values.get(launch_config.mode)
    return value if isinstance(value, Mapping) else {}


def _paths_config(launch_config: LaunchConfig) -> Mapping[str, object]:
    value = launch_config.values.get("paths")
    return value if isinstance(value, Mapping) else {}


def _launch_group_directory(launch_config: LaunchConfig) -> Path:
    mode_config = _mode_config(launch_config)
    paths = _paths_config(launch_config)
    launches_root = mode_config.get("launches_root") or paths.get("launches_root") or ".kairos/launches"
    path = Path(str(launches_root)).expanduser()
    if not path.is_absolute():
        path = launch_config.root / path
    return path.resolve() / launch_config.mode / launch_config.launch_id


def _instance_directory(launch_config: LaunchConfig, group_directory: Path, value: str | Path | None) -> tuple[Path, str]:
    if value is None:
        instance_id = _instance_id()
        return group_directory / "instances" / instance_id, instance_id
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = launch_config.root / path
    path = path.resolve()
    _validate_instance_directory(path, group_directory)
    return path, path.name


def _validate_instance_directory(path: Path, group_directory: Path) -> None:
    group = group_directory.resolve()
    resolved = path.resolve()
    if resolved == group:
        raise ValueError("launch instance directory cannot be the launch group directory; use <launch-group>/instances/<launch-instance-id>")
    if resolved.parent != group / "instances":
        raise ValueError("launch instance directory must be <launch-group>/instances/<launch-instance-id>")
    if not resolved.name.strip():
        raise ValueError("launch instance id is required")


def _instance_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]


def resolve_config(name_or_path: str | Path, *, start: str | Path | None = None) -> Path:
    try:
        workspace = KairosWorkspace.resolve(start)
    except Exception:
        return Path(name_or_path).expanduser()
    return workspace.launch_index.resolve_config_path(name_or_path)


def ensure_launch_registered(
    name: str,
    config_path: str | Path,
    *,
    start: str | Path | None = None,
    project_name: str | None = None,
) -> Path:
    if not name.strip():
        raise ValueError("launch name is required")
    config = Path(config_path).expanduser()
    if not config.is_absolute():
        base = Path.cwd() if start is None else Path(start).expanduser()
        config = (base if base.is_dir() else base.parent) / config
    config = config.resolve()
    if not config.exists():
        raise ValueError(f"launch config does not exist: {config}")
    workspace_root = _ensure_workspace(start=start, project_name=project_name)
    workspace = KairosWorkspace.resolve(workspace_root)
    try:
        existing = workspace.launch_index.get(name)
    except ConfigError:
        workspace.launch_index.register(name, config)
    else:
        if existing.config_path.resolve() != config:
            workspace.launch_index.register(name, config)
    return workspace.launch_index.get(name).config_path


def _ensure_workspace(*, start: str | Path | None, project_name: str | None) -> Path:
    base = Path.cwd() if start is None else Path(start).expanduser()
    base = base.resolve()
    if base.is_file():
        base = base.parent
    manifest = find_manifest_path(base)
    if manifest is not None:
        return manifest.parent.parent if manifest.parent.name == ".kairos" else manifest.parent
    kairos_root = base / ".kairos"
    for directory in ("accounts", "state", "launches", "data", "reference", "orders/journals"):
        (kairos_root / directory).mkdir(parents=True, exist_ok=True)
    (kairos_root / CONFIG_FILENAME).write_text(_workspace_manifest(project_name or base.name), encoding="utf-8")
    return base


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


__all__ = ["LaunchEnvironment", "SourceTools", "ensure_launch_registered", "resolve_config"]
