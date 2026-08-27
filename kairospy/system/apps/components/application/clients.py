"""Typed clients for already-running business processes.

These clients are part of the System boundary. They expose health and control
commands through JSON-RPC; business state is read from module-owned typed indexed
views.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from kairospy.infrastructure.unix_http import request_sync

if TYPE_CHECKING:
    from kairospy.system.apps.workspace.application import InstanceWorkspace
    from kairospy.primitives.account import AccountId
    from kairospy.infrastructure.contracts.account import (
        AccountControlClient,
    )
    from kairospy.infrastructure.contracts.capital.types import CapitalControlClient
    from kairospy.infrastructure.contracts.execution import (
        ExecutionControlClient,
    )
    from kairospy.infrastructure.contracts.execution.types import (
        AdvanceExecutionTimeResponse,
        ExecutionBacktestMarketResponse,
        ExecutionCommandStatus,
        ExecutionOrderAuditQuery,
        ExecutionOrderAuditResponse,
        ExecutionRoutesQuery,
        ExecutionRoutesResponse,
        ReplaceOrderRequest,
        SubmitIntentRequest,
    )
    from kairospy.infrastructure.contracts.market import MarketControlClient
    from kairospy.infrastructure.contracts.reference.client import ReferenceClient
    from kairospy.infrastructure.contracts.reference.control import (
        ReferenceControlClient,
    )


class _RiskCommandStatus(Protocol):
    status: str


class _RiskAdvanceResponse(Protocol):
    event_time_unix_nanos: int
    expired: int


class _RiskHealth(Protocol):
    status: str
    generation: int
    event_sequence: int
    policy_version: int
    reservation_count: int
    open_circuit_count: int


class _RiskControl(Protocol):
    def publish_policy(self, request: object) -> _RiskCommandStatus: ...
    def pre_trade_check(self, request: object) -> object: ...
    def authorize_and_reserve(self, request: object) -> object: ...
    def release_reservation(self, request: object) -> object: ...
    def consume_reservation(self, request: object) -> object: ...
    def resize_reservation(self, request: object) -> object: ...
    def open_circuit(self, request: object) -> object: ...
    def close_circuit(self, request: object) -> object: ...
    def advance_time(self, request: object) -> _RiskAdvanceResponse: ...
    def health(self) -> _RiskHealth: ...


@dataclass(frozen=True)
class SystemRpcClient:
    """Synchronous typed facade over the Unix JSON-RPC transport."""

    socket_path: Path
    view_root: Path | None = None
    database_path: Path | None = None
    actor_id: str | None = None
    workspace_id: str | None = None
    launch_id: str | None = None
    instance_id: str | None = None
    timeout: float = 3.0

    def __post_init__(self) -> None:
        if not isinstance(self.socket_path, Path):
            object.__setattr__(self, "socket_path", Path(self.socket_path))
        if self.view_root is not None and not isinstance(self.view_root, Path):
            object.__setattr__(self, "view_root", Path(self.view_root))
        if self.database_path is not None and not isinstance(self.database_path, Path):
            object.__setattr__(self, "database_path", Path(self.database_path))
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")

    def call(self, method: str, params: list[Any] | None = None) -> dict[str, Any]:
        if not method or "/" in method:
            raise ValueError("JSON-RPC method name must be non-empty and path-free")
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": [] if params is None else params,
        }
        status, value = request_sync(
            self.socket_path,
            "POST",
            "/",
            payload,
            timeout=self.timeout,
        )
        if status >= 400:
            raise RuntimeError(
                str(value.get("error", f"JSON-RPC request failed: HTTP {status}"))
            )
        if "error" in value:
            raise RuntimeError(str(value["error"]))
        return value.get("result", {})

    def status(self) -> dict[str, Any]:
        return self.call("system_health")

    def refresh(self) -> dict[str, Any]:
        return self.call("system_refresh")

    def stop(self) -> dict[str, Any]:
        return self.call("system_stop")

    def subscribe(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.call("system_subscribe", [body])

    def unsubscribe(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.call("system_unsubscribe", [body])

    def recover(self) -> dict[str, Any]:
        return self.call("system_recover")

    def command(self, component: str, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.call(f"{component}_command", [body])

    def require_view_root(self) -> Path:
        if self.view_root is None:
            raise RuntimeError(
                "component connection manifest is missing a view_root path"
            )
        return self.view_root


class AccountSystemClient(SystemRpcClient):
    control: AccountControlClient

    def __post_init__(self) -> None:
        super().__post_init__()
        from kairospy.infrastructure.contracts.account import AccountControlClient

        object.__setattr__(
            self,
            "control",
            AccountControlClient(self.socket_path, timeout=self.timeout),
        )

    def reconcile(self) -> dict[str, Any]:
        from kairospy.infrastructure.contracts.account import AccountSegmentsRequest

        return _account_refresh_response(
            self.control.reconcile(AccountSegmentsRequest())
        )

    def refresh(self) -> dict[str, Any]:
        from kairospy.infrastructure.contracts.account import AccountSegmentsRequest

        return _account_refresh_response(self.control.refresh(AccountSegmentsRequest()))

    def advance_time(self, event_time_unix_nanos: int) -> dict[str, Any]:
        from kairospy.infrastructure.contracts.account import AdvanceAccountTimeRequest

        result = self.control.advance_time(
            AdvanceAccountTimeRequest(event_time_unix_nanos)
        )
        return {"event_time_unix_nanos": result.event_time_unix_nanos}

    def mark_to_market_event(self, event: object) -> dict[str, Any] | None:
        from kairospy.investment.apps.account.application.mapping import (
            backtest_mark_to_market_request,
        )

        request = backtest_mark_to_market_request(event)
        if request is None:
            return None
        result = self.control.mark_to_market(request)
        segment_key = getattr(request, "segment_key")
        if not isinstance(segment_key, str):
            raise TypeError("Account mark-to-market request omitted segment_key")
        return {
            "result": {"status": result.status},
            "segment_key": segment_key,
        }

    def current_view(self, account_id: AccountId):
        from kairospy.infrastructure.contracts.account import AccountCurrentView

        return AccountCurrentView(
            self.require_view_root(),
            str(account_id),
            self._require_workspace_id(),
            self.launch_id,
            self.instance_id,
        )

    def observed_orders_view(self, account_id: AccountId):
        return self.current_view(account_id)

    def _require_workspace_id(self) -> str:
        if self.workspace_id is None:
            raise RuntimeError(
                "Account indexed current view requires workspace identity"
            )
        return self.workspace_id


class ExecutionSystemClient(SystemRpcClient):
    control: ExecutionControlClient

    def __post_init__(self) -> None:
        super().__post_init__()
        from kairospy.infrastructure.contracts.execution import ExecutionControlClient

        object.__setattr__(
            self,
            "control",
            ExecutionControlClient(self.socket_path, timeout=self.timeout),
        )

    def routes(self, query: "ExecutionRoutesQuery") -> "ExecutionRoutesResponse":
        return self.control.routes(query)

    def submit_intent(
        self, request: "SubmitIntentRequest"
    ) -> "ExecutionCommandStatus":
        return self.control.submit_intent(request)

    def cancel_intent(self, intent_id: str, *, reason: str = "") -> dict[str, Any]:
        raise NotImplementedError("cancel_intent is not part of ExecutionControlRpc")

    def expire_intent(self, intent_id: str, *, reason: str = "") -> dict[str, Any]:
        raise NotImplementedError("expire_intent is not part of ExecutionControlRpc")

    def submit(
        self, request: "SubmitIntentRequest", *, dry_run: bool = False
    ) -> "ExecutionCommandStatus":
        if dry_run:
            raise NotImplementedError(
                "dry-run submit is not part of ExecutionControlRpc"
            )
        return self.control.submit_intent(request)

    def cancel(self, order_id: str, reason: str = "system cancel") -> "ExecutionCommandStatus":
        from kairospy.infrastructure.contracts.execution.types import CancelOrderRequest

        return self.control.cancel_order(order_id, CancelOrderRequest(reason=reason))

    def replace(
        self, order_id: str, replacement: "ReplaceOrderRequest"
    ) -> "ExecutionCommandStatus":
        return self.control.replace_order(order_id, replacement)

    def advance_time(
        self, event_time_unix_nanos: int
    ) -> "AdvanceExecutionTimeResponse":
        from kairospy.infrastructure.contracts.execution.types import (
            AdvanceExecutionTimeRequest,
        )

        return self.control.advance_time(
            AdvanceExecutionTimeRequest(event_time_unix_nanos)
        )

    def backtest_market(self, event: object) -> "ExecutionBacktestMarketResponse":
        from kairospy.investment.apps.execution.application.mapping import (
            backtest_market_request,
        )

        request = backtest_market_request(event)
        if request is None:
            raise ValueError("unsupported Execution backtest Market event")
        return self.control.backtest_market(request)

    def current_view(self, instance: InstanceWorkspace):
        from kairospy.infrastructure.contracts.execution import ExecutionCurrentView

        return ExecutionCurrentView(
            instance.snapshot(),
            instance.workspace.workspace_id,
            instance.launch_id,
            instance.instance_id,
        )


class MarketSystemClient(SystemRpcClient):
    control: MarketControlClient

    def __post_init__(self) -> None:
        super().__post_init__()
        from kairospy.infrastructure.contracts.market import MarketControlClient

        object.__setattr__(
            self,
            "control",
            MarketControlClient(self.socket_path, timeout=self.timeout),
        )

    def data_routes(
        self,
        *,
        market_id: str | None = None,
        instrument_id: str | None = None,
        observation_kind: str | None = None,
        provider: str | None = None,
        configured_only: bool = False,
        ready_only: bool = False,
    ) -> dict[str, Any]:
        query: dict[str, object] = {}
        if configured_only:
            query["configured_only"] = True
        if ready_only:
            query["ready_only"] = True
        for name, value in (
            ("market_id", market_id),
            ("instrument_id", instrument_id),
            ("observation_kind", observation_kind),
            ("provider", provider),
        ):
            if value is not None:
                query[name] = value
        response = self.control.data_routes(**query)
        return {
            "routes": [
                {
                    "market_id": route.market_id,
                    "provider": route.provider,
                    "observation_kinds": list(route.observation_kinds),
                    "state": route.state,
                    "selected": route.selected,
                    "pending_reason": route.pending_reason,
                }
                for route in response.routes
            ]
        }

    def recover(self) -> dict[str, Any]:
        response = self.control.recover()
        return {"status": response.status}

    def pause_replay(self) -> dict[str, Any]:
        return {"status": self.control.pause_replay().status}

    def resume_replay(self) -> dict[str, Any]:
        return {"status": self.control.resume_replay().status}


class RiskSystemClient(SystemRpcClient):
    control: _RiskControl

    def __post_init__(self) -> None:
        super().__post_init__()
        from kairospy.infrastructure.contracts.risk import RiskControlClient

        object.__setattr__(
            self,
            "control",
            RiskControlClient(self.socket_path, timeout=self.timeout),
        )

    def configure(self, request: object) -> dict[str, Any]:
        return {"status": self.control.publish_policy(request).status}

    def assess(self, request: object) -> dict[str, Any]:
        return _risk_decision(self.control.pre_trade_check(request))

    def reserve(self, request: object) -> dict[str, Any]:
        return _risk_decision(self.control.authorize_and_reserve(request))

    def release(self, request: object) -> dict[str, Any]:
        return _risk_reservation(self.control.release_reservation(request))

    def consume(self, request: object) -> dict[str, Any]:
        return _risk_reservation(self.control.consume_reservation(request))

    def resize(self, request: object) -> dict[str, Any]:
        return _risk_reservation(self.control.resize_reservation(request))

    def open_circuit(self, request: object) -> dict[str, Any]:
        return _risk_circuit_state(self.control.open_circuit(request))

    def close_circuit(self, request: object) -> dict[str, Any]:
        return _risk_circuit_state(self.control.close_circuit(request))

    def advance_time(self, event_time_unix_nanos: int) -> dict[str, Any]:
        from kairospy.infrastructure.contracts.risk import AdvanceRiskTimeRequest

        value = self.control.advance_time(AdvanceRiskTimeRequest(event_time_unix_nanos))
        return {
            "event_time_unix_nanos": value.event_time_unix_nanos,
            "expired": value.expired,
        }

    def health(self) -> dict[str, Any]:
        value = self.control.health()
        return {
            "status": value.status,
            "generation": value.generation,
            "event_sequence": value.event_sequence,
            "policy_version": value.policy_version,
            "reservation_count": value.reservation_count,
            "open_circuit_count": value.open_circuit_count,
        }

    def latest_metadata(self, *, actor_id: str) -> dict[str, Any]:
        return self.latest(actor_id=actor_id)

    def latest(self, *, actor_id: str) -> dict[str, Any]:
        current_view = self.latest_view(actor_id=actor_id)
        snapshot = current_view.snapshot()
        limits = [_risk_limit(value) for value in snapshot.limits]
        reservations = [_risk_reservation(value) for value in snapshot.reservations]
        circuits = [_risk_circuit(value) for value in snapshot.circuits]
        return {
            "actor_id": snapshot.actor_id,
            "kind": "latest",
            "generation": snapshot.generation,
            "path": str(current_view.path),
            "policy_version": snapshot.policy_version,
            "limits": limits,
            "active_reservations": reservations,
            "circuits": circuits,
            "summary": {
                "limit_count": len(limits),
                "active_reservation_count": len(reservations),
                "open_circuit_count": sum(
                    1 for value in circuits if value["status"] == "open"
                ),
            },
            "applied_event_sequence": snapshot.applied_event_sequence,
        }

    def latest_limits(self, *, actor_id: str) -> dict[str, Any]:
        current_view = self.latest_view(actor_id=actor_id)
        return {
            "actor_id": actor_id,
            "limits": [_risk_limit(value) for value in current_view.snapshot().limits],
        }

    def latest_reservations(self, *, actor_id: str) -> dict[str, Any]:
        current_view = self.latest_view(actor_id=actor_id)
        return {
            "actor_id": actor_id,
            "active_reservations": [
                _risk_reservation(value) for value in current_view.snapshot().reservations
            ],
        }

    def latest_circuits(self, *, actor_id: str) -> dict[str, Any]:
        current_view = self.latest_view(actor_id=actor_id)
        return {
            "actor_id": actor_id,
            "circuits": [_risk_circuit(value) for value in current_view.snapshot().circuits],
        }

    def latest_view(self, *, actor_id: str):
        from kairospy.infrastructure.contracts.risk import RiskCurrentView

        if self.workspace_id is None:
            raise RuntimeError("Risk indexed current view requires workspace identity")
        return RiskCurrentView(
            self.require_view_root(),
            actor_id,
            self.workspace_id,
            self.launch_id,
            self.instance_id,
        )

def _risk_scope(value: Any) -> dict[str, str | None]:
    return {
        "account_id": value.account_id,
        "strategy_id": value.strategy_id,
        "instrument_id": value.instrument_id,
        "exchange_id": value.exchange_id,
    }


def _risk_limit(value: Any) -> dict[str, Any]:
    policy = value.policy
    return {
        "policy": {
            "policy_id": policy.policy_id,
            "version": policy.version,
            "scope": _risk_scope(policy.scope),
            "metric": policy.metric,
            "limit": str(policy.limit.value),
            "enforcement": policy.enforcement,
            "valid_from_unix_nanos": policy.valid_from_unix_nanos,
            "valid_until_unix_nanos": policy.valid_until_unix_nanos,
            "window_nanos": policy.window_nanos,
        },
        "used": str(value.used.value),
        "reserved": str(value.reserved.value),
        "available": str(value.available.value),
    }


def _risk_reservation(value: Any) -> dict[str, Any]:
    return {
        "reservation_id": value.reservation_id,
        "request_id": value.request_id,
        "account_id": value.account_id,
        "strategy_id": value.strategy_id,
        "idempotency_key": value.idempotency_key,
        "allocations": [
            {
                "policy_id": item.policy_id,
                "metric": item.metric,
                "amount": str(item.amount.value),
            }
            for item in value.allocations
        ],
        "status": value.status,
        "created_at_unix_nanos": value.created_at_unix_nanos,
        "updated_at_unix_nanos": value.updated_at_unix_nanos,
        "expires_at_unix_nanos": value.expires_at_unix_nanos,
        "policy_version": value.policy_version,
    }


def _risk_decision(value: Any) -> dict[str, Any]:
    return {
        "decision_id": value.decision_id,
        "request_id": value.request_id,
        "account_id": value.account_id,
        "strategy_id": value.strategy_id,
        "instrument_id": value.instrument_id,
        "allowed": value.allowed,
        "degraded": value.degraded,
        "reason_codes": list(value.reason_codes),
        "violations": list(value.violations),
        "allocations": [
            {
                "policy_id": item.policy_id,
                "metric": item.metric,
                "amount": item.amount.value,
            }
            for item in value.allocations
        ],
        "reservation": (
            None if value.reservation is None else _risk_reservation(value.reservation)
        ),
        "policy_version": value.policy_version,
        "dependency_generation": value.dependency_generation,
        "dependency_event_sequence": value.dependency_event_sequence,
        "evaluated_at_unix_nanos": value.evaluated_at_unix_nanos,
    }


def _risk_circuit_state(value: Any) -> dict[str, Any]:
    return {
        "scope": _risk_scope(value.scope),
        "open": value.open,
        "opened_at_unix_nanos": value.opened_at_unix_nanos,
        "reset_at_unix_nanos": value.reset_at_unix_nanos,
        "reason": value.reason,
    }


def _risk_circuit(value: Any) -> dict[str, Any]:
    scope = _risk_scope(value.scope)
    scope.pop("instrument_id")
    return {
        "circuit_id": value.circuit_id,
        "scope": scope,
        "status": value.status,
        "opened_at_unix_nanos": value.opened_at_unix_nanos,
        "reset_at_unix_nanos": value.reset_at_unix_nanos,
        "reason": value.reason,
    }


class CapitalSystemClient(SystemRpcClient):
    control: CapitalControlClient

    def __post_init__(self) -> None:
        super().__post_init__()
        from kairospy.infrastructure.contracts.capital import CapitalControlClient

        object.__setattr__(
            self,
            "control",
            CapitalControlClient(self.socket_path, timeout=self.timeout),
        )

    def health(self) -> dict[str, Any]:
        value = self.control.health()
        return {"status": value.status}

    def publish_funding_objective(
        self, request: object
    ) -> dict[str, Any]:
        return _capital_control_response(
            self.control.publish_funding_objective(request)
        )

    def cancel_funding_objective_request(
        self, request: object
    ) -> dict[str, Any]:
        return _capital_control_response(self.control.cancel_funding_objective(request))

    def observe_capital_demand(
        self, request: object
    ) -> dict[str, Any]:
        return _capital_demand_response(self.control.observe_capital_demand(request))

    def reconcile_plan(
        self,
        *,
        capital_group_id: str,
        plan_id: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        from kairospy.infrastructure.contracts.capital.types import (
            ReconcileCapitalPlanRequest,
        )

        request = ReconcileCapitalPlanRequest(
            request_id or f"capital.reconcile:{time.time_ns()}",
            capital_group_id,
            plan_id,
            time.time_ns(),
        )
        return _capital_reconcile_response(
            self.control.reconcile_capital_plan(request)
        )

    def reconcile_plan_request(
        self, request: object
    ) -> dict[str, Any]:
        return _capital_reconcile_response(
            self.control.reconcile_capital_plan(request)
        )

    def current_view(self, capital_group_id: str):
        from kairospy.infrastructure.contracts.capital import CapitalCurrentView

        if self.workspace_id is None:
            raise RuntimeError(
                "Capital indexed current view requires workspace identity"
            )
        return CapitalCurrentView(
            self.require_view_root(),
            capital_group_id,
            self.workspace_id,
            self.launch_id,
            self.instance_id,
        )

    def current_metadata(self, capital_group_id: str) -> dict[str, Any]:
        return self.current(capital_group_id)

    def current(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return current_view.snapshot()

    def current_availabilities(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "availabilities": list(current_view.snapshot().availabilities),
        }

    def current_objectives(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "objectives": list(current_view.snapshot().objectives),
        }

    def current_demands(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "demands": list(current_view.snapshot().demands),
        }

    def current_plans(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "plans": list(current_view.snapshot().plans),
        }

    def current_routes(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "routes": list(current_view.snapshot().routes),
        }

    def current_reservations(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "reservations": list(current_view.snapshot().reservations),
        }

    def current_operations(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "operations": list(current_view.snapshot().operations),
        }

    def current_alerts(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "alerts": list(current_view.snapshot().alerts),
        }


class ReferenceSystemClient(SystemRpcClient):
    control: ReferenceControlClient
    reader: ReferenceClient

    def __post_init__(self) -> None:
        super().__post_init__()
        from kairospy.infrastructure.contracts.reference import (
            ReferenceClient,
            ReferenceControlClient,
        )

        object.__setattr__(
            self,
            "control",
            ReferenceControlClient(self.socket_path, timeout=self.timeout),
        )
        object.__setattr__(
            self,
            "reader",
            ReferenceClient(
                socket_path=self.socket_path,
                database_path=self.database_path,
                timeout=self.timeout,
            ),
        )

    def publish(self) -> dict[str, Any]:
        return dict(self.control.publish())

    def add_asset(self, asset: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.control.add_asset(asset))

    def application_client(self):
        return self.reader

    def health(self) -> dict[str, Any]:
        return self.reader.health()

    def providers(self) -> dict[str, Any]:
        return self.reader.providers()

    def catalog(self) -> dict[str, Any]:
        return self.reader.catalog()

    def events(self, **filters: Any) -> dict[str, Any]:
        return self.reader.events(**filters)

    def snapshot(self):
        return self.reader.snapshot()


def _account_refresh_response(value: object) -> dict[str, Any]:
    return {
        "status": getattr(value, "status"),
        "account_id": getattr(value, "account_id"),
        "segments": list(getattr(value, "segments")),
    }


def _capital_control_response(value: object) -> dict[str, Any]:
    return {
        "request_id": getattr(value, "request_id"),
        "objective_id": getattr(value, "objective_id"),
        "version": getattr(value, "version"),
        "status": getattr(value, "status"),
        "error_code": getattr(value, "error_code"),
        "error_message": getattr(value, "error_message"),
        "retryable": getattr(value, "retryable"),
    }


def _capital_demand_response(value: object) -> dict[str, Any]:
    return {
        "request_id": getattr(value, "request_id"),
        "demand_id": getattr(value, "demand_id"),
        "status": getattr(value, "status"),
        "error_code": getattr(value, "error_code"),
        "error_message": getattr(value, "error_message"),
        "retryable": getattr(value, "retryable"),
    }


def _capital_reconcile_response(value: object) -> dict[str, Any]:
    return {
        "request_id": getattr(value, "request_id"),
        "plan_id": getattr(value, "plan_id"),
        "status": getattr(value, "status"),
        "error_code": getattr(value, "error_code"),
        "error_message": getattr(value, "error_message"),
        "retryable": getattr(value, "retryable"),
    }


def system_client(
    component: str, socket_path: str | Path, *, timeout: float = 3.0
) -> SystemRpcClient:
    clients = {
        "account": AccountSystemClient,
        "execution": ExecutionSystemClient,
        "market": MarketSystemClient,
        "reference": ReferenceSystemClient,
        "risk": RiskSystemClient,
        "capital": CapitalSystemClient,
    }
    client_type = clients.get(component, SystemRpcClient)
    return client_type(Path(socket_path), timeout=timeout)


@dataclass(frozen=True, slots=True)
class InstanceSystemClients:
    """Typed business clients owned by one launched Conflux instance."""

    accounts: Mapping[Any, AccountSystemClient]
    market: MarketSystemClient | None = None
    risk: RiskSystemClient | None = None
    execution: ExecutionSystemClient | None = None
    capital: CapitalSystemClient | None = None
    reference: ReferenceSystemClient | None = None

    @classmethod
    def from_connections(cls, connections: Any) -> "InstanceSystemClients":
        return cls(
            accounts={
                account_id: AccountSystemClient(
                    connection.socket,
                    view_root=connection.view_root,
                    workspace_id=connections.workspace_id,
                    launch_id=connections.launch_id,
                    instance_id=connections.instance_id,
                )
                for account_id, connection in connections.accounts.items()
            },
            market=(
                None
                if connections.market is None
                else MarketSystemClient(
                    connections.market.socket,
                    view_root=connections.market.view_root,
                )
            ),
            risk=(
                None
                if connections.risk is None
                else RiskSystemClient(
                    connections.risk.socket,
                    view_root=connections.risk.view_root,
                    workspace_id=connections.workspace_id,
                    launch_id=connections.launch_id,
                    instance_id=connections.instance_id,
                )
            ),
            execution=(
                None
                if connections.execution is None
                else ExecutionSystemClient(
                    connections.execution.socket,
                    view_root=connections.execution.view_root,
                )
            ),
            capital=(
                None
                if connections.capital is None
                else CapitalSystemClient(
                    connections.capital.socket,
                    view_root=connections.capital.view_root,
                    workspace_id=connections.workspace_id,
                    launch_id=connections.launch_id,
                    instance_id=connections.instance_id,
                )
            ),
            reference=(
                None
                if connections.reference is None
                else ReferenceSystemClient(
                    connections.reference.socket,
                    database_path=connections.reference.database,
                    actor_id=connections.reference.actor_id,
                )
            ),
        )


__all__ = [
    "SystemRpcClient",
    "AccountSystemClient",
    "ExecutionSystemClient",
    "MarketSystemClient",
    "RiskSystemClient",
    "CapitalSystemClient",
    "ReferenceSystemClient",
    "InstanceSystemClients",
    "system_client",
]
