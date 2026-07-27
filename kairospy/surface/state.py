from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from kairospy.config import KairosConfig, load_config
from kairospy.runtime import list_run_daemons


@dataclass(frozen=True, slots=True)
class SurfaceRunSummary:
    mode: str
    run_id: str
    status: str
    phase: str
    heartbeat_age_seconds: float | None
    strategy: str
    config_file: str
    log_file: str

    @property
    def active(self) -> bool:
        return self.status in {"running", "starting", "stopping", "stale"}


@dataclass(frozen=True, slots=True)
class SurfaceSnapshot:
    project_name: str
    root: Path
    data_root: Path
    reference_root: Path
    current_product: str
    refreshed_at: datetime
    refresh_interval_seconds: float
    runs: tuple[SurfaceRunSummary, ...]

    @property
    def active_runs(self) -> tuple[SurfaceRunSummary, ...]:
        return tuple(run for run in self.runs if run.active)

    @property
    def stale_runs(self) -> tuple[SurfaceRunSummary, ...]:
        return tuple(run for run in self.runs if run.status == "stale")


class SurfaceContext:
    def __init__(
        self,
        *,
        product: str = "top",
        refresh_interval_seconds: float = 2.0,
        stale_after_seconds: float = 5.0,
        config: KairosConfig | None = None,
    ) -> None:
        if refresh_interval_seconds <= 0:
            raise ValueError("refresh_interval_seconds must be positive")
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self.product = product
        self.refresh_interval_seconds = refresh_interval_seconds
        self.stale_after_seconds = stale_after_seconds
        self._config = config
        self._snapshot: SurfaceSnapshot | None = None

    def set_product(self, product: str) -> None:
        self.product = product
        self._snapshot = None

    def snapshot(self, *, force: bool = False) -> SurfaceSnapshot:
        cached = self._snapshot
        now = datetime.now(timezone.utc)
        if not force and cached is not None:
            age = (now - cached.refreshed_at).total_seconds()
            if age < self.refresh_interval_seconds and cached.current_product == self.product:
                return cached
        config = self._config or load_config()
        runs = tuple(
            _run_summary(status.to_dict())
            for status in list_run_daemons(stale_after_seconds=self.stale_after_seconds)
        )
        snapshot = SurfaceSnapshot(
            project_name=config.project_name or config.root.name,
            root=config.root,
            data_root=config.data_root,
            reference_root=config.reference_root,
            current_product=self.product,
            refreshed_at=now,
            refresh_interval_seconds=self.refresh_interval_seconds,
            runs=runs,
        )
        self._snapshot = snapshot
        return snapshot

    def refresh(self) -> SurfaceSnapshot:
        return self.snapshot(force=True)


def render_surface_overview(snapshot: SurfaceSnapshot) -> str:
    lines = [
        f"Kairos  {snapshot.project_name}",
        (
            f"view {snapshot.current_product} | "
            f"runs {len(snapshot.active_runs)} active / {len(snapshot.runs)} total | "
            f"refresh {snapshot.refresh_interval_seconds:g}s"
        ),
        f"root    {snapshot.root}",
    ]
    if snapshot.stale_runs:
        stale = ", ".join(f"{run.mode}:{run.run_id}" for run in snapshot.stale_runs[:3])
        extra = "" if len(snapshot.stale_runs) <= 3 else f" +{len(snapshot.stale_runs) - 3}"
        lines.append(f"stale   {stale}{extra}")
    return "\n".join(lines)


def render_run_strip(snapshot: SurfaceSnapshot, *, limit: int = 6) -> str:
    if not snapshot.runs:
        return "Recent Runs\n  no recorded runs"
    runs = snapshot.runs[:limit]
    run_id_width = min(max(18, *(len(run.run_id) for run in runs)), 28)
    lines = [
        "Recent Runs",
        f"  #  {'mode':<8}  {'run':<{run_id_width}}  {'status':<8}  {'age':<8}  detail",
        f"  -  {'-' * 8}  {'-' * run_id_width}  {'-' * 8}  {'-' * 8}  ------",
    ]
    for index, run in enumerate(runs, start=1):
        detail = _run_detail(run)
        age = "-"
        if run.heartbeat_age_seconds is not None:
            age = f"{run.heartbeat_age_seconds:.1f}s"
        lines.append(
            f"  {index:<2} {run.mode:<8}  {_clip(run.run_id, run_id_width):<{run_id_width}}  "
            f"{run.status:<8}  {age:<8}  {detail}"
        )
    if len(snapshot.runs) > limit:
        lines.append(f"  +{len(snapshot.runs) - limit} more")
    return "\n".join(lines)


def _run_summary(payload: Mapping[str, object]) -> SurfaceRunSummary:
    context = payload.get("context")
    context = context if isinstance(context, Mapping) else {}
    result = payload.get("result")
    result = result if isinstance(result, Mapping) else {}
    return SurfaceRunSummary(
        mode=str(payload.get("mode") or ""),
        run_id=str(payload.get("run_id") or ""),
        status=str(payload.get("status") or payload.get("phase") or "unknown"),
        phase=str(payload.get("phase") or "unknown"),
        heartbeat_age_seconds=_optional_float(payload.get("heartbeat_age_seconds")),
        strategy=str(context.get("strategy") or result.get("strategy_id") or result.get("latest_strategy_id") or ""),
        config_file=str(context.get("config_file") or ""),
        log_file=str(payload.get("log_file") or context.get("log_file") or ""),
    )


def _optional_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _run_detail(run: SurfaceRunSummary) -> str:
    if run.strategy:
        return run.strategy
    if run.config_file:
        return f"config:{Path(run.config_file).name}"
    if run.log_file:
        return "log"
    return ""


def _clip(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 1:
        return value[:width]
    return value[: width - 1] + "~"


__all__ = [
    "SurfaceContext",
    "SurfaceRunSummary",
    "SurfaceSnapshot",
    "render_run_strip",
    "render_surface_overview",
]
