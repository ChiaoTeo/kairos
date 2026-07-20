from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .contracts import LiveViewManifest


@dataclass(frozen=True, slots=True)
class LiveViewFreshnessPolicy:
    name: str
    require_max_age: bool = True
    passing_statuses: tuple[str, ...] = ("configured", "healthy")


@dataclass(frozen=True, slots=True)
class LiveViewFreshnessGateResult:
    live_view_id: str
    policy_name: str
    freshness_status: str
    max_age_seconds: int | None
    passed: bool
    reason: str


LIVE_VIEW_CONFIGURED_FRESHNESS_POLICY = LiveViewFreshnessPolicy(
    "live-view-configured",
    require_max_age=True,
    passing_statuses=("configured", "healthy"),
)
PAPER_LIVE_FRESHNESS_POLICY = LiveViewFreshnessPolicy(
    "paper-live-freshness",
    require_max_age=True,
    passing_statuses=("healthy",),
)

LIVE_VIEW_FRESHNESS_POLICIES: Mapping[str, LiveViewFreshnessPolicy] = MappingProxyType({
    LIVE_VIEW_CONFIGURED_FRESHNESS_POLICY.name: LIVE_VIEW_CONFIGURED_FRESHNESS_POLICY,
    PAPER_LIVE_FRESHNESS_POLICY.name: PAPER_LIVE_FRESHNESS_POLICY,
})


def live_view_freshness_policy(name: str) -> LiveViewFreshnessPolicy:
    try:
        return LIVE_VIEW_FRESHNESS_POLICIES[name]
    except KeyError as error:
        raise ValueError(f"unknown Live View freshness policy: {name}") from error


def evaluate_live_view_freshness(
    manifest: LiveViewManifest,
    *,
    policy: LiveViewFreshnessPolicy | None = None,
) -> LiveViewFreshnessGateResult:
    profile = policy or LIVE_VIEW_CONFIGURED_FRESHNESS_POLICY
    max_age_seconds = _max_age_seconds(manifest.live_data_plane.get("freshness"))
    status = manifest.freshness_status.strip()
    if profile.require_max_age and max_age_seconds is None:
        return LiveViewFreshnessGateResult(
            manifest.live_view_id, profile.name, status, max_age_seconds, False,
            f"Live View {manifest.live_view_id} requires freshness.max_age_seconds",
        )
    if status not in profile.passing_statuses:
        return LiveViewFreshnessGateResult(
            manifest.live_view_id, profile.name, status, max_age_seconds, False,
            f"Live View {manifest.live_view_id} freshness status {status!r} does not satisfy {profile.name}",
        )
    return LiveViewFreshnessGateResult(
        manifest.live_view_id, profile.name, status, max_age_seconds, True,
        f"Live View {manifest.live_view_id} satisfies {profile.name}",
    )


def _max_age_seconds(value: object) -> int | None:
    if not isinstance(value, Mapping):
        return None
    raw = value.get("max_age_seconds")
    if raw is None:
        return None
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None
