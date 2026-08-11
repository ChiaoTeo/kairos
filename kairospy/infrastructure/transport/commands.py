from __future__ import annotations

from decimal import Decimal
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode

from kairospy.strategy import (
    CommandHandle,
    CommandEnvelope,
    HedgePolicy,
    MakerExecutionPolicy,
    MarketSubscriptionRequest,
    PairArbitrageRequest,
    PortfolioRebalanceRequest,
    QuoteProvisioningRequest,
    QuoteRefreshRequest,
    SplitOrderPolicy,
    TargetPositionRequest,
)
from kairospy.application.strategy.domain.messages import StrategySignal
from ..unix_http import request_sync


class UnixJsonCommandClient:
    """Synchronous, low-frequency JSON-over-Unix command transport."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 30.0) -> None:
        self.socket_path = Path(socket_path)
        self.timeout = timeout

    def request(
        self, method: str, path: str, body: Mapping[str, object] | None = None
    ) -> tuple[int, dict[str, Any]]:
        return request_sync(self.socket_path, method, path, body, timeout=self.timeout)


class MarketUnixCommandPort:
    def __init__(
        self, client: UnixJsonCommandClient, *, launch_id: str | None = None
    ) -> None:
        self.client = client
        self.launch_id = launch_id

    def subscribe(
        self,
        request: MarketSubscriptionRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        envelope = CommandEnvelope(
            command_id=request_id,
            operation="market.subscribe",
            strategy_id=strategy_id,
            instance_id=instance_id,
            launch_id=self.launch_id,
            payload={
                "subject": request.subject,
                "selectors": list(request.selectors),
                "exchange": request.exchange,
                "market_type": request.market_type,
                "asset_type": request.asset_type,
                "identity": request.identity,
                "params": dict(request.params),
                "dynamic": request.dynamic,
            },
        )
        status, value = self.client.request("POST", "/v1/subscribe", envelope.as_dict())
        return _handle(request_id, status, value)

    def unsubscribe(
        self,
        subscription: object,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        if not instance_id.strip():
            return CommandHandle(
                request_id,
                "rejected",
                error="instance_id is required for market commands",
            )
        subscription_id = (
            subscription if isinstance(subscription, str) else str(subscription)
        )
        envelope = CommandEnvelope(
            command_id=request_id,
            operation="market.unsubscribe",
            strategy_id=strategy_id,
            instance_id=instance_id,
            payload={"subscription_id": subscription_id},
        )
        status, value = self.client.request(
            "POST", "/v1/unsubscribe", envelope.as_dict()
        )
        return _handle(request_id, status, value)


class ExecutionIntentCommandPort:
    def __init__(
        self,
        client: UnixJsonCommandClient,
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

    def target_position(
        self,
        request: TargetPositionRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
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
        envelope = CommandEnvelope(
            command_id=request_id,
            operation="execution.submit_intent",
            strategy_id=strategy_id,
            instance_id=instance_id,
            launch_id=self.launch_id,
            payload={
                "intent": {
                    "intent_id": intent_id,
                    "strategy_id": strategy_id,
                    "launch_id": self.launch_id or "",
                    "instance_id": instance_id,
                    "account_ids": account_ids,
                    "segment_key": self.default_segment,
                    "instrument_id": request.instrument_id,
                    "kind": "TargetPosition",
                    "target_quantity_mantissa": _decimal(request.quantity)["mantissa"],
                    "quantity_scale": _decimal(request.quantity)["scale"],
                    "limit_price_mantissa": None
                    if request.limit_price is None
                    else _decimal(request.limit_price)["mantissa"],
                    "limit_price_scale": None
                    if request.limit_price is None
                    else _decimal(request.limit_price)["scale"],
                    "source_snapshot_id": request.source_snapshot_id,
                    "source_event_sequence": request.source_event_sequence,
                    "reason": request.reason,
                    "order_options": _execution_options(request.split, request.maker),
                }
            },
        )
        status, value = self.client.request(
            "POST", "/v1/intents/submit", envelope.as_dict()
        )
        return _handle(request_id, status, value)

    def pair_arbitrage(
        self,
        request: PairArbitrageRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
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
            account_id = leg.account_id or request.account_id
            if account_id not in account_ids:
                account_ids.append(account_id)
            payload_legs.append(
                {
                    "leg_id": f"leg-{index}",
                    "account_id": account_id,
                    "segment_key": leg.segment_key,
                    "instrument_id": leg.instrument_id,
                    "market_id": None,
                    "side": leg.side.capitalize(),
                    "quantity_mantissa": _decimal(leg.quantity)["mantissa"],
                    "quantity_scale": _decimal(leg.quantity)["scale"],
                    "limit_price_mantissa": None
                    if leg.limit_price is None
                    else _decimal(leg.limit_price)["mantissa"],
                    "limit_price_scale": None
                    if leg.limit_price is None
                    else _decimal(leg.limit_price)["scale"],
                    "target_position": False,
                    "options": _execution_options(leg.split, leg.maker),
                }
            )
        body = {
            "intent_id": intent_id,
            "strategy_id": strategy_id,
            "launch_id": self.launch_id or "",
            "instance_id": instance_id,
            "instrument_id": legs[0].instrument_id,
            "market_id": None,
            "account_ids": account_ids,
            "segment_key": legs[0].segment_key,
            "target_quantity_mantissa": 0,
            "quantity_scale": 0,
            "limit_price_mantissa": None,
            "limit_price_scale": None,
            "source_snapshot_id": None,
            "source_event_sequence": None,
            "reason": request.reason,
            "intent_type": "PairArbitrage",
            "completion_policy": request.completion_policy,
            "failure_policy": request.failure_policy,
            "legs": payload_legs,
            "deadline_unix_nanos": None
            if request.max_wait_nanos is None
            else time.time_ns() + request.max_wait_nanos,
            "min_edge_bps": request.min_edge_bps,
            "max_slippage_bps": request.max_slippage_bps,
            "estimated_fee_bps": request.estimated_fee_bps,
            "hedge_policy": _hedge_policy(request.hedge_policy),
        }
        envelope = CommandEnvelope(
            command_id=request_id,
            operation="execution.submit_intent",
            strategy_id=strategy_id,
            instance_id=instance_id,
            launch_id=self.launch_id,
            payload={"intent": body},
        )
        status, value = self.client.request(
            "POST", "/v1/intents/submit", envelope.as_dict()
        )
        return _handle(request_id, status, value)

    def portfolio_rebalance(
        self,
        request: PortfolioRebalanceRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
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
            account_id = target.account_id or request.account_id
            if account_id not in account_ids:
                account_ids.append(account_id)
            payload_legs.append(
                {
                    "leg_id": f"target-{index}",
                    "account_id": account_id,
                    "segment_key": target.segment_key,
                    "instrument_id": target.instrument_id,
                    "market_id": None,
                    "side": "Buy",
                    "quantity_mantissa": _decimal(target.quantity)["mantissa"],
                    "quantity_scale": _decimal(target.quantity)["scale"],
                    "limit_price_mantissa": None
                    if target.limit_price is None
                    else _decimal(target.limit_price)["mantissa"],
                    "limit_price_scale": None
                    if target.limit_price is None
                    else _decimal(target.limit_price)["scale"],
                    "target_position": True,
                    "options": _execution_options(target.split, target.maker),
                }
            )
        first = request.targets[0]
        body = {
            "intent_id": intent_id,
            "strategy_id": strategy_id,
            "launch_id": self.launch_id or "",
            "instance_id": instance_id,
            "instrument_id": first.instrument_id,
            "market_id": None,
            "account_ids": account_ids,
            "segment_key": first.segment_key,
            "target_quantity_mantissa": 0,
            "quantity_scale": 0,
            "limit_price_mantissa": None,
            "limit_price_scale": None,
            "source_snapshot_id": None,
            "source_event_sequence": None,
            "reason": request.reason,
            "intent_type": "PortfolioRebalance",
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
            payload={"intent": body},
        )
        status, value = self.client.request(
            "POST", "/v1/intents/submit", envelope.as_dict()
        )
        return _handle(request_id, status, value)

    def quote_provisioning(
        self,
        request: QuoteProvisioningRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
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
            "strategy_id": strategy_id,
            "launch_id": self.launch_id or "",
            "instance_id": instance_id,
            "instrument_id": request.instrument_id,
            "market_id": request.market_id,
            "account_ids": account_ids,
            "segment_key": request.segment_key,
            "target_quantity_mantissa": 0,
            "quantity_scale": 0,
            "limit_price_mantissa": None,
            "limit_price_scale": None,
            "source_snapshot_id": None,
            "source_event_sequence": None,
            "reason": request.reason,
            "intent_type": "QuoteProvisioning",
            "completion_policy": "BestEffort",
            "failure_policy": "ContinueOtherLegs",
            "legs": [
                {
                    "leg_id": "bid",
                    "account_id": request.account_id,
                    "segment_key": request.segment_key,
                    "instrument_id": request.instrument_id,
                    "market_id": request.market_id,
                    "side": "Buy",
                    "quantity_mantissa": _decimal(request.bid_quantity)["mantissa"],
                    "quantity_scale": _decimal(request.bid_quantity)["scale"],
                    "limit_price_mantissa": _decimal(request.bid_price)["mantissa"],
                    "limit_price_scale": _decimal(request.bid_price)["scale"],
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
                    "side": "Sell",
                    "quantity_mantissa": _decimal(request.ask_quantity)["mantissa"],
                    "quantity_scale": _decimal(request.ask_quantity)["scale"],
                    "limit_price_mantissa": _decimal(request.ask_price)["mantissa"],
                    "limit_price_scale": _decimal(request.ask_price)["scale"],
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
            payload={"intent": body},
        )
        status, value = self.client.request(
            "POST", "/v1/intents/submit", envelope.as_dict()
        )
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
        envelope = CommandEnvelope(
            command_id=request_id,
            operation="execution.refresh_quote",
            strategy_id=strategy_id,
            instance_id=instance_id,
            launch_id=self.launch_id,
            payload={
                "intent_id": request.intent_id,
                "bid_price_mantissa": _decimal(request.bid_price)["mantissa"],
                "bid_price_scale": _decimal(request.bid_price)["scale"],
                "ask_price_mantissa": _decimal(request.ask_price)["mantissa"],
                "ask_price_scale": _decimal(request.ask_price)["scale"],
                "quote_observed_at_unix_nanos": request.quote_observed_at_unix_nanos,
                "reason": request.reason,
            },
        )
        status, value = self.client.request(
            "POST", "/v1/intents/refresh-quote", envelope.as_dict()
        )
        return _handle(request_id, status, value)

    def publish(self, signal: StrategySignal) -> CommandHandle:
        if not isinstance(signal.intent, TargetPositionRequest):
            return CommandHandle(
                f"{signal.strategy_id}:signal:unsupported",
                "rejected",
                error="live intent port requires TargetPositionRequest",
            )
        request_id = f"{signal.strategy_id}:signal:{signal.source_sequence or 0}"
        return self.target_position(
            signal.intent,
            strategy_id=signal.strategy_id,
            instance_id=signal.instance_id,
            request_id=request_id,
        )


class ExecutionIntentQueryPort:
    """Read-only strategy/system facade for Execution-owned intent state."""

    def __init__(self, client: UnixJsonCommandClient) -> None:
        self.client = client

    def get_intent(self, intent_id: str) -> dict[str, Any]:
        status, value = self.client.request(
            "GET", f"/v1/intent?{urlencode({'intent_id': intent_id})}"
        )
        if status >= 400:
            raise RuntimeError(str(value.get("error", "intent query failed")))
        return value

    def list_intents(self) -> list[dict[str, Any]]:
        status, value = self.client.request("GET", "/v1/intents")
        if status >= 400:
            raise RuntimeError(str(value.get("error", "intent query failed")))
        return list(value.get("intents", []))

    def intent_events(
        self,
        intent_id: str | None = None,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        path = "/v1/intent-events"
        params: dict[str, str | int] = {"after_sequence": after_sequence}
        if intent_id is not None:
            params["intent_id"] = intent_id
        if limit is not None:
            params["limit"] = limit
        path = f"{path}?{urlencode(params)}"
        status, value = self.client.request("GET", path)
        if status >= 400:
            raise RuntimeError(str(value.get("error", "intent event query failed")))
        return list(value.get("events", []))

    def hedge_requirement(self, intent_id: str) -> dict[str, Any] | None:
        status, value = self.client.request(
            "GET", f"/v1/intent-hedge?{urlencode({'intent_id': intent_id})}"
        )
        if status >= 400:
            raise RuntimeError(
                str(value.get("error", "hedge requirement query failed"))
            )
        return value


def _decimal(value: Decimal) -> dict[str, int]:
    value = value.normalize()
    exponent = value.as_tuple().exponent
    scale = max(0, -exponent) if isinstance(exponent, int) else 0
    mantissa = int(value * (10**scale))
    return {"mantissa": mantissa, "scale": scale}


def _execution_options(
    split: SplitOrderPolicy | None,
    maker: MakerExecutionPolicy | None,
) -> dict[str, object]:
    options: dict[str, object] = {}
    if split is not None:
        options["split"] = {
            "max_child_quantity_mantissa": None
            if split.max_child_quantity is None
            else _decimal(split.max_child_quantity)["mantissa"],
            "child_count": split.child_count,
            "min_child_quantity_mantissa": None
            if split.min_child_quantity is None
            else _decimal(split.min_child_quantity)["mantissa"],
            "interval_millis": split.interval_millis,
        }
    if maker is not None:
        options["maker"] = {
            "min_interval_millis": maker.min_interval_millis,
            "max_orders_per_window": maker.max_orders_per_window,
            "window_millis": maker.window_millis,
            "max_inventory_abs_mantissa": None
            if maker.max_inventory_abs is None
            else _decimal(maker.max_inventory_abs)["mantissa"],
            "target_inventory_mantissa": None
            if maker.target_inventory is None
            else _decimal(maker.target_inventory)["mantissa"],
            "max_quote_age_millis": maker.max_quote_age_millis,
        }
    return options


def _hedge_policy(policy: HedgePolicy | None) -> dict[str, object] | None:
    if policy is None:
        return None
    return {
        "leader_leg_id": policy.leader_leg_id,
        "hedge_leg_id": policy.hedge_leg_id,
        "ratio_numerator": policy.ratio_numerator,
        "ratio_denominator": policy.ratio_denominator,
        "contract_multiplier_numerator": policy.contract_multiplier_numerator,
        "contract_multiplier_denominator": policy.contract_multiplier_denominator,
        "max_unhedged_quantity_mantissa": _decimal(policy.max_unhedged_quantity)[
            "mantissa"
        ],
        "compensate_on_failure": policy.compensate_on_failure,
        "max_compensation_attempts": policy.max_compensation_attempts,
    }


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
