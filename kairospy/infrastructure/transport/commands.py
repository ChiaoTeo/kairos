from __future__ import annotations

import json
from decimal import Decimal
import socket
import time
from pathlib import Path
from typing import Any, Mapping

from kairospy.strategy import (
    CommandHandle,
    CommandEnvelope,
    MarketSubscriptionRequest,
    TargetPositionRequest,
)
from kairospy.application.strategy.domain.messages import StrategySignal


class UnixJsonCommandClient:
    """Synchronous, low-frequency JSON-over-Unix command transport."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 30.0) -> None:
        self.socket_path = Path(socket_path)
        self.timeout = timeout

    def request(self, method: str, path: str, body: Mapping[str, object] | None = None) -> tuple[int, dict[str, Any]]:
        payload = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
        request = (
            f"{method} {path} HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Connection: close\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n\r\n"
        ).encode() + payload
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self.timeout)
            connection.connect(str(self.socket_path))
            connection.sendall(request)
            response = bytearray()
            while True:
                chunk = connection.recv(64 * 1024)
                if not chunk:
                    break
                response.extend(chunk)
        head, raw_body = bytes(response).split(b"\r\n\r\n", 1)
        status = int(head.splitlines()[0].split()[1])
        value = json.loads(raw_body or b"{}")
        if not isinstance(value, dict):
            raise ValueError("command response must be a JSON object")
        return status, value


class MarketUnixCommandPort:
    def __init__(self, client: UnixJsonCommandClient, *, launch_id: str | None = None) -> None:
        self.client = client
        self.launch_id = launch_id

    def subscribe(self, request: MarketSubscriptionRequest, *, strategy_id: str, instance_id: str, request_id: str) -> CommandHandle:
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
                "dynamic": request.dynamic,
            },
        )
        status, value = self.client.request("POST", "/v1/subscribe", envelope.as_dict())
        return _handle(request_id, status, value)

    def unsubscribe(self, subscription: object, *, strategy_id: str, instance_id: str, request_id: str) -> CommandHandle:
        if not instance_id.strip():
            return CommandHandle(request_id, "rejected", error="instance_id is required for market commands")
        subscription_id = subscription if isinstance(subscription, str) else str(subscription)
        envelope = CommandEnvelope(
            command_id=request_id,
            operation="market.unsubscribe",
            strategy_id=strategy_id,
            instance_id=instance_id,
            payload={"subscription_id": subscription_id},
        )
        status, value = self.client.request("POST", "/v1/unsubscribe", envelope.as_dict())
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

    def target_position(self, request: TargetPositionRequest, *, strategy_id: str, instance_id: str, request_id: str) -> CommandHandle:
        if not instance_id.strip():
            return CommandHandle(request_id, "rejected", error="instance_id is required for execution intents")
        intent_id = request.intent_id or f"{strategy_id}:intent:{request_id}"
        if not self.allow_trading:
            return CommandHandle(request_id, "rejected", error="launch live trading is disabled by safety policy")
        if self.require_limit_orders and request.limit_price is None:
            return CommandHandle(request_id, "rejected", error="launch safety policy requires limit orders")
        if self.max_order_notional is not None and request.limit_price is not None:
            if abs(request.quantity * request.limit_price) > self.max_order_notional:
                return CommandHandle(request_id, "rejected", error="intent exceeds launch max_order_notional")
        account_ids = list(request.account_ids) or ([request.account_id] if request.account_id else ["main"])
        envelope = CommandEnvelope(
            command_id=request_id,
            operation="execution.submit_intent",
            strategy_id=strategy_id,
            instance_id=instance_id,
            launch_id=self.launch_id,
            payload={"intent": {
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
                "limit_price_mantissa": None if request.limit_price is None else _decimal(request.limit_price)["mantissa"],
                "limit_price_scale": None if request.limit_price is None else _decimal(request.limit_price)["scale"],
                "source_snapshot_id": request.source_snapshot_id,
                "source_event_sequence": request.source_event_sequence,
                "reason": request.reason,
            }},
        )
        status, value = self.client.request("POST", "/v1/intents/submit", envelope.as_dict())
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


def _decimal(value: Decimal) -> dict[str, int]:
    value = value.normalize()
    exponent = value.as_tuple().exponent
    scale = max(0, -exponent) if isinstance(exponent, int) else 0
    mantissa = int(value * (10 ** scale))
    return {"mantissa": mantissa, "scale": scale}


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
