from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from kairospy.domain.identity import InstrumentId
from kairospy.domain.intent import HedgeIntent, Intent, TargetExposureIntent, TargetPositionIntent


class IntentStatus(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    PARTIALLY_SATISFIED = "partially_satisfied"
    SATISFIED = "satisfied"
    BLOCKED = "blocked"
    FAILED = "failed"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class IntentScope:
    strategy_id: str
    intent_kind: str
    resource_key: str

    @property
    def key(self) -> str:
        return f"{self.strategy_id}:{self.intent_kind}:{self.resource_key}"


@dataclass(frozen=True, slots=True)
class IntentExecutionView:
    intent_id: UUID
    scope: IntentScope
    status: IntentStatus
    target_quantity: Decimal | None = None
    fulfilled_quantity: Decimal = Decimal("0")
    remaining_quantity: Decimal = Decimal("0")
    working_quantity: Decimal = Decimal("0")
    filled_quantity: Decimal = Decimal("0")
    attempt_count: int = 0
    last_attempt_at: datetime | None = None
    last_error: str | None = None


def intent_scope(intent: Intent) -> IntentScope:
    if isinstance(intent, TargetPositionIntent):
        return IntentScope(intent.strategy_id, "target_position", intent.instrument_id.value)
    if isinstance(intent, TargetExposureIntent):
        return IntentScope(intent.strategy_id, "target_exposure", intent.instrument_id.value)
    if isinstance(intent, HedgeIntent):
        return IntentScope(intent.strategy_id, "hedge", intent.hedge_instrument_id.value)
    return IntentScope(intent.strategy_id, type(intent).__name__, str(intent.intent_id))


class IntentExecutionTracker:
    """In-memory projection of strategy-visible intent execution progress.

    Orders, executions, positions, and Ledger entries remain the durable facts.
    This projection is deliberately rebuildable and contains no venue-specific state.
    """

    def __init__(self, *, quantity_tolerance: Decimal = Decimal("0")) -> None:
        if quantity_tolerance < 0:
            raise ValueError("intent quantity tolerance cannot be negative")
        self.quantity_tolerance = quantity_tolerance
        self._views: dict[str, IntentExecutionView] = {}

    @property
    def views(self) -> tuple[IntentExecutionView, ...]:
        return tuple(self._views[key] for key in sorted(self._views))

    def active(self, scope: IntentScope | str) -> IntentExecutionView | None:
        key = scope if isinstance(scope, str) else scope.key
        value = self._views.get(key)
        if value is None or value.status is IntentStatus.SUPERSEDED:
            return None
        return value

    def execution(self, intent_id: UUID) -> IntentExecutionView | None:
        return next((item for item in self._views.values() if item.intent_id == intent_id), None)

    def publish(self, intent: Intent) -> IntentExecutionView:
        scope = intent_scope(intent)
        current = self._views.get(scope.key)
        target = _target_quantity(intent)
        if current is not None and current.intent_id == intent.intent_id:
            return current
        self._views[scope.key] = IntentExecutionView(
            intent.intent_id, scope, IntentStatus.PENDING, target_quantity=target,
            remaining_quantity=target if target is not None else Decimal("0"),
        )
        return self._views[scope.key]

    def refresh_target(
        self,
        intent: TargetPositionIntent | HedgeIntent,
        *,
        current_quantity: Decimal,
        working_quantity: Decimal = Decimal("0"),
        filled_quantity: Decimal = Decimal("0"),
        attempt_count: int = 0,
        last_attempt_at: datetime | None = None,
        last_error: str | None = None,
    ) -> IntentExecutionView:
        scope = intent_scope(intent)
        current = self._views.get(scope.key)
        if current is None or current.intent_id != intent.intent_id:
            current = self.publish(intent)
        target = _target_quantity(intent)
        assert target is not None
        remaining = target - current_quantity - working_quantity
        if abs(remaining) <= self.quantity_tolerance:
            status = IntentStatus.SATISFIED if working_quantity == 0 else IntentStatus.EXECUTING
        elif last_error is not None:
            status = IntentStatus.BLOCKED
        elif current_quantity != 0 or filled_quantity != 0:
            status = IntentStatus.PARTIALLY_SATISFIED
        elif working_quantity != 0 or attempt_count:
            status = IntentStatus.EXECUTING
        else:
            status = IntentStatus.PENDING
        updated = IntentExecutionView(
            intent.intent_id, scope, status, target, current_quantity, remaining,
            working_quantity, filled_quantity, attempt_count, last_attempt_at, last_error,
        )
        self._views[scope.key] = updated
        return updated

    def mark_satisfied(self, intent: Intent, *, filled_quantity: Decimal = Decimal("0")) -> IntentExecutionView:
        current = self.publish(intent)
        updated = IntentExecutionView(
            current.intent_id, current.scope, IntentStatus.SATISFIED,
            current.target_quantity, current.target_quantity or filled_quantity, Decimal("0"),
            Decimal("0"), filled_quantity, max(1, current.attempt_count), current.last_attempt_at,
        )
        self._views[current.scope.key] = updated
        return updated


def _target_quantity(intent: Intent) -> Decimal | None:
    if isinstance(intent, TargetPositionIntent):
        return intent.target_quantity
    if isinstance(intent, HedgeIntent):
        return intent.target_delta
    return None
