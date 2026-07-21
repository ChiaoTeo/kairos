from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Callable, Protocol

from kairospy.trading.identity import AccountKey
from kairospy.orchestration.runtime_store import SQLiteRuntimeStore

from .clock import Clock, SystemClock
from .config import ApplicationConfig
from .recovery import RuntimeRecovery, RuntimeRecoveryResult

if TYPE_CHECKING:
    from kairospy.execution.recovery import OrderRecoveryReport, VenueOrderRecoveryService


class RuntimeStatus(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RECOVERING = "recovering"
    RECONCILING = "reconciling"
    READY = "ready"
    RUNNING = "running"
    DEGRADED = "degraded"
    REDUCE_ONLY = "reduce_only"
    UNKNOWN_EXTERNAL_STATE = "unknown_external_state"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED_START = "failed_start"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    name: str
    ready: bool
    reason: str
    checked_at: datetime


class ReadinessProbe(Protocol):
    name: str

    def check(self, at: datetime) -> ProbeResult: ...


class FunctionProbe:
    def __init__(self, name: str, check: Callable[[], tuple[bool, str]]) -> None:
        if not name.strip():
            raise ValueError("probe name cannot be empty")
        self.name = name
        self._check = check

    def check(self, at: datetime) -> ProbeResult:
        ready, reason = self._check()
        return ProbeResult(self.name, bool(ready), str(reason), at)


class PersistenceProbe:
    name = "persistence"

    def __init__(self, store: SQLiteRuntimeStore) -> None:
        self.store = store

    def check(self, at: datetime) -> ProbeResult:
        marker = at.isoformat()
        self.store.set_runtime_state("persistence_probe", {"at": marker}, at)
        value = self.store.runtime_state("persistence_probe")
        ready = value == {"at": marker}
        return ProbeResult(self.name, ready, "read/write transaction passed" if ready else "read/write mismatch", at)


class KairosApplication:
    """Lifecycle and recovery gate for a single-account-set execution runtime."""

    STATE_KEY = "kairospy_application"

    def __init__(self, config: ApplicationConfig, store: SQLiteRuntimeStore, *, runtime_id: str,
                 accounts: tuple[AccountKey, ...] = (), probes: tuple[ReadinessProbe, ...] = (),
                 recovery: RuntimeRecovery | None = None,
                 order_recovery: VenueOrderRecoveryService | None = None,
                 clock: Clock | None = None) -> None:
        if not runtime_id.strip():
            raise ValueError("runtime id cannot be empty")
        if len(set(accounts)) != len(accounts):
            raise ValueError("runtime accounts must be unique")
        names = [probe.name for probe in probes]
        if len(set(names)) != len(names):
            raise ValueError("readiness probe names must be unique")
        if accounts and recovery is None:
            raise ValueError("account runtimes require durable recovery and reconciliation")
        self.config = config
        self.store = store
        self.runtime_id = runtime_id
        self.accounts = accounts
        self.probes = (PersistenceProbe(store), *probes)
        self.recovery = recovery
        self.order_recovery = order_recovery
        self.clock = clock or SystemClock()
        self.status = RuntimeStatus.CREATED
        self.probe_results: tuple[ProbeResult, ...] = ()
        self.recovery_result: RuntimeRecoveryResult | None = None
        self.order_recovery_report: OrderRecoveryReport | None = None
        self._locked_accounts: list[AccountKey] = []

    def start(self) -> None:
        if self.status not in {RuntimeStatus.CREATED, RuntimeStatus.STOPPED}:
            raise RuntimeError(f"runtime cannot start from {self.status.value}")
        self.config.validate()
        self._set_status(RuntimeStatus.STARTING)
        try:
            for account in self.accounts:
                self.store.acquire_account_lock(
                    account, self.runtime_id, self.clock.now(),
                    lease_seconds=self.config.account_lock_lease_seconds,
                )
                self._locked_accounts.append(account)
            self._set_status(RuntimeStatus.RECOVERING)
            unresolved = self.store.unresolved_orders()
            unresolved_ids = tuple(item.request.client_order_id for item in unresolved)
            if self.order_recovery is not None:
                self.order_recovery_report = self.order_recovery.recover(self.clock.now())
                unresolved = self.store.unresolved_orders()
                unresolved_ids = tuple(dict.fromkeys((
                    *(item.request.client_order_id for item in unresolved),
                    *self.order_recovery_report.unresolved,
                )))
            if unresolved_ids:
                self._set_status(RuntimeStatus.UNKNOWN_EXTERNAL_STATE, reason=(
                    "unresolved orders: " + ",".join(unresolved_ids)
                ))
                raise RuntimeError("runtime recovery requires venue resolution for unresolved orders")
            if self.recovery is not None:
                self.recovery_result = self.recovery.recover(self.clock.now())
                if not self.recovery_result.ready:
                    self._set_status(RuntimeStatus.UNKNOWN_EXTERNAL_STATE, reason=self.recovery_result.reason)
                    raise RuntimeError("runtime recovery failed: " + self.recovery_result.reason)
            self._set_status(RuntimeStatus.RECONCILING)
            at = self.clock.now()
            self.probe_results = tuple(probe.check(at) for probe in self.probes)
            failures = tuple(item for item in self.probe_results if not item.ready)
            if failures:
                self._set_status(RuntimeStatus.FAILED_START, reason="; ".join(
                    f"{item.name}:{item.reason}" for item in failures
                ))
                raise RuntimeError("runtime readiness failed: " + ", ".join(item.name for item in failures))
            self._set_status(RuntimeStatus.READY)
        except Exception:
            if self.status not in {RuntimeStatus.UNKNOWN_EXTERNAL_STATE, RuntimeStatus.FAILED_START}:
                self._set_status(RuntimeStatus.FAILED_START)
            raise

    def run(self) -> None:
        if self.status is not RuntimeStatus.READY:
            raise RuntimeError("runtime must be ready before running")
        self._set_status(RuntimeStatus.RUNNING)

    def require_operational(self) -> None:
        """Fail closed unless the durable runtime has completed its startup gates."""
        if self.status not in {
            RuntimeStatus.READY, RuntimeStatus.RUNNING, RuntimeStatus.DEGRADED, RuntimeStatus.REDUCE_ONLY,
        }:
            raise RuntimeError(f"runtime is not operational: {self.status.value}")

    def heartbeat(self) -> None:
        if self.status not in {
            RuntimeStatus.READY, RuntimeStatus.RUNNING, RuntimeStatus.DEGRADED, RuntimeStatus.REDUCE_ONLY,
        }:
            raise RuntimeError(f"runtime cannot heartbeat from {self.status.value}")
        at = self.clock.now()
        for account in self._locked_accounts:
            self.store.heartbeat_account_lock(
                account, self.runtime_id, at, lease_seconds=self.config.account_lock_lease_seconds,
            )

    def degrade(self, reason: str, *, reduce_only: bool = True) -> None:
        if self.status not in {RuntimeStatus.READY, RuntimeStatus.RUNNING, RuntimeStatus.DEGRADED, RuntimeStatus.REDUCE_ONLY}:
            raise RuntimeError(f"runtime cannot degrade from {self.status.value}")
        if not reason.strip():
            raise ValueError("runtime degradation requires a reason")
        self._set_status(RuntimeStatus.REDUCE_ONLY if reduce_only else RuntimeStatus.DEGRADED, reason=reason)

    def stop(self) -> None:
        if self.status is RuntimeStatus.STOPPED:
            return
        self._set_status(RuntimeStatus.STOPPING)
        errors = []
        for account in reversed(self._locked_accounts):
            try:
                self.store.release_account_lock(account, self.runtime_id)
            except Exception as error:
                errors.append(str(error))
        self._locked_accounts.clear()
        self._set_status(RuntimeStatus.STOPPED, reason="; ".join(errors) if errors else None)
        if errors:
            raise RuntimeError("runtime stopped with account-lock errors: " + "; ".join(errors))

    def _set_status(self, status: RuntimeStatus, *, reason: str | None = None) -> None:
        self.status = status
        at = self.clock.now()
        self.store.set_runtime_state(self.STATE_KEY, {
            "runtime_id": self.runtime_id,
            "status": status.value,
            "reason": reason,
            "updated_at": at.isoformat(),
            "accounts": [account.value for account in self.accounts],
        }, at)
