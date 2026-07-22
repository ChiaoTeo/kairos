from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
from typing import Awaitable, Callable, Mapping

from .application import KairosApplication, ProbeResult, RuntimeStatus
from .bindings import ManagedServiceEvidenceProvider
from .kernel import RunArtifactWriter, RunKernel, RunRequest, RunResult, RunStatus, StrategyRunResult
from .service_supervisor import AsyncServiceSupervisor, ManagedServiceSpec
from kairospy.infrastructure.storage.codec import to_primitive


RunArtifactWriterFactory = Callable[[Mapping[str, object]], RunArtifactWriter]


@dataclass(frozen=True, slots=True)
class RuntimeLaunchResult:
    run_result: RunResult
    evidence: Mapping[str, object]

    @property
    def artifact_hash(self) -> str | None:
        return self.run_result.artifact_hash

    @property
    def artifact_refs(self) -> tuple[str, ...]:
        return self.run_result.artifact_refs


class RuntimeRunLauncher:
    """Bind application startup gates to RunKernel without owning strategy or governance."""

    def __init__(
        self,
        application: KairosApplication,
        kernel: RunKernel,
        *,
        service_evidence_provider: Callable[[], Mapping[str, object]] | None = None,
        managed_services: tuple[ManagedServiceSpec, ...] = (),
        service_evidence_binding_id: str = "runtime-services",
        service_stop_timeout_seconds: float = 5.0,
        shutdown_handler: Callable[..., object] | None = None,
    ) -> None:
        if service_evidence_provider is not None and managed_services:
            raise ValueError("runtime launcher accepts either service_evidence_provider or managed_services")
        if service_stop_timeout_seconds <= 0:
            raise ValueError("runtime launcher service stop timeout must be positive")
        if not service_evidence_binding_id.strip():
            raise ValueError("runtime launcher service evidence binding_id cannot be empty")
        self.application = application
        self.kernel = kernel
        self.service_evidence_provider = service_evidence_provider
        self.managed_services = tuple(managed_services)
        self.service_evidence_binding_id = service_evidence_binding_id
        self.service_stop_timeout_seconds = service_stop_timeout_seconds
        self.shutdown_handler = shutdown_handler

    def run(
        self,
        request: RunRequest,
        strategy_runner: Callable[[object], StrategyRunResult],
        *,
        artifact_writer: RunArtifactWriter | None = None,
        artifact_writer_factory: RunArtifactWriterFactory | None = None,
    ) -> RuntimeLaunchResult:
        if artifact_writer is not None and artifact_writer_factory is not None:
            raise ValueError("runtime launcher accepts either artifact_writer or artifact_writer_factory")
        service_supervisor = AsyncServiceSupervisor() if self.managed_services else None
        service_evidence_provider = self.service_evidence_provider
        if service_supervisor is not None:
            service_evidence_provider = ManagedServiceEvidenceProvider(
                service_supervisor,
                self.service_evidence_binding_id,
            )
        stopped_services = False
        shutdown_attempted = False
        evidence: dict[str, object] | None = None

        def refresh_service_evidence() -> None:
            if evidence is None or service_evidence_provider is None:
                return
            evidence["services"] = dict(service_evidence_provider())

        def stop_services() -> None:
            nonlocal stopped_services
            if service_supervisor is None or stopped_services:
                return
            self._run_async(lambda: service_supervisor.stop(timeout_seconds=self.service_stop_timeout_seconds))
            stopped_services = True
            refresh_service_evidence()

        def run_shutdown_handler(reason: str) -> None:
            nonlocal shutdown_attempted
            if self.shutdown_handler is None or shutdown_attempted:
                return
            shutdown_attempted = True
            report = self._run_shutdown_handler(reason)
            if evidence is not None and report is not None:
                try:
                    evidence["stop_report"] = to_primitive(report)
                except TypeError:
                    evidence["stop_report"] = str(report)

        self._ensure_application_running()
        if service_supervisor is not None:
            self._run_async(lambda: service_supervisor.start(self.managed_services))
        evidence = self._evidence(service_evidence_provider)
        writer = artifact_writer_factory(evidence) if artifact_writer_factory is not None else artifact_writer
        if writer is not None and (service_supervisor is not None or self.shutdown_handler is not None):
            base_writer = writer

            def finalizing_writer(prepared, strategy_result, profile_result):
                run_shutdown_handler("crash" if profile_result.status is RunStatus.FAILED else "scheduled")
                stop_services()
                return base_writer(prepared, strategy_result, profile_result)

            writer = finalizing_writer

        try:
            result = self.kernel.run(request, strategy_runner, artifact_writer=writer)
            run_shutdown_handler("crash" if result.status is RunStatus.FAILED else "scheduled")
            stop_services()
            return RuntimeLaunchResult(result, evidence)
        except Exception:
            run_shutdown_handler("crash")
            raise
        finally:
            stop_services()

    def evidence(self) -> dict[str, object]:
        return self._evidence(self.service_evidence_provider)

    def _evidence(
        self,
        service_evidence_provider: Callable[[], Mapping[str, object]] | None,
    ) -> dict[str, object]:
        evidence: dict[str, object] = {
            "runtime_id": self.application.runtime_id,
            "status": self.application.status.value,
            "environment": self.application.config.environment.value,
            "accounts": tuple(account.value for account in self.application.accounts),
            "probes": tuple(_probe_result(item) for item in self.application.probe_results),
            "recovery": _runtime_recovery_evidence(self.application.recovery_result),
            "order_recovery": _order_recovery_evidence(self.application.order_recovery_report),
        }
        if service_evidence_provider is not None:
            evidence["services"] = dict(service_evidence_provider())
        return evidence

    def _ensure_application_running(self) -> None:
        if self.application.status in {RuntimeStatus.CREATED, RuntimeStatus.STOPPED}:
            self.application.start()
        if self.application.status is RuntimeStatus.READY:
            self.application.run()
        self.application.require_operational()

    def _run_shutdown_handler(self, reason: str) -> object | None:
        if self.shutdown_handler is None:
            return None
        try:
            signature = inspect.signature(self.shutdown_handler)
        except (TypeError, ValueError):
            result = self.shutdown_handler(reason)
        else:
            positional = tuple(
                parameter
                for parameter in signature.parameters.values()
                if parameter.kind in {
                    parameter.POSITIONAL_ONLY,
                    parameter.POSITIONAL_OR_KEYWORD,
                    parameter.VAR_POSITIONAL,
                }
            )
            result = self.shutdown_handler(reason) if positional else self.shutdown_handler()
        if inspect.isawaitable(result):
            return self._run_async(lambda: result)
        return result

    @staticmethod
    def _run_async(operation: Callable[[], Awaitable[object]]) -> object:
        if _has_running_loop():
            raise RuntimeError("runtime launcher cannot manage async services inside a running event loop")
        return asyncio.run(operation())


def _probe_result(value: ProbeResult) -> dict[str, object]:
    return {
        "name": value.name,
        "ready": value.ready,
        "reason": value.reason,
        "checked_at": value.checked_at.isoformat(),
    }


def _runtime_recovery_evidence(value: object | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "ready": bool(getattr(value, "ready", False)),
        "reason": str(getattr(value, "reason", "")),
        "recovered_at": _timestamp(getattr(value, "recovered_at", "")),
    }


def _order_recovery_evidence(value: object | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "complete": bool(getattr(value, "complete", False)),
        "resolved": tuple(str(item) for item in getattr(value, "resolved", ())),
        "unresolved": tuple(str(item) for item in getattr(value, "unresolved", ())),
    }


def _timestamp(value: object) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _has_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True
