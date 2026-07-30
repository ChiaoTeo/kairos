from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

from kairospy.application.runtime import RuntimeMode
from kairospy.application.runtime.sources import AsyncEventSource, ClockEventSource, CsvEventSource, IntervalClockSource, IterableEventSource, RealtimeClockSource
from kairospy.application.strategy.entrypoint import StrategyEntrypoint, load_strategy_entrypoint
from kairospy.application.system.builder import RunBuilder
from kairospy.application.system.workspace import KairosWorkspace
from kairospy.config import CONFIG_FILENAME, ConfigError, RunConfig, find_manifest_path, load_run_config


@dataclass(frozen=True, slots=True)
class SourceTools:
    env: "RunEnvironment"

    def csv_events(self, path: str | Path, **kwargs: object) -> CsvEventSource:
        kwargs.setdefault("default_timezone", self.env.timezone)
        return CsvEventSource(self.env.path(path), **kwargs)

    def iterable(self, source_id: str, events: object) -> IterableEventSource:
        return IterableEventSource(source_id, events)  # type: ignore[arg-type]

    def async_events(self, source_id: str, events: object, *, limit: int | None = None) -> AsyncEventSource:
        return AsyncEventSource(source_id, events, limit=limit)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ClockTools:
    env: "RunEnvironment"

    def ticks(self, source_id: str, times: object, **kwargs: object) -> ClockEventSource:
        kwargs.setdefault("default_timezone", self.env.timezone)
        return ClockEventSource(source_id, times, **kwargs)  # type: ignore[arg-type]

    def interval(self, source_id: str, *, start: object, end: object, every: object, **kwargs: object) -> IntervalClockSource:
        kwargs.setdefault("default_timezone", self.env.timezone)
        return IntervalClockSource(source_id, start=start, end=end, every=every, **kwargs)  # type: ignore[arg-type]

    def realtime(self, source_id: str, *, every: object, limit: int | None = None, start_immediately: bool = False, **kwargs: object) -> RealtimeClockSource:
        return RealtimeClockSource(source_id, every=every, limit=limit, start_immediately=start_immediately, **kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class RunEnvironment:
    run_config: RunConfig
    run_group_dir: Path
    instance_dir: Path
    run_instance_id: str
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
    ) -> "RunEnvironment":
        config_path = resolve_config(path)
        run_config = load_run_config(config_path)
        workspace = KairosWorkspace.resolve(config_path)
        merged_params = {**_params(run_config.values), **dict(params or {})}
        merged_strategy_params = {**_strategy_params(run_config.values), **dict(strategy_params or {})}
        group_directory = _run_group_directory(run_config)
        directory, instance_id = _instance_directory(run_config, group_directory, instance_dir)
        timezone_name = workspace.manifest.timezone_name
        language = workspace.manifest.language
        normalized_config = {
            "run": {
                "id": run_config.run_id,
                "mode": run_config.mode,
                "strategy": run_config.strategy,
                "run_instance_id": instance_id,
            },
            "project": {
                "timezone": timezone_name,
                "language": language,
            },
            "params": dict(merged_params),
            "strategy": {"params": dict(merged_strategy_params)},
            run_config.mode: dict(_mode_config(run_config)),
        }
        return cls(
            run_config=run_config,
            run_group_dir=group_directory,
            instance_dir=directory,
            run_instance_id=instance_id,
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
    ) -> "RunEnvironment":
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
    ) -> "RunEnvironment":
        return cls.from_run(
            name_or_path,
            params=params,
            strategy_params=strategy_params,
            instance_dir=instance_dir,
        )

    @property
    def root(self) -> Path:
        return self.run_config.root

    @property
    def run_id(self) -> str:
        return self.run_config.run_id

    @property
    def mode(self) -> RuntimeMode:
        return RuntimeMode(self.run_config.mode)

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

    def builder(self) -> RunBuilder:
        return RunBuilder(self)

    def load_strategy(self, ref: str | None = None) -> StrategyEntrypoint:
        strategy_ref = ref or self.run_config.strategy
        if strategy_ref is None:
            raise ValueError("strategy entrypoint is required")
        return load_strategy_entrypoint(
            strategy_ref,
            root=self.root,
            env=self,
            params=self.strategy_params,
        )

    def run(self, *, strategy: object, sources: object = (), clocks: object = (), echo: bool = False):
        return self.builder().strategy(strategy).sources(sources).clocks(clocks).echo(echo).run()  # type: ignore[arg-type]


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


