from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
from types import MappingProxyType
from typing import Literal, Mapping

from kairospy.application.execution import (
    ExecutionIntent,
    Fill,
    FillEvent,
    IntentStatus,
    IntentUpdateEvent,
)
from kairospy.application.notification import (
    NotificationApplication,
    NotificationSeverity,
)
from kairospy.application.events import EventMetadata
from kairospy.strategy.clock import StrategyClock, TimerEvent, parse_duration

from ..services.decision_journal import StrategyDecisionJournal


def _items(value: object, name: str) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"decision journal {name} must be an array")
    return tuple(value)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"decision journal {name} must be an integer")
    return value


class DecisionLifecycle(StrEnum):
    RECORDED = "recorded"
    NOT_SUBMITTED = "not_submitted"
    SUBMITTED = "submitted"
    EXECUTION_COMPLETED = "execution_completed"
    EVALUATION_PENDING = "evaluation_pending"
    EVALUATED = "evaluated"
    ABANDONED = "abandoned"


EffectOutcome = Literal["favorable", "neutral", "adverse", "unavailable"]


@dataclass(frozen=True, slots=True)
class EffectEvidence:
    owner: str
    reference_id: str
    event_sequence: int | None = None
    observed_at_unix_nanos: int | None = None
    value: str | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        if not self.owner.strip() or not self.reference_id.strip():
            raise ValueError("effect evidence owner and reference_id are required")
        if self.event_sequence is not None and self.event_sequence <= 0:
            raise ValueError("effect evidence sequence must be positive")
        if self.observed_at_unix_nanos is not None and self.observed_at_unix_nanos < 0:
            raise ValueError("effect evidence observed time cannot be negative")


def _evidence_from_mapping(value: Mapping[str, object]) -> EffectEvidence:
    event_sequence = value.get("event_sequence")
    observed_at = value.get("observed_at_unix_nanos")
    return EffectEvidence(
        owner=str(value["owner"]),
        reference_id=str(value["reference_id"]),
        event_sequence=None
        if event_sequence is None
        else _integer(event_sequence, "evidence event_sequence"),
        observed_at_unix_nanos=None
        if observed_at is None
        else _integer(observed_at, "evidence observed_at_unix_nanos"),
        value=None if value.get("value") is None else str(value["value"]),
        unit=None if value.get("unit") is None else str(value["unit"]),
    )


@dataclass(frozen=True, slots=True)
class DecisionHorizon:
    name: str
    delay: timedelta | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("decision horizon name is required")
        if self.delay is not None and self.delay <= timedelta(0):
            raise ValueError("decision horizon delay must be positive")

    @classmethod
    def after(
        cls, name: str, delay: str | int | float | timedelta
    ) -> "DecisionHorizon":
        return cls(name, parse_duration(delay))


@dataclass(frozen=True, slots=True)
class DecisionEffectEvaluation:
    evaluation_id: str
    strategy_decision_id: str
    intent_ids: tuple[str, ...]
    horizon: str
    revision: int
    outcome: EffectOutcome
    evaluated_at_unix_nanos: int
    summary: str
    evidence: tuple[EffectEvidence, ...]


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    strategy_decision_id: str
    strategy_id: str
    lifecycle: DecisionLifecycle
    reason: str
    expected_outcome: str
    evidence: tuple[EffectEvidence, ...]
    expected_intent_count: int
    intent_ids: tuple[str, ...]
    intent_statuses: Mapping[str, IntentStatus]
    horizons: tuple[DecisionHorizon, ...]
    due_horizons: tuple[str, ...]
    evaluations: tuple[DecisionEffectEvaluation, ...]
    recorded_at_unix_nanos: int


@dataclass(slots=True)
class _DecisionState:
    decision_id: str
    strategy_id: str
    lifecycle: DecisionLifecycle
    reason: str
    expected_outcome: str
    evidence: tuple[EffectEvidence, ...]
    expected_intent_count: int
    horizons: tuple[DecisionHorizon, ...]
    recorded_at_unix_nanos: int
    intent_ids: list[str] = field(default_factory=list)
    intent_statuses: dict[str, IntentStatus] = field(default_factory=dict)
    due_horizons: set[str] = field(default_factory=set)
    due_at_unix_nanos: dict[str, int] = field(default_factory=dict)
    evaluations: list[DecisionEffectEvaluation] = field(default_factory=list)
    observed_fill_ids: set[str] = field(default_factory=set)


