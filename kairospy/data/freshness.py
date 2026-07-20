from __future__ import annotations

import asyncio
from dataclasses import dataclass
from inspect import isawaitable
import json
from pathlib import Path
from types import MappingProxyType
from typing import Awaitable, Callable, Mapping

from .contracts import LiveViewManifest


@dataclass(frozen=True, slots=True)
class LiveViewFreshnessPolicy:
    name: str
    require_max_age: bool = True
    passing_statuses: tuple[str, ...] = ("configured", "healthy")
    require_channel_diagnostics: bool = False


@dataclass(frozen=True, slots=True)
class LiveViewFreshnessGateResult:
    live_view_id: str
    policy_name: str
    freshness_status: str
    max_age_seconds: int | None
    channel_diagnostics: Mapping[str, object]
    channel_failures: tuple[str, ...]
    passed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class LiveViewSubscriptionBinding:
    name: str
    dataset_id: str
    live_view_id: str
    artifact_ref: str
    event_source_contract: str
    channel_contract: str
    transport: str
    freshness_gate: LiveViewFreshnessGateResult

    def to_primitive(self) -> dict[str, object]:
        return {
            "name": self.name,
            "dataset": self.dataset_id,
            "live_view_id": self.live_view_id,
            "artifact_ref": self.artifact_ref,
            "event_source_contract": self.event_source_contract,
            "channel_contract": self.channel_contract,
            "transport": self.transport,
            "freshness_gate": freshness_gate_to_primitive(self.freshness_gate),
        }


@dataclass(frozen=True, slots=True)
class LiveViewFreshnessMonitor:
    manifest_path: Path
    evidence_provider: Callable[[], Mapping[str, object] | Awaitable[Mapping[str, object]]]
    interval_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("Live View freshness monitor interval must be positive")

    async def run(self) -> None:
        while True:
            await self.poll_once()
            await asyncio.sleep(self.interval_seconds)

    async def poll_once(self) -> LiveViewManifest:
        evidence = self.evidence_provider()
        if isawaitable(evidence):
            evidence = await evidence
        return update_live_view_manifest_freshness(self.manifest_path, evidence)


