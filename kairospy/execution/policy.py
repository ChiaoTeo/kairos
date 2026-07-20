from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from kairospy.domain.capability import TimeInForce

from .planner import LeggingPolicy


class ExecutionMode(StrEnum):
    MAKER = "maker"
    TAKER = "taker"
    HYBRID = "hybrid"


class PartialFillPolicy(StrEnum):
    WAIT = "wait"
    CANCEL_REMAINDER = "cancel_remainder"
    CROSS_REMAINDER = "cross_remainder"
    HEDGE_IMMEDIATELY = "hedge_immediately"


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    policy_id: str
    version: str
    mode: ExecutionMode
    time_in_force: TimeInForce
    maximum_slippage_bps: Decimal
    maker_timeout_ms: int | None = None
    order_latency_ms: int = 0
    cancel_latency_ms: int = 0
    queue_model: str | None = None
    slippage_model: str = "top_of_book"
    partial_fill_policy: PartialFillPolicy = PartialFillPolicy.CANCEL_REMAINDER
    legging_policy: LeggingPolicy = LeggingPolicy.PROHIBIT
    maximum_naked_legs: int = 0
    fee_schedule: str = "unspecified"

    def __post_init__(self) -> None:
        if not self.policy_id or not self.version:
            raise ValueError("execution policy identity and version are required")
        if self.maximum_slippage_bps < 0 or min(self.order_latency_ms, self.cancel_latency_ms) < 0:
            raise ValueError("slippage and latency values cannot be negative")
        if self.mode in (ExecutionMode.MAKER, ExecutionMode.HYBRID):
            if self.maker_timeout_ms is None or self.maker_timeout_ms <= 0 or not self.queue_model:
                raise ValueError("maker and hybrid policies require timeout and queue model")
        if self.mode is ExecutionMode.TAKER and self.maker_timeout_ms is not None:
            raise ValueError("taker policy cannot specify maker timeout")
        if self.legging_policy is LeggingPolicy.SEQUENTIAL and self.maximum_naked_legs < 1:
            raise ValueError("sequential legging requires a positive naked-leg limit")
        if self.legging_policy is LeggingPolicy.PROHIBIT and self.maximum_naked_legs:
            raise ValueError("naked-leg limit is invalid when legging is prohibited")

    @property
    def required_data_capabilities(self) -> tuple[str, ...]:
        values = ["synchronous_quotes", "top_of_book", "quote_size"]
        if self.mode in (ExecutionMode.MAKER, ExecutionMode.HYBRID):
            values += ["incremental_order_book", "sequence_numbers", "trade_events", "queue_reconstructable"]
        if self.slippage_model == "order_book_walk":
            values.append("multi_level_order_book")
        return tuple(values)