def _mode_config(run_config: RunConfig) -> Mapping[str, object]:
    value = run_config.values.get(run_config.mode)
    return value if isinstance(value, Mapping) else {}


def _paths_config(run_config: RunConfig) -> Mapping[str, object]:
    value = run_config.values.get("paths")
    return value if isinstance(value, Mapping) else {}


def _run_group_directory(run_config: RunConfig) -> Path:
    mode_config = _mode_config(run_config)
    paths = _paths_config(run_config)
    runs_root = mode_config.get("runs_root") or paths.get("runs_root") or ".kairos/runs"
    path = Path(str(runs_root)).expanduser()
    if not path.is_absolute():
        path = run_config.root / path
    return path.resolve() / run_config.mode / run_config.run_id


def _instance_directory(run_config: RunConfig, group_directory: Path, value: str | Path | None) -> tuple[Path, str]:
    if value is None:
        instance_id = _instance_id()
        return group_directory / "instances" / instance_id, instance_id
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = run_config.root / path
    path = path.resolve()
    _validate_instance_directory(path, group_directory)
    return path, path.name


def _validate_instance_directory(path: Path, group_directory: Path) -> None:
    group = group_directory.resolve()
    resolved = path.resolve()
    if resolved == group:
        raise ValueError("run instance directory cannot be the run group directory; use <run-group>/instances/<run-instance-id>")
    if resolved.parent != group / "instances":
        raise ValueError("run instance directory must be <run-group>/instances/<run-instance-id>")
    if not resolved.name.strip():
        raise ValueError("run instance id is required")


def _instance_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]


def resolve_config(name_or_path: str | Path, *, start: str | Path | None = None) -> Path:
    try:
        workspace = KairosWorkspace.resolve(start)
    except Exception:
        return Path(name_or_path).expanduser()
    return workspace.run_index.resolve_config_path(name_or_path)


def ensure_run_registered(
    name: str,
    config_path: str | Path,
    *,
    start: str | Path | None = None,
    project_name: str | None = None,
) -> Path:
    if not name.strip():
        raise ValueError("run name is required")
    config = Path(config_path).expanduser()
    if not config.is_absolute():
        base = Path.cwd() if start is None else Path(start).expanduser()
        config = (base if base.is_dir() else base.parent) / config
    config = config.resolve()
    if not config.exists():
        raise ValueError(f"run config does not exist: {config}")
    workspace_root = _ensure_workspace(start=start, project_name=project_name)
    workspace = KairosWorkspace.resolve(workspace_root)
    try:
        existing = workspace.run_index.get(name)
    except ConfigError:
        workspace.run_index.register(name, config)
    else:
        if existing.config_path.resolve() != config:
            workspace.run_index.register(name, config)
    return workspace.run_index.get(name).config_path


def _ensure_workspace(*, start: str | Path | None, project_name: str | None) -> Path:
    base = Path.cwd() if start is None else Path(start).expanduser()
    base = base.resolve()
    if base.is_file():
        base = base.parent
    manifest = find_manifest_path(base)
    if manifest is not None:
        return manifest.parent.parent if manifest.parent.name == ".kairos" else manifest.parent
    kairos_root = base / ".kairos"
    for directory in ("accounts", "state", "runs", "data", "reference", "orders/journals"):
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
            "run_control = true",
            "",
        ]
    )


__all__ = ["RunEnvironment", "SourceTools", "ensure_run_registered", "resolve_config"]