LIVE_VIEW_CONFIGURED_FRESHNESS_POLICY = LiveViewFreshnessPolicy(
    "live-view-configured",
    require_max_age=True,
    passing_statuses=("configured", "healthy"),
)
PAPER_LIVE_FRESHNESS_POLICY = LiveViewFreshnessPolicy(
    "paper-live-freshness",
    require_max_age=True,
    passing_statuses=("healthy",),
    require_channel_diagnostics=True,
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


def live_view_channel_diagnostics(value: Mapping[str, object]) -> Mapping[str, object]:
    return {
        "capacity": _optional_int(value.get("channel_capacity") or value.get("capacity")),
        "peak_depth": _optional_int(value.get("peak_channel_depth") or value.get("peak_depth")),
        "peak_utilization": _optional_float(value.get("peak_channel_utilization") or value.get("peak_utilization")),
        "dropped": _optional_int(value.get("channel_dropped") or value.get("dropped")) or 0,
        "overflow": _optional_int(value.get("channel_overflow") or value.get("overflow") or value.get("overflows")) or 0,
        "sequence_gaps": _optional_int(value.get("sequence_gaps") or value.get("sequence_gap_count")) or 0,
        "conflated": _optional_int(value.get("conflated")) or 0,
        "reconnects": _optional_int(value.get("reconnect_count") or value.get("reconnects")) or 0,
    }


def live_view_freshness_evidence(
    service: object,
    channel: object,
    *,
    source: str,
    stream_id: str,
) -> Mapping[str, object]:
    metrics = getattr(channel, "metrics", None)
    capture = getattr(service, "canonical_capture", None)
    capture_manifest = getattr(capture, "_finalized", None)
    event_count = _optional_int(getattr(service, "canonical_events", None)) or 0
    ignored = _optional_int(getattr(service, "ignored_messages", None)) or 0
    dropped = _optional_int(getattr(metrics, "dropped", None)) or 0
    overflow = _optional_int(getattr(metrics, "overflow", None)) or 0
    sequence_gaps = _optional_int(getattr(metrics, "sequence_gaps", None)) or dropped
    passed = event_count > 0 and ignored == 0 and dropped == 0 and overflow == 0 and sequence_gaps == 0
    return {
        "passed": passed,
        "source": source,
        "stream_id": stream_id,
        "event_count": event_count,
        "raw_message_count": _optional_int(getattr(service, "raw_messages", None)) or event_count,
        "canonical_event_count": event_count,
        "ignored_message_count": ignored,
        "reconnect_count": _optional_int(getattr(service, "reconnects", None)) or 0,
        "channel_capacity": _optional_int(getattr(metrics, "capacity", None)),
        "peak_channel_depth": _optional_int(getattr(metrics, "peak_depth", None)),
        "channel_dropped": dropped,
        "channel_overflow": overflow,
        "sequence_gaps": sequence_gaps,
        "artifact": str(getattr(capture_manifest, "event_path", "") or ""),
        "audit_hash": str(getattr(capture_manifest, "content_sha256", "") or ""),
    }


def update_live_view_manifest_freshness(
    manifest_path: str | Path,
    evidence: Mapping[str, object],
) -> LiveViewManifest:
    path = Path(manifest_path)
    payload = load_live_view_manifest(path).to_primitive()
    live_data_plane = dict(payload.get("live_data_plane", {}))
    channel_diagnostics = dict(live_view_channel_diagnostics(evidence))
    channel_failures = _channel_failures(channel_diagnostics, require=True)
    live_data_plane["channel_diagnostics"] = channel_diagnostics
    live_data_plane["freshness_evidence"] = {
        "artifact": str(evidence.get("artifact") or ""),
        "audit_hash": str(evidence.get("audit_hash") or ""),
        "source": str(evidence.get("source") or ""),
        "stream_id": str(evidence.get("stream_id") or ""),
        "event_count": _optional_int(evidence.get("event_count")) or 0,
        "channel_failures": tuple(channel_failures),
    }
    payload["live_data_plane"] = live_data_plane
    payload["freshness_status"] = "healthy" if bool(evidence.get("passed")) and not channel_failures else "unhealthy"
    manifest = _live_view_manifest_from_payload(payload)
    write_live_view_manifest(path, manifest)
    return manifest


def live_view_manifest_path(root: str | Path, dataset_id: str, live_view_id: str) -> Path:
    return Path(root) / "live-views" / dataset_id.replace(".", "/") / live_view_id / "manifest.json"


def load_live_view_manifest(path: str | Path) -> LiveViewManifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return _live_view_manifest_from_payload(payload)


def write_live_view_manifest(path: str | Path, manifest: LiveViewManifest) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest.to_primitive(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return target


def find_live_view_manifest(
    root: str | Path,
    *,
    dataset_id: str,
    contract_hash: str,
    policy: LiveViewFreshnessPolicy | None = None,
) -> LiveViewManifest | None:
    if not dataset_id or not contract_hash:
        return None
    directory = Path(root) / "live-views" / dataset_id.replace(".", "/")
    candidates = []
    for path in sorted(directory.glob("*/manifest.json")):
        manifest = load_live_view_manifest(path)
        if manifest.dataset_id == dataset_id and manifest.contract_hash == contract_hash:
            candidates.append(manifest)
    if policy is not None:
        passing = [item for item in candidates if evaluate_live_view_freshness(item, policy=policy).passed]
        if passing:
            return passing[-1]
    return candidates[-1] if candidates else None


def resolve_live_view_subscription(
    root: str | Path,
    *,
    name: str,
    dataset_id: str,
    contract_hash: str,
    policy: LiveViewFreshnessPolicy | None = None,
) -> LiveViewSubscriptionBinding:
    profile = policy or PAPER_LIVE_FRESHNESS_POLICY
    manifest = find_live_view_manifest(root, dataset_id=dataset_id, contract_hash=contract_hash, policy=profile)
    if manifest is None:
        raise ValueError(f"paper/live run requires healthy Live View freshness for data input {name!r}")
    gate = evaluate_live_view_freshness(manifest, policy=profile)
    if not gate.passed:
        raise ValueError(gate.reason)
    plane = manifest.live_data_plane
    return LiveViewSubscriptionBinding(
        name,
        manifest.dataset_id,
        manifest.live_view_id,
        manifest.artifact_ref,
        str(plane.get("event_source_contract") or "EventSource[DataSetRecord]"),
        str(plane.get("channel_contract") or "BoundedEventChannel"),
        str(plane.get("transport") or "connector"),
        gate,
    )


def freshness_gate_to_primitive(gate: LiveViewFreshnessGateResult) -> dict[str, object]:
    return {
        "live_view_id": gate.live_view_id,
        "policy": gate.policy_name,
        "freshness_status": gate.freshness_status,
        "max_age_seconds": gate.max_age_seconds,
        "channel_diagnostics": dict(gate.channel_diagnostics),
        "channel_failures": list(gate.channel_failures),
        "passed": gate.passed,
        "reason": gate.reason,
    }


def evaluate_live_view_freshness(
    manifest: LiveViewManifest,
    *,
    policy: LiveViewFreshnessPolicy | None = None,
) -> LiveViewFreshnessGateResult:
    profile = policy or LIVE_VIEW_CONFIGURED_FRESHNESS_POLICY
    max_age_seconds = _max_age_seconds(manifest.live_data_plane.get("freshness"))
    channel_diagnostics = _channel_diagnostics(manifest.live_data_plane.get("channel_diagnostics"))
    channel_failures = _channel_failures(channel_diagnostics, require=profile.require_channel_diagnostics)
    status = manifest.freshness_status.strip()
    if profile.require_max_age and max_age_seconds is None:
        return LiveViewFreshnessGateResult(
            manifest.live_view_id, profile.name, status, max_age_seconds,
            channel_diagnostics, channel_failures, False,
            f"Live View {manifest.live_view_id} requires freshness.max_age_seconds",
        )
    if status not in profile.passing_statuses:
        return LiveViewFreshnessGateResult(
            manifest.live_view_id, profile.name, status, max_age_seconds,
            channel_diagnostics, channel_failures, False,
            f"Live View {manifest.live_view_id} freshness status {status!r} does not satisfy {profile.name}",
        )
    if channel_failures:
        return LiveViewFreshnessGateResult(
            manifest.live_view_id, profile.name, status, max_age_seconds,
            channel_diagnostics, channel_failures, False,
            f"Live View {manifest.live_view_id} channel diagnostics failed: {', '.join(channel_failures)}",
        )
    return LiveViewFreshnessGateResult(
        manifest.live_view_id, profile.name, status, max_age_seconds,
        channel_diagnostics, channel_failures, True,
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


def _channel_diagnostics(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return live_view_channel_diagnostics(value)


def _channel_failures(diagnostics: Mapping[str, object], *, require: bool) -> tuple[str, ...]:
    if not diagnostics:
        return ("missing_channel_diagnostics",) if require else ()
    failures = []
    if _nonzero(diagnostics.get("dropped")):
        failures.append("channel_dropped")
    if _nonzero(diagnostics.get("overflow")):
        failures.append("channel_overflow")
    if _nonzero(diagnostics.get("sequence_gaps")):
        failures.append("sequence_gap")
    return tuple(failures)


def _nonzero(value: object) -> bool:
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return False


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _live_view_manifest_from_payload(payload: Mapping[str, object]) -> LiveViewManifest:
    return LiveViewManifest(
        str(payload["dataset_id"]),
        str(payload["live_view_id"]),
        str(payload["contract_hash"]),
        str(payload["connector_hash"]),
        str(payload["primary_time"]),
        tuple(str(item) for item in payload.get("fields", ())),
        payload.get("live_data_plane", {}),
        payload.get("source", {}),
        str(payload.get("freshness_status", "")),
        str(payload.get("published_at", "")),
        int(payload.get("schema_version", 1)),
    )
