from __future__ import annotations

from ..domain.messages import (
    CommandHandle,
    ContextRequest,
    MarketSubscriptionRequest,
    StrategySignal,
    TargetPositionRequest,
)
from ..protocol import ContextBus, IntentCommandPort, MarketCommandPort


class StrategyContextBus(ContextBus):
    """Composition adapter routing strategy requests to owner applications."""

    def __init__(self, *, market: MarketCommandPort, intents: IntentCommandPort) -> None:
        self._market = market
        self._intents = intents
        self._handles: dict[str, CommandHandle] = {}

    def submit(self, request: ContextRequest) -> CommandHandle:
        try:
            handle = self._submit(request)
        except (OSError, TimeoutError, RuntimeError, TypeError, ValueError) as error:
            handle = CommandHandle(request.request_id, "rejected", error=str(error))
        self._handles[request.request_id] = handle
        return handle

    def publish_signal(self, signal: StrategySignal) -> CommandHandle:
        handle = self._intents.publish(signal)
        self._handles[handle.request_id] = handle
        return handle

    def status(self, request_id: str) -> CommandHandle:
        return self._handles.get(
            request_id,
            CommandHandle(request_id, "missing", error="request not found"),
        )

    def _submit(self, request: ContextRequest) -> CommandHandle:
        if request.operation == "market.subscribe":
            if not isinstance(request.payload, MarketSubscriptionRequest):
                raise TypeError("market.subscribe requires MarketSubscriptionRequest")
            return self._market.subscribe(
                request.payload,
                strategy_id=request.strategy_id,
                instance_id=request.instance_id,
                request_id=request.request_id,
            )
        if request.operation == "market.unsubscribe":
            return self._market.unsubscribe(
                request.payload,
                strategy_id=request.strategy_id,
                request_id=request.request_id,
            )
        if request.operation == "intent.target_position":
            if not isinstance(request.payload, TargetPositionRequest):
                raise TypeError("intent.target_position requires TargetPositionRequest")
            return self._intents.target_position(
                request.payload,
                strategy_id=request.strategy_id,
                request_id=request.request_id,
            )
        raise ValueError(f"unsupported strategy operation: {request.operation}")
