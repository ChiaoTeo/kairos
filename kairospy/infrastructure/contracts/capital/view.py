"""Capital owner-owned native current-view queries."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any


def capital_indexed_environment_path(root: str | Path, capital_group_id: str) -> Path:
    return Path(root) / "views" / "v3" / "Capital" / f"capital-{_component(capital_group_id)}" / "epoch-1" / "current.lmdb"


class CapitalIndexedViewQueries:
    """Stable business queries backed only by the Capital native contract."""

    def __init__(self, root: str | Path, capital_group_id: str, *, workspace_id: str, launch_id: str | None, instance_id: str | None) -> None:
        native = import_module("kairospy._native_capital_contract")
        info = native.build_info()
        if info.api_version != 1 or info.owner != "Capital":
            raise RuntimeError("incompatible Capital native contract binding")
        self.capital_group_id = capital_group_id
        self.path = capital_indexed_environment_path(root, capital_group_id)
        self._native: Any = native
        self._args = (Path(root), capital_group_id, workspace_id, launch_id, instance_id)
        self._reader: Any | None = None

    def _open(self) -> Any:
        if self._reader is None:
            self._reader = self._native.CapitalCurrentView(*self._args)
        return self._reader

    def _snapshot(self) -> Any:
        return self._open().snapshot()

    def current(self) -> dict[str, Any]:
        value = self._snapshot()
        availabilities = tuple(_availability(item, value.capital_group_id) for item in value.availabilities)
        alerts = tuple(_alert(item) for item in value.alerts)
        return {"capital_group_id": value.capital_group_id, "kind": "current", "generation": value.event_sequence, "applied_event_sequence": value.applied_event_sequence, "path": str(self.path), "strategy_id": value.strategy_id, "environment": value.environment, "membership_version": value.membership_version, "event_sequence": value.event_sequence, "journal_sequence": value.journal_sequence, "summary": {"objective_count": len(value.objectives), "demand_count": len(value.demands), "policy_count": value.policy_count, "facts_count": value.facts_count, "availability_count": len(availabilities), "route_count": len(value.routes), "plan_count": len(value.plans), "reservation_count": len(value.reservations), "operation_count": len(value.operations), "alert_count": len(alerts), "ready_availability_count": sum(1 for item in availabilities if item["readiness"] == "ready"), "degraded_availability_count": sum(1 for item in availabilities if item["readiness"] == "degraded"), "critical_alert_count": sum(1 for item in alerts if item["severity"] == "critical")}, "availabilities": list(availabilities), "alerts": list(alerts)}

    def availabilities(self) -> tuple[dict[str, Any], ...]: return tuple(_availability(v, self.capital_group_id) for v in self._snapshot().availabilities)
    def objectives(self) -> tuple[dict[str, Any], ...]: return tuple(_objective(v) for v in self._snapshot().objectives)
    def demands(self) -> tuple[dict[str, Any], ...]: return tuple(_demand(v) for v in self._snapshot().demands)
    def policies(self) -> tuple[dict[str, Any], ...]: return tuple(_policy(v) for v in self._snapshot().policies)
    def facts(self) -> tuple[dict[str, Any], ...]: return tuple(_facts(v) for v in self._snapshot().facts)
    def plans(self) -> tuple[dict[str, Any], ...]: return tuple(_plan(v) for v in self._snapshot().plans)
    def routes(self) -> tuple[dict[str, Any], ...]: return tuple(_route(v) for v in self._snapshot().routes)
    def reservations(self) -> tuple[dict[str, Any], ...]: return tuple(_reservation(v) for v in self._snapshot().reservations)
    def operations(self) -> tuple[dict[str, Any], ...]: return tuple(_operation(v) for v in self._snapshot().operations)
    def alerts(self) -> tuple[dict[str, Any], ...]: return tuple(_alert(v) for v in self._snapshot().alerts)

    def availability(self, *, capital_group_id: str | None, location: object | None) -> dict[str, Any]:
        if capital_group_id != self.capital_group_id: raise ValueError("Capital current view belongs to another capital group")
        values = self.availabilities()
        if location is None:
            if len(values) != 1: raise ValueError("Capital location is required when the group has multiple locations")
            return values[0]
        expected = {"broker": str(getattr(location,"broker")), "account_id": str(getattr(location,"account_id")), "segment": str(getattr(location,"segment")), "asset": str(getattr(location,"asset"))}
        for value in values:
            if value["location"] == expected: return value
        raise LookupError("Capital location has not been evaluated")

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None


def _location(v: Any) -> dict[str,str]: return {"broker":v.broker,"account_id":v.account_id,"segment":v.segment,"asset":v.asset}
def _availability(v: Any, group: str) -> dict[str,Any]: return {"capital_group_id":group,"readiness":v.readiness,"location":_location(v.location),"policy_version":v.policy_version,"active_objective_ids":list(v.active_objective_ids),"active_demand_ids":list(v.active_demand_ids),"funding_horizons":[{"required_by_unix_nanos":h.required_by_unix_nanos,"objective_ids":list(h.objective_ids),"demand_ids":list(h.demand_ids),"desired_available":h.desired_available} for h in v.funding_horizons],"desired_target":v.desired_target,"observed_available":v.observed_available,"effective_target":v.effective_target,"deficit":v.deficit,"account_watermark":v.account_watermark,"risk_policy_version":v.risk_policy_version,"risk_watermark":v.risk_watermark,"reason":v.reason}
def _objective(v: Any) -> dict[str,Any]: return _fields(v,("objective_id","version","strategy_id","desired_available","required_by_unix_nanos","expires_at_unix_nanos","priority","confidence_bps","strategy_decision_id","status","updated_at_unix_nanos"),destination=_location(v.destination))
def _demand(v: Any) -> dict[str,Any]: return _fields(v,("demand_id","idempotency_key","strategy_id","observed_shortfall","observed_at_unix_nanos","required_by_unix_nanos","expires_at_unix_nanos","priority","confidence_bps","account_watermark","risk_watermark","launch_id","instance_id","status","updated_at_unix_nanos"),destination=_location(v.destination),causal_references=tuple(v.causal_references))
def _policy(v: Any) -> dict[str,Any]: return _fields(v,("version","minimum","default_target","maximum","stress_buffer","minimum_movement","hysteresis","deficit_dwell_nanos","cooldown_nanos","max_fact_age_nanos"),destination=_location(v.destination))
def _facts(v: Any) -> dict[str,Any]: return _fields(v,("observed_available","account_watermark","account_observed_at_unix_nanos","account_complete","risk_capacity","risk_policy_version","risk_watermark"),destination=_location(v.destination),earn_holdings=[_fields(h,("product_id","principal","redeemable_amount","immediately_redeemable","active")) for h in v.earn_holdings])
def _plan(v: Any) -> dict[str,Any]: return _fields(v,("plan_id","rebalance_decision_id","route_id","route_version","route_kind","amount","reservation_id","idempotency_key","selected_earn_product_id","source_account_watermark","destination_account_watermark","source_observed_available","destination_observed_available","redemption_account_watermark","redemption_observed_available","earn_principal_before","status","recovery_action","recovery_reason","recovery_decided_at_unix_nanos","created_at_unix_nanos","expires_at_unix_nanos"),source=_location(v.source),destination=_location(v.destination),objective_ids=tuple(v.objective_ids),demand_ids=tuple(v.demand_ids))
def _route(v: Any) -> dict[str,Any]: return _fields(v,("route_id","version","kind","per_operation_limit","daily_limit","required_source_authority","settlement_class","enabled","earn_product_id","demand_guard_nanos","allow_unknown_redemption_quota"),source=_location(v.source),destination=_location(v.destination))
def _reservation(v: Any) -> dict[str,Any]: return _fields(v,("reservation_id","plan_id","amount","source_account_watermark","status","created_at_unix_nanos","expires_at_unix_nanos"),source=_location(v.source))
def _operation(v: Any) -> dict[str,Any]: return _fields(v,("operation_id","plan_id","idempotency_key","operation_index","kind","status","participant_operation_id","participant_state","dispatch_started_at_unix_nanos","attempt_count","failure_reason","account_observation_watermark","updated_at_unix_nanos"))
def _alert(v: Any) -> dict[str,Any]: return _fields(v,("alert_id","plan_id","operation_id","kind","severity","recovery_action","message","opened_at_unix_nanos"))
def _fields(v: Any, names: tuple[str,...], **extra: object) -> dict[str,Any]: return {**{name:getattr(v,name) for name in names},**extra}
def _component(value: str) -> str: return "".join(chr(b) if (b<128 and chr(b).isalnum()) or b in b"-_." else f"%{b:02X}" for b in value.encode())

__all__=["CapitalIndexedViewQueries","capital_indexed_environment_path"]
