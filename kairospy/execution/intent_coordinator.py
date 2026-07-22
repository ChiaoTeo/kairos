from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Sequence
from uuid import UUID

from kairospy.strategy.contracts import EconomicIntent
from kairospy.strategy.intents import HedgeIntent, Intent, TargetPositionIntent
from kairospy.strategy.protocols import Context
from kairospy.strategy.runtime import GovernedStrategyRuntime

from .intent_status import IntentExecutionTracker, IntentExecutionView


@dataclass(slots=True)
class IntentCoordinator:
    """Owns the lifecycle boundary after a strategy emits intents.

    Strategy code only returns Intent objects. The coordinator publishes new
    intents into a strategy-visible progress projection and wraps them into the
    governed EconomicIntent contract for downstream risk/execution stages.
    """

    strategy_runtime: GovernedStrategyRuntime | None = None
    tracker: IntentExecutionTracker = field(default_factory=IntentExecutionTracker)
    _published_intent_ids: set[UUID] = field(default_factory=set, init=False)
    _published_decision_ids: set[UUID] = field(default_factory=set, init=False)

    @property
    def views(self) -> tuple[IntentExecutionView, ...]:
        return self.tracker.views

    def intent_view(
        self,
        *,
        orders: tuple[object, ...] = (),
        outbox_records: tuple[object, ...] = (),
        execution_records: tuple[object, ...] = (),
    ):
        from kairospy.strategy.views import IntentView

        return IntentView.from_executions(
            self.views,
            orders=orders,
            outbox_records=outbox_records,
            execution_records=execution_records,
        )

    def publish(
        self,
        intents: Sequence[Intent],
        context: Context,
        *,
        approved_equity: Decimal | None = None,
    ) -> EconomicIntent | None:
        if self.strategy_runtime is None:
            raise ValueError("publishing economic intents requires a strategy runtime")
        new_intents = tuple(
            intent for intent in intents
            if intent.intent_id not in self._published_intent_ids
        )
        if not new_intents:
            return None
        economic_intent = self.strategy_runtime.wrap_intents(
            new_intents,
            context,
            approved_equity=approved_equity,
        )
        if economic_intent is None or economic_intent.decision_id in self._published_decision_ids:
            return None
        self._published_decision_ids.add(economic_intent.decision_id)
        for intent in economic_intent.intents:
            self.tracker.publish(intent)
            self._published_intent_ids.add(intent.intent_id)
        return economic_intent

    def publish_progress(self, intents: Sequence[Intent]) -> tuple[IntentExecutionView, ...]:
        views = []
        for intent in intents:
            if intent.intent_id in self._published_intent_ids:
                current = self.tracker.execution(intent.intent_id)
                if current is not None:
                    views.append(current)
                continue
            views.append(self.tracker.publish(intent))
            self._published_intent_ids.add(intent.intent_id)
        return tuple(views)

    def execution(self, intent_id: UUID) -> IntentExecutionView | None:
        return self.tracker.execution(intent_id)

    def mark_satisfied(
        self,
        intent: Intent,
        *,
        filled_quantity: Decimal = Decimal("0"),
    ) -> IntentExecutionView:
        self._published_intent_ids.add(intent.intent_id)
        return self.tracker.mark_satisfied(intent, filled_quantity=filled_quantity)

    def mark_blocked(self, intent: Intent, *, reason: str) -> IntentExecutionView:
        self._published_intent_ids.add(intent.intent_id)
        return self.tracker.mark_blocked(intent, reason=reason)

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
        self._published_intent_ids.add(intent.intent_id)
        return self.tracker.refresh_target(
            intent,
            current_quantity=current_quantity,
            working_quantity=working_quantity,
            filled_quantity=filled_quantity,
            attempt_count=attempt_count,
            last_attempt_at=last_attempt_at,
            last_error=last_error,
        )
