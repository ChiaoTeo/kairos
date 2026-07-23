from __future__ import annotations

import asyncio

from kairospy.governance.reconciliation import unknown_external_open_order_ids
from kairospy.runtime.application import RuntimeStatus
from kairospy.runtime.clock import Clock, SystemClock
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore


class RiskRuntimeMonitorService:
    """Managed service that projects runtime risk gates into durable status."""

    STATE_KEY_PREFIX = "risk_monitor"

    def __init__(
        self,
        application: object,
        store: SQLiteRuntimeStore,
        *,
        run_id: str,
        interval_seconds: float = 5.0,
        clock: Clock | None = None,
    ) -> None:
        if not str(run_id).strip():
            raise ValueError("risk runtime monitor requires run_id")
        if interval_seconds <= 0:
            raise ValueError("risk runtime monitor interval must be positive")
        self.application = application
        self.store = store
        self.run_id = str(run_id)
        self.interval_seconds = interval_seconds
        self.clock = clock or SystemClock()

    @property
    def state_key(self) -> str:
        return f"{self.STATE_KEY_PREFIX}:{self.run_id}:last"

    def managed_service(self, name: str | None = None):
        from kairospy.runtime.service_supervisor import ManagedServiceSpec

        return ManagedServiceSpec(name or f"risk-monitor:{self.run_id}", self.run)

    def check_once(self) -> dict[str, object]:
        at = self.clock.now()
        reasons: list[str] = []
        heartbeat_error = None
        status = getattr(self.application, "status", None)
        if status in {
            RuntimeStatus.READY,
            RuntimeStatus.RUNNING,
            RuntimeStatus.DEGRADED,
            RuntimeStatus.REDUCE_ONLY,
        }:
            try:
                self.application.heartbeat()
            except Exception as error:
                heartbeat_error = error
                reasons.append(f"account_lock_heartbeat_failed:{type(error).__name__}:{error}")
        kill_switch = self.store.runtime_state("kill_switch")
        if isinstance(kill_switch, dict) and bool(kill_switch.get("triggered")):
            reasons.append("kill_switch")
        reconciliation = self.store.runtime_state("reconciliation:last")
        external_open_orders = ()
        if isinstance(reconciliation, dict) and reconciliation.get("matched") is False:
            reasons.append("reconciliation_mismatch")
            external_open_orders = unknown_external_open_order_ids(reconciliation)
            if external_open_orders:
                reasons.append("unknown_external_open_orders")
        unresolved = tuple(self.store.unresolved_orders())
        if unresolved:
            reasons.append("unresolved_orders")
        requiring_recovery = tuple(self.store.orders_requiring_venue_recovery())

        phase = "ok" if not reasons else "blocking"
        state = {
            "run_id": self.run_id,
            "phase": phase,
            "status": "ok" if not reasons else "blocking",
            "reasons": tuple(dict.fromkeys(reasons)),
            "unresolved_order_count": len(unresolved),
            "orders_requiring_recovery_count": len(requiring_recovery),
            "unknown_external_open_order_count": len(external_open_orders),
            "unknown_external_open_order_ids": external_open_orders,
            "updated_at": at.isoformat(),
        }
        self.store.set_runtime_state(self.state_key, state, at)
        current_risk = self.store.runtime_state("risk_runtime:last")
        if reasons:
            self.store.set_runtime_state("risk_runtime:last", state, at)
            if status in {RuntimeStatus.READY, RuntimeStatus.RUNNING, RuntimeStatus.DEGRADED}:
                self.application.degrade("risk monitor blocking: " + ",".join(state["reasons"]), reduce_only=True)
        elif not (isinstance(current_risk, dict) and str(current_risk.get("status")) in {"paused", "blocking"}):
            self.store.set_runtime_state("risk_runtime:last", state, at)
        if heartbeat_error is not None:
            raise heartbeat_error
        return state

    async def run(self) -> None:
        self._persist_phase("running", {"status": "ok", "reasons": (), "reason": "started"})
        try:
            while True:
                self.check_once()
                await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            self._persist_phase("stopped", {"status": "ok", "reasons": (), "reason": "service stopped"})
            raise
        except Exception as error:
            self._persist_phase("failed", {
                "status": "blocking",
                "reasons": (f"{type(error).__name__}:{error}",),
                "error_type": type(error).__name__,
                "message": str(error),
            })
            raise

    def _persist_phase(self, phase: str, evidence: dict[str, object]) -> None:
        at = self.clock.now()
        self.store.set_runtime_state(self.state_key, {
            "run_id": self.run_id,
            "phase": phase,
            "updated_at": at.isoformat(),
            **evidence,
        }, at)


__all__ = ["RiskRuntimeMonitorService"]
