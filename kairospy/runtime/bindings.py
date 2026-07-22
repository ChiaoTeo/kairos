from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from kairospy.execution.command import OrderCommand
from kairospy.execution.ports import ComboOrderRequest, OrderAck, OrderRequest

from .kernel import PreparedRun, RecoveryResult, SubmitResult


@dataclass(frozen=True, slots=True)
class EventSourceRunEventProvider:
    source: object
    binding_id: str
    max_events: int | None = None

    def __post_init__(self) -> None:
        if not self.binding_id.strip():
            raise ValueError("event source run provider requires binding_id")
        if not hasattr(self.source, "events") or not callable(self.source.events):
            raise ValueError("event source run provider requires source.events()")
        if self.max_events is not None and self.max_events < 1:
            raise ValueError("event source max_events must be positive")

    def __call__(self, prepared: PreparedRun) -> Iterable[object]:
        if _has_running_loop():
            raise RuntimeError(
                "event source run provider cannot collect async events inside a running event loop; "
                "bind long-lived streams through runtime service plans"
            )
        return asyncio.run(self._collect())

    async def _collect(self) -> tuple[object, ...]:
        values: list[object] = []
        async for event in self.source.events():
            values.append(event)
            if self.max_events is not None and len(values) >= self.max_events:
                break
        return tuple(values)


@dataclass(frozen=True, slots=True)
class ExecutionPortCommandSubmitter:
    gateway: object
    binding_id: str

    def __post_init__(self) -> None:
        if not self.binding_id.strip():
            raise ValueError("execution port command submitter requires binding_id")
        if not hasattr(self.gateway, "place_order") or not callable(self.gateway.place_order):
            raise ValueError("execution port command submitter requires gateway.place_order(request)")

    def __call__(self, commands: Iterable[object]) -> SubmitResult:
        accepted: list[str] = []
        rejected: list[str] = []
        acknowledgements: list[dict[str, object]] = []
        errors: list[tuple[str, str]] = []
        for command in commands:
            command_id = _command_id(command)
            request = _command_request(command)
            try:
                acknowledgement = _submit_request(self.gateway, request)
            except Exception as exc:
                rejected.append(command_id)
                errors.append((command_id, type(exc).__name__))
            else:
                accepted.append(command_id)
                acknowledgements.append(_ack_evidence(command_id, acknowledgement))
        return SubmitResult(
            tuple(accepted),
            tuple(rejected),
            {
                "binding_id": self.binding_id,
                "gateway": _binding_name(self.gateway),
                "acknowledgements": tuple(acknowledgements),
                "errors": tuple(errors),
            },
        )


@dataclass(frozen=True, slots=True)
class DurableOutboxCommandSubmitter:
    command_service: object
    dispatcher: object
    binding_id: str
    dispatch_immediately: bool = True

    def __post_init__(self) -> None:
        if not self.binding_id.strip():
            raise ValueError("durable outbox command submitter requires binding_id")
        if not hasattr(self.command_service, "submit") or not callable(self.command_service.submit):
            raise ValueError("durable outbox command submitter requires command_service.submit(request)")
        if self.dispatch_immediately and (
            not hasattr(self.dispatcher, "dispatch_once") or not callable(self.dispatcher.dispatch_once)
        ):
            raise ValueError("durable outbox command submitter requires dispatcher.dispatch_once()")

    def __call__(self, commands: Iterable[object]) -> SubmitResult:
        accepted: list[str] = []
        rejected: list[str] = []
        enqueue_errors: list[tuple[str, str]] = []
        dispatch_errors: list[tuple[str, str]] = []
        dispatch_count = 0
        for command in commands:
            command_id = _command_id(command)
            try:
                record = self.command_service.submit(_command_request(command))
            except Exception as exc:
                rejected.append(command_id)
                enqueue_errors.append((command_id, type(exc).__name__))
                continue
            outbox_command_id = str(getattr(record.command, "command_id", command_id))
            accepted.append(outbox_command_id)
            if self.dispatch_immediately:
                try:
                    if _dispatch_once(self.dispatcher):
                        dispatch_count += 1
                except Exception as exc:
                    dispatch_errors.append((outbox_command_id, type(exc).__name__))
        return SubmitResult(
            tuple(accepted),
            tuple(rejected),
            {
                "binding_id": self.binding_id,
                "gateway": _binding_name(getattr(self.dispatcher, "router", self.dispatcher)),
                "outbox": tuple(_outbox_record(record) for record in _outbox_records(self.command_service)),
                "dispatch_count": dispatch_count,
                "enqueue_errors": tuple(enqueue_errors),
                "dispatch_errors": tuple(dispatch_errors),
            },
        )


@dataclass(frozen=True, slots=True)
class ExecutionRecoveryBinding:
    recovery: object
    binding_id: str

    def __post_init__(self) -> None:
        if not self.binding_id.strip():
            raise ValueError("execution recovery binding requires binding_id")
        if not hasattr(self.recovery, "recover") or not callable(self.recovery.recover):
            raise ValueError("execution recovery binding requires recovery.recover(at)")

    def __call__(self, prepared: PreparedRun) -> RecoveryResult:
        result = self.recovery.recover(prepared.request.requested_at)
        complete = bool(getattr(result, "complete", False))
        resolved = tuple(str(item) for item in getattr(result, "resolved", ()))
        unresolved = tuple(str(item) for item in getattr(result, "unresolved", ()))
        return RecoveryResult(
            True,
            complete,
            {
                "binding_id": self.binding_id,
                "recovery": _binding_name(self.recovery),
                "resolved_command_ids": resolved,
                "unresolved_command_ids": unresolved,
            },
        )


