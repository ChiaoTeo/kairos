from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from kairospy.ports import ComboOrderRequest, OrderRequest
from kairospy.trading.capability import ExecutionCapabilities


class LeggingPolicy(StrEnum):
    PROHIBIT = "prohibit"
    SEQUENTIAL = "sequential"


@dataclass(frozen=True, slots=True)
class NativeComboPlan:
    request: ComboOrderRequest


@dataclass(frozen=True, slots=True)
class SequentialLegPlan:
    requests: tuple[OrderRequest, ...]
    maximum_naked_legs: int


def plan_combo(request: ComboOrderRequest, capabilities: ExecutionCapabilities, *, legging_policy: LeggingPolicy = LeggingPolicy.PROHIBIT, maximum_naked_legs: int = 0):
    if capabilities.supports_combo_orders:
        return NativeComboPlan(request)
    if legging_policy is LeggingPolicy.PROHIBIT:
        raise ValueError("venue lacks native combo support and silent legging is prohibited")
    if maximum_naked_legs < 1:
        raise ValueError("sequential legging requires an explicit naked-leg limit")
    requests = tuple(
        OrderRequest(
            f"{request.internal_order_id}:leg:{index}", f"{request.client_order_id}-L{index}",
            request.strategy_id, request.intent_id, request.correlation_id, request.account,
            leg.instrument_id, leg.side, request.quantity * leg.ratio, request.instructions,
        )
        for index, leg in enumerate(request.legs, 1)
    )
    return SequentialLegPlan(requests, maximum_naked_legs)