_TERMINAL_INTENT_STATUSES = frozenset(
    {
        IntentStatus.SATISFIED,
        IntentStatus.COMPLETED,
        IntentStatus.REJECTED,
        IntentStatus.CANCELED,
        IntentStatus.EXPIRED,
        IntentStatus.FAILED,
        IntentStatus.RECONCILIATION_REQUIRED,
    }
)


class StrategyDecisionApplication:
    """Strategy-owned decision progress, effect evaluation, and notifications."""

    _TIMER_PREFIX = "strategy-decision-effect:"

    def __init__(
        self,
        *,
        strategy_id: str,
        instance_id: str,
        journal: StrategyDecisionJournal,
        notifications: NotificationApplication,
        clock: StrategyClock,
        notification_routes: tuple[str, ...] = (),
    ) -> None:
        if not strategy_id.strip() or not instance_id.strip():
            raise ValueError("strategy_id and instance_id are required")
        self.strategy_id = strategy_id
        self.instance_id = instance_id
        self._journal = journal
        self._notifications = notifications
        self._clock = clock
        self._notification_routes = tuple(dict.fromkeys(notification_routes))
        self._states: dict[str, _DecisionState] = {}
        self._timer_bindings: dict[str, tuple[str, str]] = {}
        self._sequence = 0
        self._unattributed_intents = 0
        self._lifecycle_sequence_gaps = 0
        self._restore()

    def record(
        self,
        *,
        reason: str,
        expected_outcome: str,
        evidence: tuple[EffectEvidence, ...] = (),
        expected_intent_count: int = 1,
        horizons: tuple[DecisionHorizon, ...] = (DecisionHorizon("execution-final"),),
        strategy_decision_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> StrategyDecision:
        reason = reason.strip()
        expected_outcome = expected_outcome.strip()
        if not reason or not expected_outcome:
            raise ValueError("decision reason and expected_outcome are required")
        if expected_intent_count < 0:
            raise ValueError("expected_intent_count cannot be negative")
        if not horizons:
            raise ValueError("decision requires at least one evaluation horizon")
        names = [horizon.name for horizon in horizons]
        if len(names) != len(set(names)):
            raise ValueError("decision horizon names must be unique")
        at = self._event_time(occurred_at)
        decision_id = strategy_decision_id or self._next_decision_id(at)
        if not decision_id.strip():
            raise ValueError("strategy_decision_id is required")
        existing = self._states.get(decision_id)
        if existing is not None:
            if (
                existing.reason != reason
                or existing.expected_outcome != expected_outcome
                or existing.evidence != evidence
                or existing.expected_intent_count != expected_intent_count
                or existing.horizons != horizons
            ):
                raise ValueError("strategy decision idempotent replay changed content")
            return self._snapshot(existing)
        state = _DecisionState(
            decision_id,
            self.strategy_id,
            DecisionLifecycle.RECORDED,
            reason,
            expected_outcome,
            tuple(evidence),
            expected_intent_count,
            horizons,
            self._unix_nanos(at),
        )
        self._states[decision_id] = state
        self._append(
            "decision_recorded",
            state,
            reason=reason,
            expected_outcome=expected_outcome,
            evidence=[self._evidence_dict(value) for value in evidence],
            expected_intent_count=expected_intent_count,
            horizons=[
                {
                    "name": horizon.name,
                    "delay_nanos": None
                    if horizon.delay is None
                    else int(horizon.delay.total_seconds() * 1_000_000_000),
                }
                for horizon in horizons
            ],
            occurred_at_unix_nanos=self._unix_nanos(at),
        )
        self._notify(
            title="Strategy decision recorded",
            body=f"{reason}\nExpected: {expected_outcome}",
            severity="info",
            dedupe_key=f"decision:{decision_id}:recorded",
            attributes={"strategy_decision_id": decision_id, "lifecycle": "recorded"},
        )
        if expected_intent_count == 0:
            self.not_submitted(
                decision_id, reason="decision intentionally produces no Intent"
            )
        return self._snapshot(state)

    def attach_intent(
        self, strategy_decision_id: str, intent_id: str
    ) -> StrategyDecision:
        state = self._require(strategy_decision_id)
        intent_id = intent_id.strip()
        if not intent_id:
            raise ValueError("intent_id is required")
        if state.expected_intent_count == 0:
            raise ValueError("not-submitted decision cannot attach an Intent")
        if intent_id not in state.intent_ids:
            if len(state.intent_ids) >= state.expected_intent_count:
                raise ValueError("decision already has its expected number of Intents")
            state.intent_ids.append(intent_id)
            state.lifecycle = DecisionLifecycle.SUBMITTED
            self._append("intent_attached", state, intent_id=intent_id)
        return self._snapshot(state)

    def not_submitted(
        self, strategy_decision_id: str, *, reason: str
    ) -> StrategyDecision:
        state = self._require(strategy_decision_id)
        if state.intent_ids:
            raise ValueError("submitted decision cannot become not_submitted")
        state.lifecycle = DecisionLifecycle.NOT_SUBMITTED
        self._append("decision_not_submitted", state, reason=reason)
        return self._snapshot(state)

    def abandon(self, strategy_decision_id: str, *, reason: str) -> StrategyDecision:
        state = self._require(strategy_decision_id)
        state.lifecycle = DecisionLifecycle.ABANDONED
        self._append("decision_abandoned", state, reason=reason)
        return self._snapshot(state)

    def observe_execution(
        self, event: IntentUpdateEvent | FillEvent, *, replay: bool = False
    ) -> StrategyDecision | None:
        if isinstance(event, FillEvent):
            return self._observe_fill(event)
        intent = event.data
        decision_id = intent.strategy_decision_id
        if decision_id is None:
            self._unattributed_intents += 1
            return None
        state = self._states.get(decision_id)
        if state is None:
            raise ValueError(
                f"Execution Intent {intent.id} references unknown Strategy decision {decision_id}"
            )
        self.attach_intent(decision_id, str(intent.id))
        previous = state.intent_statuses.get(str(intent.id))
        if previous == intent.status:
            self._complete_execution_if_ready(state, event, replay=replay)
            return self._snapshot(state)
        if (
            previous is not None
            and event.previous_status is not None
            and event.previous_status is not previous
        ):
            self._lifecycle_sequence_gaps += 1
            raise ValueError(
                f"Intent {intent.id} lifecycle is not contiguous: "
                f"expected previous={previous.value}, "
                f"received previous={event.previous_status.value}"
            )
        state.intent_statuses[str(intent.id)] = intent.status
        self._append(
            "intent_status_observed",
            state,
            intent_id=str(intent.id),
            previous_status=None if previous is None else previous.value,
            status=intent.status.value,
            order_ids=[str(value) for value in intent.order_ids],
            reason=intent.reason,
            source_event_sequence=event.metadata.sequence,
            occurred_at_unix_nanos=event.metadata.occurred_at_unix_nanos,
            recovered_from_snapshot=replay,
        )
        if not replay:
            self._notify_intent(event)
        self._complete_execution_if_ready(state, event, replay=replay)
        return self._snapshot(state)

    def reconcile_execution_snapshot(
        self, intent: ExecutionIntent, *, source_event_sequence: int
    ) -> StrategyDecision | None:
        occurred_nanos = intent.updated_at_unix_nanos
        occurred_at = (
            None
            if occurred_nanos is None
            else datetime.fromtimestamp(occurred_nanos / 1_000_000_000, tz=timezone.utc)
        )
        return self.observe_execution(
            IntentUpdateEvent(
                intent,
                EventMetadata(
                    "execution.events",
                    source_event_sequence,
                    producer="execution.snapshot-recovery",
                    occurred_at=occurred_at,
                    occurred_at_unix_nanos=occurred_nanos,
                ),
            ),
            replay=True,
        )

    def reconcile_execution_fill(
        self, fill: Fill, *, source_event_sequence: int
    ) -> StrategyDecision | None:
        occurred_nanos = self._unix_nanos(fill.occurred_at)
        return self.observe_execution(
            FillEvent(
                fill,
                EventMetadata(
                    "execution.events",
                    source_event_sequence,
                    producer="execution.snapshot-recovery",
                    occurred_at=fill.occurred_at,
                    occurred_at_unix_nanos=occurred_nanos,
                ),
            ),
            replay=True,
        )

    def _observe_fill(self, event: FillEvent) -> StrategyDecision | None:
        fill = event.data
        if fill.intent_id is None:
            return None
        intent_id = str(fill.intent_id)
        state = next(
            (value for value in self._states.values() if intent_id in value.intent_ids),
            None,
        )
        if state is None or str(fill.id) in state.observed_fill_ids:
            return None
        state.observed_fill_ids.add(str(fill.id))
        revised_horizons = {value.horizon for value in state.evaluations}
        if not revised_horizons:
            self._append(
                "fill_observed",
                state,
                fill_id=str(fill.id),
                intent_id=intent_id,
                order_id=str(fill.order_id),
                instrument_id=str(fill.instrument.id),
                quantity=str(fill.quantity),
                price=str(fill.price),
                occurred_at=fill.occurred_at.isoformat(),
                source_event_sequence=event.metadata.sequence,
            )
            return self._snapshot(state)
        occurred_at = event.metadata.occurred_at_unix_nanos or self._unix_nanos(
            fill.occurred_at
        )
        for horizon in revised_horizons:
            state.due_horizons.add(horizon)
            state.due_at_unix_nanos[horizon] = occurred_at
        state.lifecycle = DecisionLifecycle.EVALUATION_PENDING
        self._append(
            "effect_revision_required",
            state,
            fill_id=str(fill.id),
            intent_id=intent_id,
            order_id=str(fill.order_id),
            instrument_id=str(fill.instrument.id),
            quantity=str(fill.quantity),
            price=str(fill.price),
            occurred_at=fill.occurred_at.isoformat(),
            horizons=sorted(revised_horizons),
            source_event_sequence=event.metadata.sequence,
            occurred_at_unix_nanos=occurred_at,
        )
        return self._snapshot(state)

    def observe_timer(self, timer: TimerEvent) -> str | None:
        if not timer.timer_id.startswith(self._TIMER_PREFIX):
            return None
        binding = self._timer_bindings.pop(timer.timer_id, None)
        if binding is None:
            raise ValueError("invalid decision evaluation timer identity")
        decision_id, horizon = binding
        state = self._require(decision_id)
        state.due_horizons.add(horizon)
        state.due_at_unix_nanos[horizon] = self._unix_nanos(timer.event_time)
        state.lifecycle = DecisionLifecycle.EVALUATION_PENDING
        self._append(
            "evaluation_due",
            state,
            horizon=horizon,
            occurred_at_unix_nanos=self._unix_nanos(timer.event_time),
        )
        return horizon

    def evaluate(
        self,
        strategy_decision_id: str,
        *,
        horizon: str,
        outcome: EffectOutcome,
        summary: str,
        evidence: tuple[EffectEvidence, ...],
        evaluated_at: datetime | None = None,
    ) -> DecisionEffectEvaluation:
        state = self._require(strategy_decision_id)
        if horizon not in {value.name for value in state.horizons}:
            raise ValueError(f"unknown decision horizon: {horizon}")
        if outcome not in {"favorable", "neutral", "adverse", "unavailable"}:
            raise ValueError(f"unsupported effect outcome: {outcome}")
        if not summary.strip():
            raise ValueError("effect evaluation summary is required")
        if outcome != "unavailable" and not evidence:
            raise ValueError("effect evaluation requires public fact evidence")
        revision = 1 + max(
            (value.revision for value in state.evaluations if value.horizon == horizon),
            default=0,
        )
        at = self._event_time(evaluated_at)
        evaluation_id = self._evaluation_id(strategy_decision_id, horizon, revision)
        evaluation = DecisionEffectEvaluation(
            evaluation_id,
            strategy_decision_id,
            tuple(state.intent_ids),
            horizon,
            revision,
            outcome,
            self._unix_nanos(at),
            summary.strip(),
            tuple(evidence),
        )
        state.evaluations.append(evaluation)
        state.due_horizons.discard(horizon)
        state.due_at_unix_nanos.pop(horizon, None)
        if self._all_horizons_evaluated(state):
            state.lifecycle = DecisionLifecycle.EVALUATED
        else:
            state.lifecycle = DecisionLifecycle.EVALUATION_PENDING
        self._append(
            "effect_evaluated",
            state,
            evaluation=self._evaluation_dict(evaluation),
        )
        self._notify(
            title="Decision effect evaluated",
            body=f"{horizon}: {outcome}\n{summary.strip()}",
            severity="warning" if outcome in {"adverse", "unavailable"} else "info",
            dedupe_key=f"effect:{evaluation_id}:revision:{revision}",
            attributes={
                "strategy_decision_id": strategy_decision_id,
                "evaluation_id": evaluation_id,
                "horizon": horizon,
                "outcome": outcome,
            },
        )
        return evaluation

    def decision(self, strategy_decision_id: str) -> StrategyDecision | None:
        state = self._states.get(strategy_decision_id)
        return None if state is None else self._snapshot(state)

    def decisions(self) -> tuple[StrategyDecision, ...]:
        return tuple(self._snapshot(state) for state in self._states.values())

    def pending_evaluations(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (state.decision_id, horizon)
            for state in self._states.values()
            for horizon in sorted(state.due_horizons)
        )

    def health(self) -> dict[str, object]:
        pending = self.pending_evaluations()
        now = self._clock.now or datetime.now(timezone.utc)
        now_nanos = self._unix_nanos(now)
        due_times = [
            state.due_at_unix_nanos[horizon]
            for state in self._states.values()
            for horizon in state.due_horizons
            if horizon in state.due_at_unix_nanos
        ]
        return {
            "decision_count": len(self._states),
            "pending_evaluation_count": len(pending),
            "oldest_overdue_evaluation_age_ms": (
                None
                if not due_times
                else max(0, (now_nanos - min(due_times)) // 1_000_000)
            ),
            "unattributed_intent_count": self._unattributed_intents,
            "lifecycle_sequence_gap_count": self._lifecycle_sequence_gaps,
        }

    def traces(self) -> list[dict[str, object]]:
        """Return JSON-safe end-to-end decision traces for diagnostics."""

        return [
            {
                "strategy_decision_id": state.decision_id,
                "strategy_id": state.strategy_id,
                "lifecycle": state.lifecycle.value,
                "reason": state.reason,
                "expected_outcome": state.expected_outcome,
                "evidence": [self._evidence_dict(value) for value in state.evidence],
                "expected_intent_count": state.expected_intent_count,
                "intent_ids": list(state.intent_ids),
                "intent_statuses": {
                    key: value.value for key, value in state.intent_statuses.items()
                },
                "due_horizons": sorted(state.due_horizons),
                "evaluations": [
                    self._evaluation_dict(value) for value in state.evaluations
                ],
            }
            for state in self._states.values()
        ]

    def trace(self, strategy_decision_id: str) -> dict[str, object] | None:
        trace = next(
            (
                value
                for value in self.traces()
                if value["strategy_decision_id"] == strategy_decision_id
            ),
            None,
        )
        if trace is None:
            return None
        records = [
            dict(value)
            for value in self._journal.records()
            if value.get("strategy_decision_id") == strategy_decision_id
        ]
        intent_ids = trace["intent_ids"]
        assert isinstance(intent_ids, list)
        trace["execution"] = {
            "intents": [
                {
                    "intent_id": intent_id,
                    "transitions": [
                        value
                        for value in records
                        if value.get("record_type") == "intent_status_observed"
                        and value.get("intent_id") == intent_id
                    ],
                    "fills": [
                        value
                        for value in records
                        if value.get("record_type")
                        in {"fill_observed", "effect_revision_required"}
                        and value.get("intent_id") == intent_id
                    ],
                }
                for intent_id in intent_ids
            ]
        }
        trace["notifications"] = [
            value
            for value in records
            if value.get("record_type") == "notification_submission_recorded"
        ]
        return trace

    def _execution_complete(self, state: _DecisionState) -> bool:
        return len(state.intent_ids) == state.expected_intent_count and all(
            state.intent_statuses.get(intent_id) in _TERMINAL_INTENT_STATUSES
            for intent_id in state.intent_ids
        )

    def _complete_execution_if_ready(
        self,
        state: _DecisionState,
        event: IntentUpdateEvent,
        *,
        replay: bool = False,
    ) -> None:
        if state.lifecycle is DecisionLifecycle.SUBMITTED and self._execution_complete(
            state
        ):
            state.lifecycle = DecisionLifecycle.EXECUTION_COMPLETED
            self._append("execution_completed", state)
            if not replay:
                statuses = ", ".join(
                    f"{intent_id}={status.value}"
                    for intent_id, status in state.intent_statuses.items()
                )
                adverse = any(
                    status not in {IntentStatus.SATISFIED, IntentStatus.COMPLETED}
                    for status in state.intent_statuses.values()
                )
                self._notify(
                    title="Strategy decision execution completed",
                    body=statuses,
                    severity="warning" if adverse else "info",
                    dedupe_key=f"decision:{state.decision_id}:execution-completed",
                    attributes={
                        "strategy_decision_id": state.decision_id,
                        "lifecycle": "execution_completed",
                    },
                )
            self._schedule_evaluations(state, event)

    def _schedule_evaluations(
        self, state: _DecisionState, event: IntentUpdateEvent
    ) -> None:
        base = event.metadata.occurred_at
        if base is None and event.metadata.occurred_at_unix_nanos is not None:
            base = datetime.fromtimestamp(
                event.metadata.occurred_at_unix_nanos / 1_000_000_000,
                tz=timezone.utc,
            )
        base = base or self._clock.now
        if base is None:
            raise ValueError("effect evaluation requires Strategy business time")
        scheduled: list[dict[str, object]] = []
        for horizon in state.horizons:
            if horizon.delay is None:
                state.due_horizons.add(horizon.name)
                state.due_at_unix_nanos[horizon.name] = self._unix_nanos(base)
                continue
            due_at = base + horizon.delay
            self._clock.at(self._timer_id(state.decision_id, horizon.name), due_at)
            scheduled.append(
                {
                    "horizon": horizon.name,
                    "due_at": due_at.isoformat(),
                }
            )
        state.lifecycle = DecisionLifecycle.EVALUATION_PENDING
        self._append(
            "evaluation_pending",
            state,
            due_horizons=sorted(state.due_horizons),
            scheduled_horizons=scheduled,
        )

    def _notify_intent(self, event: IntentUpdateEvent) -> None:
        status = event.data.status
        severity_by_status: dict[IntentStatus, NotificationSeverity] = {
            IntentStatus.REJECTED: "warning",
            IntentStatus.CANCELED: "warning",
            IntentStatus.EXPIRED: "warning",
            IntentStatus.FAILED: "error",
            IntentStatus.COMPENSATING: "warning",
            IntentStatus.RECONCILIATION_REQUIRED: "critical",
        }
        severity = severity_by_status.get(status, "info")
        if status not in {
            IntentStatus.SATISFIED,
            IntentStatus.COMPLETED,
            IntentStatus.REJECTED,
            IntentStatus.CANCELED,
            IntentStatus.EXPIRED,
            IntentStatus.FAILED,
            IntentStatus.COMPENSATING,
            IntentStatus.RECONCILIATION_REQUIRED,
        }:
            return
        self._notify(
            title=f"Intent {status.value}",
            body=f"Intent {event.data.id}: {event.data.reason or status.value}",
            severity=severity,
            dedupe_key=f"intent:{event.data.id}:lifecycle:{event.metadata.sequence}",
            attributes={
                "strategy_decision_id": event.data.strategy_decision_id or "",
                "intent_id": str(event.data.id),
                "lifecycle": status.value,
            },
        )

    def _notify(
        self,
        *,
        title: str,
        body: str,
        severity: NotificationSeverity,
        dedupe_key: str | None,
        attributes: Mapping[str, str],
    ) -> None:
        if not self._notification_routes:
            return
        receipt = self._notifications.publish(
            title=title,
            body=body,
            severity=severity,
            dedupe_key=dedupe_key,
            attributes=attributes,
            routes=self._notification_routes,
        )
        decision_id = attributes.get("strategy_decision_id")
        if decision_id is None:
            return
        state = self._states.get(decision_id)
        if state is None:
            return
        self._append(
            "notification_submission_recorded",
            state,
            notification_id=getattr(receipt, "notification_id", None),
            status=getattr(receipt, "status", "accepted"),
            routes=list(getattr(receipt, "routes", self._notification_routes)),
            accepted_destinations=getattr(receipt, "accepted_destinations", None),
            reason=getattr(receipt, "reason", None),
            dedupe_key=dedupe_key,
            severity=severity,
        )

    def _snapshot(self, state: _DecisionState) -> StrategyDecision:
        return StrategyDecision(
            state.decision_id,
            state.strategy_id,
            state.lifecycle,
            state.reason,
            state.expected_outcome,
            state.evidence,
            state.expected_intent_count,
            tuple(state.intent_ids),
            MappingProxyType(dict(state.intent_statuses)),
            state.horizons,
            tuple(sorted(state.due_horizons)),
            tuple(state.evaluations),
            state.recorded_at_unix_nanos,
        )

    def _require(self, decision_id: str) -> _DecisionState:
        state = self._states.get(decision_id)
        if state is None:
            raise KeyError(f"Strategy decision not found: {decision_id}")
        return state

    def _append(self, kind: str, state: _DecisionState, **values: object) -> None:
        self._sequence += 1
        self._journal.append(
            {
                "schema_version": 1,
                "sequence": self._sequence,
                "record_type": kind,
                "strategy_id": state.strategy_id,
                "strategy_decision_id": state.decision_id,
                "lifecycle": state.lifecycle.value,
                **values,
            }
        )

    def _restore(self) -> None:
        for record in self._journal.records():
            self._sequence = max(
                self._sequence, _integer(record.get("sequence", 0), "sequence")
            )
            kind = str(record.get("record_type", ""))
            decision_id = str(record.get("strategy_decision_id", ""))
            if kind == "decision_recorded":
                horizons = tuple(
                    DecisionHorizon(
                        str(value["name"]),
                        None
                        if value.get("delay_nanos") is None
                        else timedelta(
                            microseconds=_integer(
                                value["delay_nanos"], "horizon delay_nanos"
                            )
                            / 1_000
                        ),
                    )
                    for value in _items(record.get("horizons", []), "horizons")
                    if isinstance(value, dict)
                )
                self._states[decision_id] = _DecisionState(
                    decision_id,
                    str(record["strategy_id"]),
                    DecisionLifecycle.RECORDED,
                    str(record["reason"]),
                    str(record["expected_outcome"]),
                    tuple(
                        _evidence_from_mapping(value)
                        for value in _items(record.get("evidence", []), "evidence")
                        if isinstance(value, Mapping)
                    ),
                    _integer(record["expected_intent_count"], "expected_intent_count"),
                    horizons,
                    _integer(
                        record["occurred_at_unix_nanos"], "occurred_at_unix_nanos"
                    ),
                )
                continue
            state = self._states.get(decision_id)
            if state is None:
                raise ValueError(
                    f"decision journal record references unknown decision: {decision_id}"
                )
            if kind == "intent_attached":
                intent_id = str(record["intent_id"])
                if intent_id not in state.intent_ids:
                    state.intent_ids.append(intent_id)
            elif kind == "intent_status_observed":
                state.intent_statuses[str(record["intent_id"])] = IntentStatus(
                    str(record["status"])
                )
            elif kind == "evaluation_due":
                horizon = str(record["horizon"])
                state.due_horizons.add(horizon)
                state.due_at_unix_nanos[horizon] = _integer(
                    record["occurred_at_unix_nanos"], "occurred_at_unix_nanos"
                )
            elif kind == "evaluation_pending":
                for value in _items(record.get("due_horizons", []), "due_horizons"):
                    horizon = str(value)
                    state.due_horizons.add(horizon)
                    state.due_at_unix_nanos.setdefault(
                        horizon, state.recorded_at_unix_nanos
                    )
                for value in _items(
                    record.get("scheduled_horizons", []), "scheduled_horizons"
                ):
                    if not isinstance(value, dict):
                        continue
                    due_at = datetime.fromisoformat(str(value["due_at"]))
                    if due_at.tzinfo is None:
                        raise ValueError(
                            "restored decision timer must be timezone-aware"
                        )
                    self._clock.at(
                        self._timer_id(decision_id, str(value["horizon"])),
                        due_at,
                    )
            elif kind == "effect_evaluated":
                value = record.get("evaluation")
                if not isinstance(value, dict):
                    raise ValueError("effect evaluation journal payload is invalid")
                evaluation = self._evaluation_from_dict(value)
                state.evaluations.append(evaluation)
                state.due_horizons.discard(evaluation.horizon)
                state.due_at_unix_nanos.pop(evaluation.horizon, None)
                timer_id = self._timer_id(decision_id, evaluation.horizon)
                self._clock.cancel(timer_id)
                self._timer_bindings.pop(timer_id, None)
            elif kind in {"fill_observed", "effect_revision_required"}:
                state.observed_fill_ids.add(str(record["fill_id"]))
                if kind == "effect_revision_required":
                    due_at = _integer(
                        record["occurred_at_unix_nanos"], "occurred_at_unix_nanos"
                    )
                    for value in _items(record.get("horizons", []), "horizons"):
                        horizon = str(value)
                        state.due_horizons.add(horizon)
                        state.due_at_unix_nanos[horizon] = due_at
            state.lifecycle = DecisionLifecycle(str(record.get("lifecycle")))

    @staticmethod
    def _evaluation_dict(value: DecisionEffectEvaluation) -> dict[str, object]:
        return {
            "evaluation_id": value.evaluation_id,
            "strategy_decision_id": value.strategy_decision_id,
            "intent_ids": list(value.intent_ids),
            "horizon": value.horizon,
            "revision": value.revision,
            "outcome": value.outcome,
            "evaluated_at_unix_nanos": value.evaluated_at_unix_nanos,
            "summary": value.summary,
            "evidence": [
                StrategyDecisionApplication._evidence_dict(item)
                for item in value.evidence
            ],
        }

    @staticmethod
    def _evidence_dict(item: EffectEvidence) -> dict[str, object]:
        return {
            "owner": item.owner,
            "reference_id": item.reference_id,
            "event_sequence": item.event_sequence,
            "observed_at_unix_nanos": item.observed_at_unix_nanos,
            "value": item.value,
            "unit": item.unit,
        }

    @staticmethod
    def _evaluation_from_dict(value: Mapping[str, object]) -> DecisionEffectEvaluation:
        evidence = tuple(
            _evidence_from_mapping(item)
            for item in _items(value.get("evidence", []), "evaluation evidence")
            if isinstance(item, Mapping)
        )
        return DecisionEffectEvaluation(
            str(value["evaluation_id"]),
            str(value["strategy_decision_id"]),
            tuple(
                str(item) for item in _items(value.get("intent_ids", []), "intent_ids")
            ),
            str(value["horizon"]),
            _integer(value["revision"], "evaluation revision"),
            str(value["outcome"]),  # type: ignore[arg-type]
            _integer(value["evaluated_at_unix_nanos"], "evaluated_at_unix_nanos"),
            str(value["summary"]),
            evidence,
        )

    def _next_decision_id(self, at: datetime) -> str:
        self._sequence += 1
        material = f"{self.strategy_id}:{self.instance_id}:{self._unix_nanos(at)}:{self._sequence}"
        digest = hashlib.sha256(material.encode()).hexdigest()[:20]
        return f"{self.strategy_id}:decision:{digest}"

    @staticmethod
    def _evaluation_id(decision_id: str, horizon: str, revision: int) -> str:
        digest = hashlib.sha256(
            f"{decision_id}:{horizon}:{revision}".encode()
        ).hexdigest()[:20]
        return f"evaluation:{digest}"

    def _timer_id(self, decision_id: str, horizon: str) -> str:
        digest = hashlib.sha256(f"{decision_id}:{horizon}".encode()).hexdigest()[:24]
        timer_id = f"{self._TIMER_PREFIX}{digest}"
        self._timer_bindings[timer_id] = (decision_id, horizon)
        return timer_id

    def _event_time(self, value: datetime | None) -> datetime:
        result = value or self._clock.now or datetime.now(timezone.utc)
        if result.tzinfo is None:
            raise ValueError("decision business time must be timezone-aware")
        return result.astimezone(timezone.utc)

    @staticmethod
    def _unix_nanos(value: datetime) -> int:
        return int(value.timestamp() * 1_000_000_000)

    @staticmethod
    def _all_horizons_evaluated(state: _DecisionState) -> bool:
        evaluated = {value.horizon for value in state.evaluations}
        return evaluated == {value.name for value in state.horizons}


__all__ = [
    "DecisionEffectEvaluation",
    "DecisionHorizon",
    "DecisionLifecycle",
    "EffectEvidence",
    "EffectOutcome",
    "StrategyDecision",
    "StrategyDecisionApplication",
]
