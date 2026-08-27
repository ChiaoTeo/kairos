"""Typed clients for already-running business processes.

These clients are part of the System boundary. They expose health and control
commands through JSON-RPC; business state is read from module-owned typed indexed
views.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from kairospy.infrastructure.unix_http import request_sync

if TYPE_CHECKING:
    from kairospy.system.apps.workspace.application import InstanceWorkspace
    from kairospy.primitives.account import AccountId
    from kairospy.infrastructure.contracts.account.runtime import (
        AccountContractClient,
    )
    from kairospy.infrastructure.contracts.capital.client import CapitalContractClient
    from kairospy.infrastructure.contracts.execution.control import (
        ExecutionControlClient,
    )
    from kairospy.infrastructure.contracts.market.control import MarketControlClient
    from kairospy.infrastructure.contracts.reference.client import ReferenceClient
    from kairospy.infrastructure.contracts.reference.control import (
        ReferenceControlClient,
    )
    from kairospy.infrastructure.contracts.risk.control import RiskControlClient


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
            raise RuntimeError("component connection manifest is missing a view_root path")
        return self.view_root


class AccountSystemClient(SystemRpcClient):
    control: AccountContractClient

    def __post_init__(self) -> None:
        super().__post_init__()
        from kairospy.infrastructure.contracts.account import AccountContractClient

        object.__setattr__(
            self,
            "control",
            AccountContractClient(self.socket_path, timeout=self.timeout),
        )

    def reconcile(self) -> dict[str, Any]:
        return dict(self.control.reconcile({}))

    def refresh(self) -> dict[str, Any]:
        return dict(self.control.refresh({}))

    def advance_time(self, event_time_unix_nanos: int) -> dict[str, Any]:
        return dict(self.control.advance_time(event_time_unix_nanos))

    def mark_to_market_event(self, event: object) -> dict[str, Any] | None:
        from kairospy.investment.apps.account.application.mapping import (
            backtest_mark_to_market_request,
        )

        request = backtest_mark_to_market_request(event)
        if request is None:
            return None
        result = self.control.mark_to_market(request)
        return {"result": result, "segment_key": request["segment_key"]}

    def current_view(self, account_id: AccountId):
        from kairospy.infrastructure.contracts.account import AccountCurrentViewReader

        return AccountCurrentViewReader(
            self.require_view_root(),
            account_id=account_id,
            workspace_id=self._require_workspace_id(),
            launch_id=self.launch_id,
            instance_id=self.instance_id,
        )

    def observed_orders_view(self, account_id: AccountId):
        from kairospy.infrastructure.contracts.account import (
            AccountObservedOrdersViewReader,
        )

        return AccountObservedOrdersViewReader(
            self.require_view_root(),
            account_id=account_id,
            workspace_id=self._require_workspace_id(),
            launch_id=self.launch_id,
            instance_id=self.instance_id,
        )

    def _require_workspace_id(self) -> str:
        if self.workspace_id is None:
            raise RuntimeError("Account indexed current view requires workspace identity")
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

    def routes(self, query: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return dict(self.control.routes(query or {}))

    def submit_intent(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.control.submit_intent(intent))

    def cancel_intent(self, intent_id: str, *, reason: str = "") -> dict[str, Any]:
        raise NotImplementedError("cancel_intent is not part of ExecutionControlRpc")

    def expire_intent(self, intent_id: str, *, reason: str = "") -> dict[str, Any]:
        raise NotImplementedError("expire_intent is not part of ExecutionControlRpc")

    def submit(
        self, request: Mapping[str, Any], *, dry_run: bool = False
    ) -> dict[str, Any]:
        if dry_run:
            raise NotImplementedError("dry-run submit is not part of ExecutionControlRpc")
        return dict(self.control.submit_intent(request))

    def cancel(self, order_id: str, reason: str = "system cancel") -> dict[str, Any]:
        return dict(self.control.cancel_order(order_id, {"reason": reason}))

    def replace(self, order_id: str, replacement: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.control.replace_order(order_id, replacement))

    def advance_time(self, event_time_unix_nanos: int) -> dict[str, Any]:
        return dict(self.control.advance_time(event_time_unix_nanos))

    def backtest_market(self, event: object) -> dict[str, Any]:
        from kairospy.investment.apps.execution.application.mapping import backtest_market_payload

        payload = backtest_market_payload(event)
        if payload is None:
            return {"fills": []}
        return dict(self.control.backtest_market(payload))

    def current_view(self, instance: InstanceWorkspace):
        from kairospy.infrastructure.contracts.execution import ExecutionCurrentViews

        return ExecutionCurrentViews(instance)


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

    def data_routes(self, query: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return dict(self.control.data_routes(query or {}))

    def subscribe(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.control.subscribe(request))

    def unsubscribe(self, request: Mapping[str, Any]) -> dict[str, Any]:
        subscription_id = str(request.get("subscription_id", ""))
        headers = {key: str(value) for key, value in request.items() if key != "subscription_id"}
        return dict(self.control.unsubscribe(subscription_id, headers=headers))

    def recover(self) -> dict[str, Any]:
        return dict(self.control.recover({}))

    def pause_replay(self) -> dict[str, Any]:
        return dict(self.control.pause_replay())

    def resume_replay(self) -> dict[str, Any]:
        return dict(self.control.resume_replay())


class RiskSystemClient(SystemRpcClient):
    control: RiskControlClient

    def __post_init__(self) -> None:
        super().__post_init__()
        from kairospy.infrastructure.contracts.risk import RiskControlClient

        object.__setattr__(
            self,
            "control",
            RiskControlClient(self.socket_path, timeout=self.timeout),
        )

    def configure(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.control.publish_policy(request))

    def assess(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.control.pre_trade_check(request))

    def reserve(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.control.authorize_and_reserve(request))

    def release(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.control.release_reservation(request))

    def consume(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.control.consume_reservation(request))

    def resize(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.control.resize_reservation(request))

    def open_circuit(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.control.open_circuit(request))

    def close_circuit(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.control.close_circuit(request))

    def advance_time(self, event_time_unix_nanos: int) -> dict[str, Any]:
        return dict(self.control.advance_time(event_time_unix_nanos))

    def health(self) -> dict[str, Any]:
        return dict(self.control.health())

    def latest_metadata(self, *, actor_id: str) -> dict[str, Any]:
        return self.latest(actor_id=actor_id)

    def latest(self, *, actor_id: str) -> dict[str, Any]:
        current_view = self.latest_view(actor_id=actor_id)
        return current_view.latest()

    def latest_limits(self, *, actor_id: str) -> dict[str, Any]:
        current_view = self.latest_view(actor_id=actor_id)
        return {
            "actor_id": actor_id,
            "limits": list(current_view.limits()),
        }

    def latest_reservations(self, *, actor_id: str) -> dict[str, Any]:
        current_view = self.latest_view(actor_id=actor_id)
        return {
            "actor_id": actor_id,
            "active_reservations": list(current_view.active_reservations()),
        }

    def latest_circuits(self, *, actor_id: str) -> dict[str, Any]:
        current_view = self.latest_view(actor_id=actor_id)
        return {
            "actor_id": actor_id,
            "circuits": list(current_view.circuits()),
        }

    def latest_view(self, *, actor_id: str):
        from kairospy.infrastructure.contracts.risk import RiskIndexedViewQueries

        if self.workspace_id is None:
            raise RuntimeError("Risk indexed current view requires workspace identity")
        return RiskIndexedViewQueries(
            self.require_view_root(),
            actor_id=actor_id,
            workspace_id=self.workspace_id,
            launch_id=self.launch_id,
            instance_id=self.instance_id,
        )


class CapitalSystemClient(SystemRpcClient):
    control: CapitalContractClient

    def __post_init__(self) -> None:
        super().__post_init__()
        from kairospy.infrastructure.contracts.capital import CapitalContractClient

        object.__setattr__(
            self,
            "control",
            CapitalContractClient(self.socket_path, timeout=self.timeout),
        )

    def health(self) -> dict[str, Any]:
        return dict(self.control.health())

    def publish_funding_objective_request(
        self, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        return dict(self.control.publish_funding_objective_request(dict(request)))

    def cancel_funding_objective(
        self, objective_id: str, *, expected_version: int, **scope: object
    ) -> dict[str, Any]:
        return dict(
            self.control.cancel_funding_objective(
                objective_id, expected_version=expected_version, **scope
            )
        )

    def cancel_funding_objective_request(
        self, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        return dict(self.control.cancel_funding_objective_request(dict(request)))

    def observe_capital_demand_request(
        self, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        return dict(self.control.observe_capital_demand_request(dict(request)))

    def reconcile_plan(
        self,
        *,
        capital_group_id: str,
        plan_id: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return dict(
            self.control.reconcile_plan(
                capital_group_id=capital_group_id,
                plan_id=plan_id,
                request_id=request_id,
            )
        )

    def reconcile_plan_request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return dict(self.control.reconcile_plan_request(dict(request)))

    def current_view(self, capital_group_id: str):
        from kairospy.infrastructure.contracts.capital import CapitalIndexedViewQueries

        if self.workspace_id is None:
            raise RuntimeError("Capital indexed current view requires workspace identity")
        return CapitalIndexedViewQueries(
            self.require_view_root(), capital_group_id,
            workspace_id=self.workspace_id, launch_id=self.launch_id, instance_id=self.instance_id,
        )

    def current_metadata(self, capital_group_id: str) -> dict[str, Any]:
        return self.current(capital_group_id)

    def current(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return current_view.current()

    def current_availabilities(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "availabilities": list(current_view.availabilities()),
        }

    def current_objectives(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "objectives": list(current_view.objectives()),
        }

    def current_demands(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "demands": list(current_view.demands()),
        }

    def current_plans(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "plans": list(current_view.plans()),
        }

    def current_routes(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "routes": list(current_view.routes()),
        }

    def current_reservations(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "reservations": list(current_view.reservations()),
        }

    def current_operations(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "operations": list(current_view.operations()),
        }

    def current_alerts(self, capital_group_id: str) -> dict[str, Any]:
        current_view = self.current_view(capital_group_id)
        return {
            "capital_group_id": capital_group_id,
            "alerts": list(current_view.alerts()),
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
