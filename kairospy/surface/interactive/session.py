from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from kairospy.application.support.system.application.facade.project import ProjectFacade


@dataclass(frozen=True, slots=True)
class SurfaceLaunchSummary:
    mode: str
    launch_id: str
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
    launches: tuple[SurfaceLaunchSummary, ...]

    @property
    def active_launches(self) -> tuple[SurfaceLaunchSummary, ...]:
        return tuple(launch for launch in self.launches if launch.active)

    @property
    def stale_launches(self) -> tuple[SurfaceLaunchSummary, ...]:
        return tuple(launch for launch in self.launches if launch.status == "stale")


class SurfaceContext:
    def __init__(
        self,
        *,
        product: str = "top",
        refresh_interval_seconds: float = 2.0,
        stale_after_seconds: float = 5.0,
        project_facade: ProjectFacade | None = None,
    ) -> None:
        if refresh_interval_seconds <= 0:
            raise ValueError("refresh_interval_seconds must be positive")
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self.product = product
        self.refresh_interval_seconds = refresh_interval_seconds
        self.stale_after_seconds = stale_after_seconds
        self._project_facade = project_facade or ProjectFacade()
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
        payload = self._project_facade.surface_snapshot(stale_after_seconds=self.stale_after_seconds)
        snapshot = SurfaceSnapshot(
            project_name=str(payload["project_name"]),
            root=Path(payload["root"]),
            data_root=Path(payload["data_root"]),
            reference_root=Path(payload["reference_root"]),
            current_product=self.product,
            refreshed_at=now,
            refresh_interval_seconds=self.refresh_interval_seconds,
            launches=tuple(_launch_summary(status) for status in payload["launches"] if isinstance(status, Mapping)),
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
            f"launches {len(snapshot.active_launches)} active / {len(snapshot.launches)} total | "
            f"refresh {snapshot.refresh_interval_seconds:g}s"
        ),
        f"root    {snapshot.root}",
    ]
    if snapshot.stale_launches:
        stale = ", ".join(f"{launch.mode}:{launch.launch_id}" for launch in snapshot.stale_launches[:3])
        extra = "" if len(snapshot.stale_launches) <= 3 else f" +{len(snapshot.stale_launches) - 3}"
        lines.append(f"stale   {stale}{extra}")
    return "\n".join(lines)


def render_launch_strip(snapshot: SurfaceSnapshot, *, limit: int = 6) -> str:
    if not snapshot.launches:
        return "Recent Launches\n  no recorded launches"
    launches = snapshot.launches[:limit]
    launch_id_width = min(max(18, *(len(launch.launch_id) for launch in launches)), 28)
    lines = [
        "Recent Launches",
        f"  #  {'mode':<8}  {'launch':<{launch_id_width}}  {'status':<8}  {'age':<8}  detail",
        f"  -  {'-' * 8}  {'-' * launch_id_width}  {'-' * 8}  {'-' * 8}  ------",
    ]
    for index, launch in enumerate(launches, start=1):
        detail = _launch_detail(launch)
        age = "-"
        if launch.heartbeat_age_seconds is not None:
            age = f"{launch.heartbeat_age_seconds:.1f}s"
        lines.append(
            f"  {index:<2} {launch.mode:<8}  {_clip(launch.launch_id, launch_id_width):<{launch_id_width}}  "
            f"{launch.status:<8}  {age:<8}  {detail}"
        )
    if len(snapshot.launches) > limit:
        lines.append(f"  +{len(snapshot.launches) - limit} more")
    return "\n".join(lines)


def _launch_summary(payload: Mapping[str, object]) -> SurfaceLaunchSummary:
    context = payload.get("context")
    context = context if isinstance(context, Mapping) else {}
    result = payload.get("result")
    result = result if isinstance(result, Mapping) else {}
    return SurfaceLaunchSummary(
        mode=str(payload.get("mode") or ""),
        launch_id=str(payload.get("launch_id") or ""),
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


def _launch_detail(launch: SurfaceLaunchSummary) -> str:
    if launch.strategy:
        return launch.strategy
    if launch.config_file:
        return f"config:{Path(launch.config_file).name}"
    if launch.log_file:
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
    "SurfaceLaunchSummary",
    "SurfaceSnapshot",
    "render_launch_strip",
    "render_surface_overview",
]
