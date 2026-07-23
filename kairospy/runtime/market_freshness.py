from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from kairospy.data.quality.freshness import (
    LIVE_VIEW_CONFIGURED_FRESHNESS_POLICY,
    LiveViewFreshnessPolicy,
    evaluate_live_view_freshness,
    freshness_gate_to_primitive,
    load_live_view_manifest,
)
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore


class _Clock(Protocol):
    def now(self) -> datetime: ...


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class MarketFreshnessRuntimeMonitorService:
    """Mirror Live View freshness evidence into runtime state for operator metrics."""

    STATE_KEY_PREFIX = "market_freshness"

    def __init__(
        self,
        store: SQLiteRuntimeStore,
        *,
        run_id: str,
        name: str,
        dataset: str,
        live_view_id: str,
        manifest_path: str | Path,
        interval_seconds: float = 5.0,
        policy: LiveViewFreshnessPolicy | None = None,
        clock: _Clock | None = None,
    ) -> None:
        if not str(run_id).strip():
            raise ValueError("market freshness monitor requires run_id")
        if not str(name).strip() or not str(dataset).strip() or not str(live_view_id).strip():
            raise ValueError("market freshness monitor requires name, dataset, and live_view_id")
        if interval_seconds <= 0:
            raise ValueError("market freshness monitor interval must be positive")
        self.store = store
        self.run_id = str(run_id)
        self.name = str(name)
        self.dataset = str(dataset)
        self.live_view_id = str(live_view_id)
        self.manifest_path = Path(manifest_path)
        self.interval_seconds = float(interval_seconds)
        self.policy = policy or LIVE_VIEW_CONFIGURED_FRESHNESS_POLICY
        self.clock = clock or _SystemClock()

    @property
    def state_key(self) -> str:
        return f"{self.STATE_KEY_PREFIX}:{self.run_id}:last"

    def managed_service(self, name: str | None = None):
        from kairospy.runtime.service_supervisor import ManagedServiceSpec

        return ManagedServiceSpec(name or f"market-freshness:{self.run_id}:{self.name}", self.run)

    async def run(self) -> None:
        self.poll_once()
        try:
            while True:
                await asyncio.sleep(self.interval_seconds)
                self.poll_once()
        except asyncio.CancelledError:
            self._persist("stopped", {"reason": "service stopped"})
            raise
        except Exception as error:
            self._persist("failed", {"error_type": type(error).__name__, "message": str(error)})
            raise

    def poll_once(self) -> dict[str, object]:
        manifest = load_live_view_manifest(self.manifest_path)
        gate = evaluate_live_view_freshness(manifest, policy=self.policy)
        mtime = datetime.fromtimestamp(self.manifest_path.stat().st_mtime, timezone.utc)
        observed_at = self.clock.now()
        freshness_evidence = manifest.live_data_plane.get("freshness_evidence")
        last_event_time = (
            _parse_datetime(freshness_evidence.get("last_available_time") or freshness_evidence.get("last_event_time"))
            if isinstance(freshness_evidence, dict) else None
        )
        payload = {
            "reason": gate.reason,
            "policy": gate.policy_name,
            "freshness_status": gate.freshness_status,
            "freshness_passed": gate.passed,
            "freshness_max_age_seconds": gate.max_age_seconds,
            "freshness_updated_age_seconds": max(0.0, (observed_at - mtime).total_seconds()),
            "market_event_time": last_event_time.isoformat() if last_event_time is not None else None,
            "market_event_age_seconds": (
                max(0.0, (observed_at - last_event_time).total_seconds())
                if last_event_time is not None else None
            ),
            "channel_failure_count": len(gate.channel_failures),
            "channel_failures": tuple(gate.channel_failures),
            "channel_diagnostics": dict(gate.channel_diagnostics),
            "manifest_path": str(self.manifest_path),
            "manifest_hash": manifest.manifest_hash,
            "freshness_gate": freshness_gate_to_primitive(gate),
        }
        self._persist("running", payload)
        return payload

    def _persist(self, phase: str, evidence: dict[str, object]) -> None:
        at = self.clock.now()
        self.store.set_runtime_state(
            self.state_key,
            {
                "run_id": self.run_id,
                "phase": phase,
                "name": self.name,
                "dataset": self.dataset,
                "live_view_id": self.live_view_id,
                "updated_at": at.isoformat(),
                **evidence,
            },
            at,
        )


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
