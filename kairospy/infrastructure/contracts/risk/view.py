"""Risk owner-owned native current-view queries."""

from __future__ import annotations

from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any

from kairospy.primitives.account import AccountId


def risk_indexed_environment_path(root: str | Path, actor_id: str) -> Path:
    return Path(root) / "views" / "v3" / "Risk" / f"risk-{_component(actor_id)}" / "epoch-1" / "current.lmdb"


def _component(value: str) -> str:
    return "".join(chr(byte) if byte < 128 and (chr(byte).isalnum() or chr(byte) in "-_.") else f"%{byte:02X}" for byte in value.encode())


class RiskIndexedViewQueries:
    """Stable business queries backed only by the Risk native contract."""

    def __init__(self, root: str | Path, *, actor_id: str, workspace_id: str, launch_id: str | None, instance_id: str | None) -> None:
        native = import_module("kairospy._native_risk_contract")
        info = native.build_info()
        if info.api_version != 1 or info.owner != "Risk":
            raise RuntimeError("incompatible Risk native contract binding")
        self.actor_id = actor_id
        self.path = risk_indexed_environment_path(root, actor_id)
        self._native: Any = native
        self._args = (Path(root), actor_id, workspace_id, launch_id, instance_id)
        self._reader: Any | None = None

    def _open(self) -> Any:
        if self._reader is None:
            self._reader = self._native.RiskCurrentView(*self._args)
        return self._reader

    def _snapshot(self) -> Any:
        return self._open().snapshot()

    def latest(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        limits = tuple(_limit(value) for value in snapshot.limits)
        reservations = tuple(_reservation(value) for value in snapshot.reservations)
        circuits = tuple(_circuit(value) for value in snapshot.circuits)
        return {"actor_id": snapshot.actor_id, "kind": "latest", "generation": snapshot.generation, "path": str(self.path), "policy_version": snapshot.policy_version, "limits": list(limits), "active_reservations": list(reservations), "circuits": list(circuits), "summary": {"limit_count": len(limits), "active_reservation_count": len(reservations), "open_circuit_count": sum(1 for value in circuits if value["status"] == "open")}, "applied_event_sequence": snapshot.applied_event_sequence}

    def limits(self) -> tuple[dict[str, Any], ...]:
        return tuple(_limit(value) for value in self._snapshot().limits)

    def active_reservations(self) -> tuple[dict[str, Any], ...]:
        return tuple(_reservation(value) for value in self._snapshot().reservations)

    def circuits(self) -> tuple[dict[str, Any], ...]:
        return tuple(_circuit(value) for value in self._snapshot().circuits)

    def status(self, account_id: AccountId) -> dict[str, object]:
        snapshot = self._snapshot()
        usages = tuple(value for value in snapshot.limits if value.policy.scope.account_id in {None, str(account_id)} and value.policy.metric == "notional")
        circuits = tuple(value for value in snapshot.circuits if value.scope.account_id in {None, str(account_id)})
        available = sum((_decimal(value.available) for value in usages), Decimal(0))
        reserved = sum((_decimal(value.reserved) for value in usages), Decimal(0))
        violations = [{"code": "circuit_open", "message": value.reason or "Risk circuit is open", "limit": None, "actual": None} for value in circuits if value.status == "open"]
        return {"account_id": str(account_id), "trading_allowed": not violations, "available_notional": format(available, "f") if usages else None, "reserved_notional": format(reserved, "f"), "utilization": None, "violations": violations, "generation": snapshot.applied_event_sequence}

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None


def _scope(value: Any) -> dict[str, str | None]:
    return {"account_id": value.account_id, "strategy_id": value.strategy_id, "instrument_id": value.instrument_id, "exchange_id": value.exchange_id}

def _policy(value: Any) -> dict[str, Any]:
    return {"policy_id": value.policy_id, "version": value.version, "scope": _scope(value.scope), "metric": value.metric, "limit": _decimal_text(value.limit), "enforcement": value.enforcement, "valid_from_unix_nanos": value.valid_from_unix_nanos, "valid_until_unix_nanos": value.valid_until_unix_nanos, "window_nanos": value.window_nanos}

def _limit(value: Any) -> dict[str, Any]:
    return {"policy": _policy(value.policy), "used": _decimal_text(value.used), "reserved": _decimal_text(value.reserved), "available": _decimal_text(value.available)}

def _reservation(value: Any) -> dict[str, Any]:
    return {"reservation_id": value.reservation_id, "request_id": value.request_id, "account_id": value.account_id, "strategy_id": value.strategy_id, "instrument_id": "", "idempotency_key": value.idempotency_key, "requested_usages": [], "allocations": [{"policy_id": item.policy_id, "metric": item.metric, "amount": _decimal_text(item.amount)} for item in value.allocations], "status": value.status, "created_at_unix_nanos": value.created_at_unix_nanos, "updated_at_unix_nanos": value.updated_at_unix_nanos, "expires_at_unix_nanos": value.expires_at_unix_nanos, "policy_version": value.policy_version}

def _circuit(value: Any) -> dict[str, Any]:
    scope = _scope(value.scope)
    scope.pop("instrument_id")
    return {"circuit_id": value.circuit_id, "scope": scope, "status": value.status, "opened_at_unix_nanos": value.opened_at_unix_nanos, "reset_at_unix_nanos": value.reset_at_unix_nanos, "reason": value.reason}

def _decimal(value: Any) -> Decimal:
    return Decimal(value.mantissa).scaleb(-value.scale)

def _decimal_text(value: Any) -> str:
    return str(_decimal(value))

__all__ = ["RiskIndexedViewQueries", "risk_indexed_environment_path"]