@dataclass(frozen=True, slots=True)
class CompositeRecoveryBinding:
    handlers: tuple[Callable[[PreparedRun], RecoveryResult], ...]
    binding_id: str

    def __post_init__(self) -> None:
        if not self.binding_id.strip():
            raise ValueError("composite recovery binding requires binding_id")
        if not self.handlers:
            raise ValueError("composite recovery binding requires at least one handler")
        if any(not callable(handler) for handler in self.handlers):
            raise ValueError("composite recovery binding handlers must be callable")

    def __call__(self, prepared: PreparedRun) -> RecoveryResult:
        required = False
        recovered = True
        evidence = []
        for handler in self.handlers:
            result = handler(prepared)
            required = required or result.required
            recovered = recovered and (not result.required or result.recovered)
            evidence.append({
                "handler": _binding_name(handler),
                "required": result.required,
                "recovered": result.recovered,
                "recovery_hash": result.recovery_hash,
                "evidence": dict(result.evidence),
            })
        return RecoveryResult(
            required,
            recovered,
            {
                "binding_id": self.binding_id,
                "handlers": tuple(evidence),
            },
        )


@dataclass(frozen=True, slots=True)
class ManagedServiceEvidenceProvider:
    supervisor: object
    binding_id: str

    def __post_init__(self) -> None:
        if not self.binding_id.strip():
            raise ValueError("managed service evidence provider requires binding_id")
        if not hasattr(self.supervisor, "snapshots") or not callable(self.supervisor.snapshots):
            raise ValueError("managed service evidence provider requires supervisor.snapshots()")

    def __call__(self) -> dict[str, object]:
        snapshots = tuple(self.supervisor.snapshots())
        return {
            "binding_id": self.binding_id,
            "healthy": bool(getattr(self.supervisor, "healthy", False)),
            "services": tuple(_service_snapshot(snapshot) for snapshot in snapshots),
        }


def _has_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _dispatch_once(dispatcher: object) -> bool:
    if _has_running_loop():
        raise RuntimeError(
            "durable outbox command submitter cannot dispatch inside a running event loop; "
            "run DurableOrderDispatcher as a managed runtime service"
        )
    return bool(asyncio.run(dispatcher.dispatch_once()))


def _command_request(command: object) -> OrderRequest | ComboOrderRequest:
    request = command.request if isinstance(command, OrderCommand) else command
    if isinstance(request, OrderRequest | ComboOrderRequest):
        return request
    raise TypeError("execution command submitter only accepts OrderCommand, OrderRequest or ComboOrderRequest")


def _submit_request(gateway: object, request: OrderRequest | ComboOrderRequest) -> OrderAck:
    if isinstance(request, ComboOrderRequest):
        if not hasattr(gateway, "place_combo_order") or not callable(gateway.place_combo_order):
            raise ValueError("execution gateway does not support combo orders")
        return gateway.place_combo_order(request)
    return gateway.place_order(request)


def _ack_evidence(command_id: str, acknowledgement: OrderAck) -> dict[str, object]:
    return {
        "command_id": command_id,
        "client_order_id": acknowledgement.client_order_id,
        "venue_order_id": acknowledgement.venue_order_id,
        "accepted_at": _timestamp(acknowledgement.accepted_at),
    }


def _command_id(command: object) -> str:
    if isinstance(command, OrderCommand):
        return command.command_id
    return str(getattr(command, "client_order_id", command))


def _binding_name(value: object) -> str:
    return str(getattr(value, "service_id", getattr(value, "binding_id", type(value).__name__)))


def _timestamp(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _outbox_records(command_service: object) -> tuple[object, ...]:
    store = getattr(command_service, "store", None)
    if store is None or not hasattr(store, "outbox_commands"):
        return ()
    return tuple(store.outbox_commands())


def _outbox_record(record: object) -> dict[str, object]:
    command = getattr(record, "command", None)
    return {
        "command_id": str(getattr(command, "command_id", "")),
        "client_order_id": str(getattr(getattr(command, "request", None), "client_order_id", "")),
        "status": _value(getattr(record, "status", "")),
        "attempts": int(getattr(record, "attempts", 0)),
        "last_error": getattr(record, "last_error", None),
    }


def _service_snapshot(snapshot: object) -> dict[str, object]:
    return {
        "name": str(getattr(snapshot, "name", "")),
        "criticality": _value(getattr(snapshot, "criticality", "")),
        "status": _value(getattr(snapshot, "status", "")),
        "attempts": int(getattr(snapshot, "attempts", 0)),
        "restart_count": int(getattr(snapshot, "restart_count", 0)),
        "last_fault": _service_fault(getattr(snapshot, "last_fault", None)),
    }


def _service_fault(fault: object | None) -> dict[str, object] | None:
    if fault is None:
        return None
    return {
        "task_name": str(getattr(fault, "task_name", "")),
        "criticality": _value(getattr(fault, "criticality", "")),
        "error_type": str(getattr(fault, "error_type", "")),
        "message": str(getattr(fault, "message", "")),
        "attempt": int(getattr(fault, "attempt", 0)),
        "occurred_at": _timestamp(getattr(fault, "occurred_at", "")),
    }


def _value(value: object) -> object:
    return getattr(value, "value", value)
