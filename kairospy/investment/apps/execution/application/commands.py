from __future__ import annotations

import copy
from decimal import Decimal
import hashlib
import json
import time
from typing import Any, Mapping

from kairospy.strategy import CommandHandle, CommandEnvelope
from kairospy.investment.apps.execution.application import (
    ExecutionAlgorithmPolicy,
    HedgePolicy,
    ImmediateAlgorithm,
    MakerTakerHedgeAlgorithm,
    MakerExecutionPolicy,
    OptionSpreadRequest,
    PairArbitrageRequest,
    PortfolioRebalanceRequest,
    QuoteProvisioningRequest,
    QuoteRefreshRequest,
    SplitOrderPolicy,
    TargetPositionRequest,
    TwapAlgorithm,
    LimitOrderRequest,
    MarketOrderRequest,
    OrderRequest,
    ReplaceOrderRequest,
    IntentAdmissionEvidence,
)
from kairospy.investment.apps.reference.application import InstrumentRef
from kairospy.infrastructure.transport.json_rpc import JsonRpcCaller


class ExecutionCommandClient:
    def __init__(
        self,
        client: JsonRpcCaller,
        *,
        default_segment: str = "spot",
        allow_trading: bool = True,
        max_order_notional: Decimal | None = None,
        require_limit_orders: bool = False,
        launch_id: str | None = None,
    ) -> None:
        self.client = client
        self.default_segment = default_segment
        self.allow_trading = allow_trading
        self.max_order_notional = max_order_notional
        self.require_limit_orders = require_limit_orders
        self.launch_id = launch_id

    def _submit_v2_intent(
        self, envelope: Mapping[str, object]
    ) -> tuple[int, dict[str, Any]]:
        payload = envelope.get("payload")
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("intent"), Mapping
        ):
            return 422, {"error": "execution intent payload is required"}
        body: dict[str, object] = {
            "command_id": envelope.get("command_id"),
            "idempotency_key": envelope.get("idempotency_key")
            or envelope.get("command_id"),
            "caller_id": envelope.get("strategy_id"),
            "workspace_id": "workspace",
            "intent": dict(payload["intent"]),
        }
        evidence = payload.get("admission_evidence")
        if isinstance(evidence, Mapping):
            body["admission_evidence"] = dict(evidence)
        return 202, dict(self.client.call("execution_submit_intent", [body]))

    def target_position(
        self,
        request: TargetPositionRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        admission_evidence: IntentAdmissionEvidence | None = None,
    ) -> CommandHandle:
        if not instance_id.strip():
            return CommandHandle(
                request_id,
                "rejected",
                error="instance_id is required for execution intents",
            )
        intent_id = request.intent_id or f"{strategy_id}:intent:{request_id}"
        if not self.allow_trading:
            return CommandHandle(
                request_id,
                "rejected",
                error="launch live trading is disabled by safety policy",
            )
        if self.require_limit_orders and request.limit_price is None:
            return CommandHandle(
                request_id,
                "rejected",
                error="launch safety policy requires limit orders",
            )
        if self.max_order_notional is not None and request.limit_price is not None:
            if abs(request.quantity * request.limit_price) > self.max_order_notional:
                return CommandHandle(
                    request_id,
                    "rejected",
                    error="intent exceeds launch max_order_notional",
                )
        account_ids = list(request.account_ids) or (
            [request.account_id] if request.account_id else ["main"]
        )
        body = {
            "intent_id": intent_id,
            "strategy_decision_id": request.strategy_decision_id,
            "strategy_id": strategy_id,
            "launch_id": self.launch_id or "",
            "instance_id": instance_id,
            "account_ids": account_ids,
            "segment_key": request.segment_key,
            "instrument_id": request.instrument_id,
            "execution_route_id": request.execution_route_id,
            "intent_type": "TargetPosition",
            "algorithm": _execution_algorithm(request.algorithm),
            "target_quantity": _decimal(request.quantity),
            "limit_price": None
            if request.limit_price is None
            else _decimal(request.limit_price),
            "source_snapshot_id": request.source_snapshot_id,
            "source_event_sequence": request.source_event_sequence,
            "source_event_time_unix_nanos": request.source_event_time_unix_nanos,
            "reason": request.reason,
            "order_options": _execution_options(request.split, request.maker),
        }
        envelope = CommandEnvelope(
            command_id=request_id,
            operation="execution.submit_intent",
            strategy_id=strategy_id,
            instance_id=instance_id,
            launch_id=self.launch_id,
            payload=_intent_submission_payload(body, admission_evidence),
        )
        status, value = self._submit_v2_intent(envelope.as_dict())
        return _handle(request_id, status, value)

    def submit_order(
        self,
        request: OrderRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        if not self.allow_trading:
            return CommandHandle(
                request_id,
                "rejected",
                error="launch live trading is disabled by safety policy",
            )
        order = _direct_order_body(
            request,
            order_id=request.request_id or request_id,
            default_segment=self.default_segment,
        )
        intent = _single_order_intent(
            order,
            strategy_id=strategy_id,
            instance_id=instance_id,
            launch_id=self.launch_id,
            request_id=request_id,
        )
        status, value = self._submit_v2_intent(
            {
                "command_id": request_id,
                "idempotency_key": request_id,
                "strategy_id": strategy_id,
                "payload": {"intent": intent},
            }
        )
        return _handle(request_id, status, value)

    def cancel_intent(
        self,
        intent_id: str,
        *,
        reason: str,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        return CommandHandle(
            request_id,
            "rejected",
            error="execution_cancel_intent is not part of ExecutionControlRpc",
        )

    def cancel_order(
        self,
        order_id: str,
        *,
        reason: str,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        value = self.client.call("execution_cancel_order", [order_id, {"reason": reason}])
        return _handle(request_id, 202, value)

    def replace_order(
        self,
        order_id: str,
        request: ReplaceOrderRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        body: dict[str, object] = {"reason": request.reason}
        if request.quantity is not None:
            body["quantity"] = _decimal(request.quantity)
        if request.limit_price is not None:
            body["limit_price"] = _decimal(request.limit_price)
        if request.time_in_force is not None:
            body["options"] = {"time_in_force": request.time_in_force.value.upper()}
        value = self.client.call("execution_replace_order", [order_id, body])
        return _handle(request_id, 202, value)

    def cancel_all(
        self,
        *,
        instrument_id: str | None,
        account_id: str | None,
        reason: str,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        return CommandHandle(
            request_id,
            "rejected",
            error="execution bulk cancel is not part of ExecutionControlRpc",
        )

    def pair_arbitrage(
        self,
        request: PairArbitrageRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        admission_evidence: IntentAdmissionEvidence | None = None,
    ) -> CommandHandle:
        if not instance_id.strip():
            return CommandHandle(
                request_id,
                "rejected",
                error="instance_id is required for execution intents",
            )
        if not self.allow_trading:
            return CommandHandle(
                request_id,
                "rejected",
                error="launch live trading is disabled by safety policy",
            )
        intent_id = request.intent_id or f"{strategy_id}:intent:{request_id}"
        legs = [request.first, request.second]
        account_ids = []
        payload_legs = []
        for index, leg in enumerate(legs):
            account_id = leg.account_id
            if account_id not in account_ids:
                account_ids.append(account_id)
            payload_legs.append(
                {
                    "leg_id": f"leg-{index}",
                    "account_id": account_id,
                    "segment_key": leg.segment_key,
                    "instrument_id": leg.instrument_id,
                    "market_id": None,
                    "execution_route_id": leg.execution_route_id,
                    "side": leg.side.capitalize(),
                    "quantity": _decimal(leg.quantity),
                    "limit_price": None
                    if leg.limit_price is None
                    else _decimal(leg.limit_price),
                    "target_position": False,
                    "options": _execution_options(leg.split, leg.maker),
                }
            )
        body = {
            "intent_id": intent_id,
            "strategy_decision_id": request.strategy_decision_id,
            "strategy_id": strategy_id,
            "launch_id": self.launch_id or "",
            "instance_id": instance_id,
            "instrument_id": legs[0].instrument_id,
            "market_id": None,
            "account_ids": account_ids,
            "segment_key": legs[0].segment_key,
            "target_quantity": "0",
            "limit_price": None,
            "source_snapshot_id": None,
            "source_event_sequence": None,
            "reason": request.reason,
            "intent_type": "PairArbitrage",
            "algorithm": _execution_algorithm(request.algorithm),
            "completion_policy": request.completion_policy,
            "failure_policy": request.failure_policy,
            "legs": payload_legs,
            "deadline_unix_nanos": None
            if request.max_wait_nanos is None
            else time.time_ns() + request.max_wait_nanos,
            "min_edge_bps": request.min_edge_bps,
            "max_slippage_bps": request.max_slippage_bps,
            "estimated_fee_bps": request.estimated_fee_bps,
        }
        envelope = CommandEnvelope(
            command_id=request_id,
            operation="execution.submit_intent",
            strategy_id=strategy_id,
            instance_id=instance_id,
            launch_id=self.launch_id,
            payload=_intent_submission_payload(body, admission_evidence),
        )
        status, value = self._submit_v2_intent(envelope.as_dict())
        return _handle(request_id, status, value)

    def option_spread(
        self,
        request: OptionSpreadRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        admission_evidence: IntentAdmissionEvidence | None = None,
    ) -> CommandHandle:
        if not instance_id.strip():
            return CommandHandle(
                request_id,
                "rejected",
                error="instance_id is required for execution intents",
            )
        if not self.allow_trading:
            return CommandHandle(
                request_id,
                "rejected",
                error="launch live trading is disabled by safety policy",
            )
        intent_id = request.intent_id or f"{strategy_id}:intent:{request_id}"
        body = {
            "intent_id": intent_id,
            "strategy_decision_id": request.strategy_decision_id,
            "strategy_id": strategy_id,
            "launch_id": self.launch_id or "",
            "instance_id": instance_id,
            "instrument_id": request.short_leg.instrument_id,
            "market_id": request.short_leg.market_id,
            "execution_route_id": request.short_leg.execution_route_id,
            "account_ids": [request.account_id],
            "segment_key": "options",
            "target_quantity": "0",
            "limit_price": None,
            "source_snapshot_id": request.source_snapshot_id,
            "source_event_sequence": request.source_event_sequence,
            "source_event_time_unix_nanos": request.source_event_time_unix_nanos,
            "reason": request.reason,
            "intent_type": "OptionSpread",
            "algorithm": _execution_algorithm(request.algorithm),
            "completion_policy": request.completion_policy,
            "failure_policy": request.failure_policy,
            "minimum_net_credit": _decimal(request.minimum_net_credit),
            "maximum_loss": _decimal(request.maximum_loss),
            "deadline_unix_nanos": request.deadline_unix_nanos,
            "legs": [
                _option_spread_leg(request.short_leg),
                _option_spread_leg(request.long_leg),
            ],
        }
        envelope = CommandEnvelope(
            command_id=request_id,
            operation="execution.submit_intent",
            strategy_id=strategy_id,
            instance_id=instance_id,
            launch_id=self.launch_id,
            payload=_intent_submission_payload(body, admission_evidence),
        )
        status, value = self._submit_v2_intent(envelope.as_dict())
        return _handle(request_id, status, value)

    def portfolio_rebalance(
        self,
        request: PortfolioRebalanceRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        admission_evidence: IntentAdmissionEvidence | None = None,
    ) -> CommandHandle:
        if not instance_id.strip():
            return CommandHandle(
                request_id,
                "rejected",
                error="instance_id is required for execution intents",
            )
        if not self.allow_trading:
            return CommandHandle(
                request_id,
                "rejected",
                error="launch live trading is disabled by safety policy",
            )
        intent_id = request.intent_id or f"{strategy_id}:intent:{request_id}"
        account_ids = []
        payload_legs = []
        for index, target in enumerate(request.targets):
            account_id = target.account_id
            if account_id not in account_ids:
                account_ids.append(account_id)
            payload_legs.append(
                {
                    "leg_id": f"target-{index}",
                    "account_id": account_id,
                    "segment_key": target.segment_key,
                    "instrument_id": target.instrument_id,
                    "market_id": None,
                    "execution_route_id": target.execution_route_id,
                    "side": "Buy",
                    "quantity": _decimal(target.quantity),
                    "limit_price": None
                    if target.limit_price is None
                    else _decimal(target.limit_price),
                    "target_position": True,
                    "options": _execution_options(target.split, target.maker),
                }
            )
        first = request.targets[0]
        body = {
            "intent_id": intent_id,
            "strategy_decision_id": request.strategy_decision_id,
            "strategy_id": strategy_id,
            "launch_id": self.launch_id or "",
            "instance_id": instance_id,
            "instrument_id": first.instrument_id,
            "market_id": None,
            "account_ids": account_ids,
            "segment_key": first.segment_key,
            "target_quantity": "0",
            "limit_price": None,
            "source_snapshot_id": None,
            "source_event_sequence": None,
            "reason": request.reason,
            "intent_type": "PortfolioRebalance",
            "algorithm": _execution_algorithm(request.algorithm),
            "completion_policy": request.completion_policy,
            "failure_policy": request.failure_policy,
            "legs": payload_legs,
            "deadline_unix_nanos": None,
            "min_edge_bps": None,
            "max_slippage_bps": None,
        }
        envelope = CommandEnvelope(
            command_id=request_id,
            operation="execution.submit_intent",
            strategy_id=strategy_id,
            instance_id=instance_id,
            launch_id=self.launch_id,
            payload=_intent_submission_payload(body, admission_evidence),
        )
        status, value = self._submit_v2_intent(envelope.as_dict())
        return _handle(request_id, status, value)

    def quote_provisioning(
        self,
        request: QuoteProvisioningRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        admission_evidence: IntentAdmissionEvidence | None = None,
    ) -> CommandHandle:
        if not instance_id.strip():
            return CommandHandle(
                request_id,
                "rejected",
                error="instance_id is required for execution intents",
            )
        if not self.allow_trading:
            return CommandHandle(
                request_id,
                "rejected",
                error="launch live trading is disabled by safety policy",
            )
        intent_id = request.intent_id or f"{strategy_id}:intent:{request_id}"
        account_ids = [request.account_id]
        body = {
            "intent_id": intent_id,
            "strategy_decision_id": request.strategy_decision_id,
            "strategy_id": strategy_id,
            "launch_id": self.launch_id or "",
            "instance_id": instance_id,
            "instrument_id": request.instrument_id,
            "market_id": request.market_id,
            "account_ids": account_ids,
            "segment_key": request.segment_key,
            "target_quantity": "0",
            "limit_price": None,
            "source_snapshot_id": None,
            "source_event_sequence": None,
            "reason": request.reason,
            "intent_type": "QuoteProvisioning",
            "algorithm": _execution_algorithm(request.algorithm),
            "completion_policy": "BestEffort",
            "failure_policy": "ContinueOtherLegs",
            "legs": [
                {
                    "leg_id": "bid",
                    "account_id": request.account_id,
                    "segment_key": request.segment_key,
                    "instrument_id": request.instrument_id,
                    "market_id": request.market_id,
                    "execution_route_id": request.execution_route_id,
                    "side": "Buy",
                    "quantity": _decimal(request.bid_quantity),
                    "limit_price": _decimal(request.bid_price),
                    "target_position": False,
                    "options": {
                        **_execution_options(None, request.maker),
                        "post_only": True,
                    },
                },
                {
                    "leg_id": "ask",
                    "account_id": request.account_id,
                    "segment_key": request.segment_key,
                    "instrument_id": request.instrument_id,
                    "market_id": request.market_id,
                    "execution_route_id": request.execution_route_id,
                    "side": "Sell",
                    "quantity": _decimal(request.ask_quantity),
                    "limit_price": _decimal(request.ask_price),
                    "target_position": False,
                    "options": {
                        **_execution_options(None, request.maker),
                        "post_only": True,
                    },
                },
            ],
            "deadline_unix_nanos": None,
            "min_edge_bps": None,
            "max_slippage_bps": None,
        }
        envelope = CommandEnvelope(
            command_id=request_id,
            operation="execution.submit_intent",
            strategy_id=strategy_id,
            instance_id=instance_id,
            launch_id=self.launch_id,
            payload=_intent_submission_payload(body, admission_evidence),
        )
        status, value = self._submit_v2_intent(envelope.as_dict())
        return _handle(request_id, status, value)

    def refresh_quote(
        self,
        request: QuoteRefreshRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        if not instance_id.strip():
            return CommandHandle(
                request_id,
                "rejected",
                error="instance_id is required for quote refresh",
            )
        return CommandHandle(
            request_id,
            "rejected",
            error="execution_refresh_quote is not part of ExecutionControlRpc",
        )


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def _direct_order_body(
    request: OrderRequest, *, order_id: str, default_segment: str
) -> dict[str, object]:
    instrument = (
        request.instrument.id
        if isinstance(request.instrument, InstrumentRef)
        else request.instrument
    )
    return {
        "order_id": order_id,
        "intent_id": None,
        "account_id": str(request.account),
        "segment_key": str(request.segment or default_segment),
        "instrument_id": str(instrument),
        "market_id": None,
        "side": request.side.value.capitalize(),
        "order_type": "Limit" if isinstance(request, LimitOrderRequest) else "Market",
        "quantity": _decimal(request.quantity),
        "limit_price": None
        if isinstance(request, MarketOrderRequest)
        else _decimal(request.limit_price),
        "options": {
            "time_in_force": request.time_in_force.value.upper(),
            "reduce_only": request.reduce_only,
            "post_only": request.post_only
            if isinstance(request, LimitOrderRequest)
            else False,
        },
        "submitted_at_unix_nanos": None,
    }


def _single_order_intent(
    order: Mapping[str, object],
    *,
    strategy_id: str,
    instance_id: str,
    launch_id: str | None,
    request_id: str,
) -> dict[str, object]:
    return {
        "intent_id": f"{strategy_id}:intent:{request_id}",
        "strategy_decision_id": None,
        "strategy_id": strategy_id,
        "launch_id": launch_id or "",
        "instance_id": instance_id,
        "instrument_id": order["instrument_id"],
        "market_id": order.get("market_id"),
        "execution_route_id": order.get("execution_route_id"),
        "account_ids": [order["account_id"]],
        "segment_key": order["segment_key"],
        "target_quantity": "0",
        "limit_price": None,
        "intent_type": "SingleOrder",
        "algorithm": _execution_algorithm(ImmediateAlgorithm()),
        "completion_policy": "AllLegsSatisfied",
        "failure_policy": "CancelRemaining",
        "reason": "",
        "legs": [
            {
                "leg_id": f"{request_id}:leg",
                "account_id": order["account_id"],
                "segment_key": order["segment_key"],
                "instrument_id": order["instrument_id"],
                "market_id": order.get("market_id") or order["instrument_id"],
                "execution_route_id": order.get("execution_route_id"),
                "side": str(order["side"]).capitalize(),
                "quantity": order["quantity"],
                "limit_price": order.get("limit_price"),
                "target_position": False,
                "options": _contract_order_options(order.get("options")),
            }
        ],
    }


def _option_spread_leg(leg: Any) -> dict[str, object]:
    return {
        "leg_id": leg.leg_id,
        "account_id": "",
        "segment_key": "options",
        "instrument_id": leg.instrument_id,
        "market_id": leg.market_id,
        "execution_route_id": leg.execution_route_id,
        "side": leg.side,
        "quantity": _decimal(leg.quantity),
        "limit_price": None if leg.limit_price is None else _decimal(leg.limit_price),
        "target_position": False,
        "options": {},
    }


def _replacement_body(
    original: Mapping[str, object],
    request: ReplaceOrderRequest,
    *,
    request_id: str,
) -> dict[str, object]:
    options: dict[str, object] = {}
    raw_options = original.get("options")
    if isinstance(raw_options, Mapping):
        for key, value in raw_options.items():
            if isinstance(key, str):
                options[key] = value
    if request.time_in_force is not None:
        options["time_in_force"] = request.time_in_force.value.upper()
    return {
        "order_id": request_id,
        "intent_id": original.get("intent_id"),
        "account_id": original.get("account_id"),
        "segment_key": original.get("segment_key", "spot"),
        "instrument_id": original.get("instrument_id"),
        "market_id": original.get("market_id"),
        "side": original.get("side"),
        "order_type": original.get("order_type"),
        "quantity": _decimal(request.quantity)
        if request.quantity is not None
        else original.get("quantity"),
        "limit_price": _decimal(request.limit_price)
        if request.limit_price is not None
        else original.get("limit_price"),
        "options": options,
        "submitted_at_unix_nanos": None,
    }


def _execution_options(
    split: SplitOrderPolicy | None,
    maker: MakerExecutionPolicy | None,
) -> dict[str, object]:
    options: dict[str, object] = {
        "time_in_force": None,
        "reduce_only": None,
        "post_only": None,
        "position_side": None,
        "quote_asset": None,
        "wallet_type": None,
        "trading_session": None,
        "tokenize": None,
        "split": None,
        "maker": None,
    }
    if split is not None:
        options["split"] = {
            "max_child_quantity": None
            if split.max_child_quantity is None
            else _decimal(split.max_child_quantity),
            "child_count": split.child_count,
            "min_child_quantity": None
            if split.min_child_quantity is None
            else _decimal(split.min_child_quantity),
        }
    if maker is not None:
        options["maker"] = {
            "min_interval": None
            if maker.min_interval_millis is None
            else maker.min_interval_millis * 1_000_000,
            "max_orders_per_window": maker.max_orders_per_window,
            "window": None
            if maker.window_millis is None
            else maker.window_millis * 1_000_000,
            "max_inventory_abs": None
            if maker.max_inventory_abs is None
            else _decimal(maker.max_inventory_abs),
            "target_inventory": None
            if maker.target_inventory is None
            else _decimal(maker.target_inventory),
            "max_quote_age": None
            if maker.max_quote_age_millis is None
            else maker.max_quote_age_millis * 1_000_000,
        }
    return options


def _contract_order_options(value: object) -> dict[str, object]:
    options = _execution_options(None, None)
    if value is None:
        return options
    if not isinstance(value, Mapping):
        raise TypeError("Execution order options must be a mapping")
    unknown = set(value) - set(options)
    if unknown:
        raise ValueError(
            "Execution order options contain unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    options.update(value)
    return options


def _intent_submission_payload(
    effective: Mapping[str, object],
    evidence: IntentAdmissionEvidence | None,
) -> dict[str, object]:
    effective_body = _contract_intent_body(effective)
    payload: dict[str, object] = {"intent": effective_body}
    if evidence is None:
        return payload
    original = _contract_intent_body(_original_intent_body(effective_body, evidence))
    original_json = _canonical_control_json(original)
    effective_json = _canonical_control_json(effective_body)
    payload["admission_evidence"] = {
        "source": evidence.source,
        "decision_id": evidence.decision_id,
        "outcome": evidence.outcome,
        "original_intent": original,
        "effective_intent": effective_body,
        "original_hash": hashlib.sha256(original_json).hexdigest(),
        "effective_hash": hashlib.sha256(effective_json).hexdigest(),
    }
    return payload


_INTENT_FIELDS = frozenset(
    {
        "intent_id",
        "strategy_decision_id",
        "strategy_id",
        "launch_id",
        "instance_id",
        "instrument_id",
        "market_id",
        "execution_route_id",
        "account_ids",
        "segment_key",
        "target_quantity",
        "limit_price",
        "source_snapshot_id",
        "source_event_sequence",
        "source_event_time_unix_nanos",
        "reason",
        "intent_type",
        "algorithm",
        "completion_policy",
        "failure_policy",
        "legs",
        "deadline_unix_nanos",
        "min_edge_bps",
        "max_slippage_bps",
        "estimated_fee_bps",
        "minimum_net_credit",
        "maximum_loss",
        "order_options",
    }
)


def _contract_intent_body(value: Mapping[str, object]) -> dict[str, object]:
    unknown = set(value) - _INTENT_FIELDS
    if unknown:
        raise ValueError(
            "Execution Intent contains unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    body = dict(value)
    for required in (
        "intent_id",
        "strategy_id",
        "launch_id",
        "instance_id",
        "instrument_id",
        "account_ids",
        "segment_key",
        "reason",
        "intent_type",
        "algorithm",
    ):
        if required not in body:
            raise ValueError(f"Execution Intent {required} is required")
    defaults: dict[str, object] = {
        "strategy_decision_id": None,
        "market_id": None,
        "execution_route_id": None,
        "target_quantity": "0",
        "limit_price": None,
        "source_snapshot_id": None,
        "source_event_sequence": None,
        "source_event_time_unix_nanos": None,
        "completion_policy": "AllLegsSatisfied",
        "failure_policy": "CancelRemaining",
        "legs": [],
        "deadline_unix_nanos": None,
        "min_edge_bps": None,
        "max_slippage_bps": None,
        "estimated_fee_bps": None,
        "minimum_net_credit": None,
        "maximum_loss": None,
        "order_options": {},
    }
    for field, default in defaults.items():
        body.setdefault(field, default)
    return body


def _original_intent_body(
    effective: Mapping[str, object], evidence: IntentAdmissionEvidence
) -> dict[str, object]:
    original = copy.deepcopy(dict(effective))
    if evidence.outcome == "approved":
        return original
    request = evidence.original_intent
    if isinstance(request, TargetPositionRequest):
        original["target_quantity"] = _decimal(request.quantity)
        original["limit_price"] = (
            None if request.limit_price is None else _decimal(request.limit_price)
        )
        original["order_options"] = _execution_options(request.split, request.maker)
    elif isinstance(request, PairArbitrageRequest):
        original["max_slippage_bps"] = request.max_slippage_bps
    elif isinstance(request, OptionSpreadRequest):
        original["deadline_unix_nanos"] = request.deadline_unix_nanos
    elif not isinstance(request, (PortfolioRebalanceRequest, QuoteProvisioningRequest)):
        raise TypeError("Unsupported original Intent admission evidence type")
    return original


def _canonical_control_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _hedge_policy(policy: HedgePolicy | None) -> dict[str, object] | None:
    if policy is None:
        return None
    return {
        "leader_leg_id": policy.leader_leg_id,
        "hedge_leg_id": policy.hedge_leg_id,
        "ratio": {
            "numerator": policy.ratio_numerator,
            "denominator": policy.ratio_denominator,
        },
        "contract_multiplier": {
            "numerator": policy.contract_multiplier_numerator,
            "denominator": policy.contract_multiplier_denominator,
        },
        "max_unhedged_quantity": _decimal(policy.max_unhedged_quantity),
        "max_unhedged_duration": policy.max_unhedged_duration_nanos,
        "fallback_execution_route_ids": list(policy.fallback_execution_route_ids),
        "compensate_on_failure": policy.compensate_on_failure,
        "max_compensation_attempts": policy.max_compensation_attempts,
    }


def _execution_algorithm(policy: ExecutionAlgorithmPolicy) -> dict[str, object]:
    if isinstance(policy, ImmediateAlgorithm):
        return {"type": "immediate"}
    if isinstance(policy, TwapAlgorithm):
        return {
            "type": "twap",
            "policy": {
                "slice_count": policy.slice_count,
                "slice_interval": policy.slice_interval_nanos,
            },
        }
    if isinstance(policy, MakerTakerHedgeAlgorithm):
        hedge = _hedge_policy(policy.hedge)
        if hedge is None:
            raise ValueError("maker-taker hedge algorithm requires hedge policy")
        return {"type": "maker_taker_hedge", "policy": hedge}
    raise TypeError(f"Unsupported execution algorithm: {type(policy).__name__}")


def _handle(request_id: str, status: int, value: Mapping[str, Any]) -> CommandHandle:
    command_status = "accepted" if 200 <= status < 300 else "rejected"
    raw_error = value.get("error")
    if isinstance(raw_error, Mapping):
        message = str(raw_error.get("message", "command failed"))
        error_code = raw_error.get("code")
        retryable = bool(raw_error.get("retryable", False))
    else:
        message = str(raw_error or "command failed")
        error_code = None
        retryable = False
    return CommandHandle(
        request_id,
        command_status,
        value,
        None if status < 400 else message,
        None if error_code is None else str(error_code),
        retryable,
    )
