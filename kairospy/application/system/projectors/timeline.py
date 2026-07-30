from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Mapping

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.system.artifacts.output import RunOutput
from kairospy.core.market import Bar, MarketEvent, OptionGreeks, Quote, RateObservation, TradePrint
from kairospy.core.views import ViewStore


@dataclass(frozen=True, slots=True)
class TimelineTrigger:
    time: datetime
    sequence: int | None
    trigger: str
    event: Mapping[str, object]


class TimelineProjector:
    def __init__(self, output: RunOutput, *, sample_interval: str | timedelta | None = "1m") -> None:
        self.output = output
        self.sample_interval = _sample_interval(sample_interval)
        self._pending: list[TimelineTrigger] = []
        self._last_sample_time: datetime | None = None
        self._last_written_marker: object | None = None
        self._intent_states: dict[str, tuple[str, bool]] = {}
        self._order_states: dict[str, str] = {}

    def on_event(self, event: RuntimeEnvelope) -> None:
        if self._should_sample_interval(event.time):
            self._pending.append(
                TimelineTrigger(
                    time=event.time,
                    sequence=event.sequence,
                    trigger="interval",
                    event=_event_summary(event),
                )
            )
            self._last_sample_time = event.time
        trigger = _event_trigger(event)
        if trigger is not None:
            self._pending.append(
                TimelineTrigger(
                    time=event.time,
                    sequence=event.sequence,
                    trigger=trigger,
                    event=_event_summary(event),
                )
            )

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        now = getattr(context, "now", None)
        if now is None:
            return
        sequence = getattr(context, "sequence", None)
        traces = tuple(getattr(context, "emitted_traces", ()) or ())
        if traces:
            self._pending.append(
                TimelineTrigger(
                    time=now,
                    sequence=sequence if isinstance(sequence, int) else None,
                    trigger="decision",
                    event={
                        "domain": "strategy",
                        "kind": hook or "decision",
                        "summary": hook or "decision",
                        "trace_count": len(traces),
                    },
                )
            )
        if intents:
            intent_ids = tuple(str(getattr(intent, "intent_id", "")) for intent in intents)
            for intent_id in intent_ids:
                if intent_id:
                    self._intent_states.setdefault(intent_id, ("created", True))
            self._pending.append(
                TimelineTrigger(
                    time=now,
                    sequence=sequence if isinstance(sequence, int) else None,
                    trigger="intent_created",
                    event={
                        "domain": "intent",
                        "kind": hook or "intent",
                        "summary": f"{len(intents)} intent(s) emitted",
                        "intent_count": len(intents),
                        "intent_ids": intent_ids,
                    },
                )
            )

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        self._pending.extend(_intent_triggers(views, self._intent_states, as_of=as_of))
        self._pending.extend(_order_triggers(views, self._order_states, as_of=as_of))
        pending = self._coalesced_pending()
        self._pending.clear()
        for trigger in pending:
            record = {
                "time": trigger.time,
                "sequence": trigger.sequence,
                "trigger": trigger.trigger,
                "event": dict(trigger.event),
                "context_hash": views.context_hash,
                "views": _view_snapshot(views),
            }
            marker = (record["time"], record["sequence"], record["trigger"], record["event"], record["context_hash"])
            if marker == self._last_written_marker:
                continue
            self.output.append_history("timeline", record)
            self._last_written_marker = marker

    def _should_sample_interval(self, time: datetime) -> bool:
        if self.sample_interval is None:
            return False
        if self._last_sample_time is None:
            return True
        return time >= self._last_sample_time + self.sample_interval

    def _coalesced_pending(self) -> tuple[TimelineTrigger, ...]:
        if not self._pending:
            return ()
        by_key: dict[tuple[datetime, int | None, str], TimelineTrigger] = {}
        for item in self._pending:
            by_key[(item.time, item.sequence, item.trigger)] = item
        return tuple(by_key[key] for key in sorted(by_key, key=lambda item: (item[0], -1 if item[1] is None else item[1], item[2])))


def _sample_interval(value: str | timedelta | None) -> timedelta | None:
    if value is None:
        return None
    if isinstance(value, timedelta):
        return value if value.total_seconds() > 0 else None
    text = str(value).strip().lower()
    if not text or text in {"off", "none", "false", "0"}:
        return None
    units = {
        "ms": 0.001,
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
    }
    for suffix, seconds in units.items():
        if text.endswith(suffix):
            amount = Decimal(text[: -len(suffix)].strip())
            return timedelta(seconds=float(amount * Decimal(str(seconds)))) if amount > 0 else None
    amount = Decimal(text)
    return timedelta(seconds=float(amount)) if amount > 0 else None


def _event_trigger(event: RuntimeEnvelope) -> str | None:
    domain = str(event.domain)
    kind = event.kind.lower()
    if domain == "account":
        return "account_update"
    if domain == "system" and "risk" in kind:
        return "risk"
    return None


def _intent_triggers(
    views: ViewStore,
    previous: dict[str, tuple[str, bool]],
    *,
    as_of: datetime | None,
) -> tuple[TimelineTrigger, ...]:
    view = views.get("system.intents", None)
    rows = tuple(getattr(view, "states", ()) or ())
    triggers: list[TimelineTrigger] = []
    for row in rows:
        intent_id = str(getattr(row, "intent_id", "") or "")
        if not intent_id:
            continue
        status = str(getattr(row, "status", "") or "")
        active = bool(getattr(row, "active", False))
        old = previous.get(intent_id)
        previous[intent_id] = (status, active)
        if old is None:
            triggers.append(_state_trigger("intent_created", as_of, "intent", status, intent_id))
        elif old != (status, active):
            trigger = "intent_completed" if old[1] and not active else "intent_update"
            triggers.append(_state_trigger(trigger, as_of, "intent", status, intent_id))
    return tuple(triggers)


def _order_triggers(
    views: ViewStore,
    previous: dict[str, str],
    *,
    as_of: datetime | None,
) -> tuple[TimelineTrigger, ...]:
    view = views.get("execution.current", None)
    rows = tuple(getattr(view, "orders", ()) or ())
    triggers: list[TimelineTrigger] = []
    for row in rows:
        order_id = str(getattr(row, "order_id", "") or "")
        if not order_id:
            continue
        status = str(getattr(row, "status", "") or "")
        old = previous.get(order_id)
        previous[order_id] = status
        if old is None:
            triggers.append(_state_trigger(_order_trigger(status, initial=True), as_of, "order", status, order_id))
        elif old != status:
            triggers.append(_state_trigger(_order_trigger(status, initial=False), as_of, "order", status, order_id))
    return tuple(triggers)


def _order_trigger(status: str, *, initial: bool) -> str:
    if status in {"filled", "partially_filled"}:
        return "fill"
    if status in {"cancel_requested", "canceled"}:
        return "order_cancelled"
    if status in {"submitting", "acknowledged"}:
        return "order_submitted"
    if initial:
        return "order_created"
    return "order_update"


def _state_trigger(trigger: str, time: datetime | None, domain: str, status: str, identity: str) -> TimelineTrigger:
    return TimelineTrigger(
        time=time or datetime.now().astimezone(),
        sequence=None,
        trigger=trigger,
        event={
            "domain": domain,
            "kind": status,
            "summary": f"{domain} {identity} {status}".strip(),
            "id": identity,
        },
    )


def _event_summary(event: RuntimeEnvelope) -> Mapping[str, object]:
    return {
        "domain": str(event.domain),
        "kind": event.kind,
        "summary": _summary_text(event),
    }


def _summary_text(event: RuntimeEnvelope) -> str:
    payload = event.payload
    value = payload.value if isinstance(payload, MarketEvent) else payload
    instrument_id = getattr(value, "instrument_id", None)
    if instrument_id is not None:
        return " ".join(part for part in (event.kind, str(instrument_id), _price_text(value)) if part)
    return f"{event.domain}.{event.kind}"


def _price_text(value: object) -> str:
    price = _mark_price(value)
    return "" if price is None else f"@ {price}"


def _view_snapshot(views: ViewStore) -> Mapping[str, object]:
    return {
        key: {
            **envelope.to_dict(),
            "payload": envelope.payload,
        }
        for key, envelope in views.envelopes().items()
    }


def _mark_price(value: object) -> Decimal | None:
    if isinstance(value, Bar):
        return getattr(value, "close", None)
    if isinstance(value, TradePrint):
        return getattr(value, "price", None)
    if isinstance(value, RateObservation):
        return getattr(value, "mark_price", None)
    if isinstance(value, OptionGreeks):
        return getattr(value, "mark_price", None) or getattr(value, "underlying_price", None)
    if isinstance(value, Quote):
        return getattr(value, "midpoint", None) or getattr(value, "bid", None) or getattr(value, "ask", None)
    return None


__all__ = ["TimelineProjector", "TimelineTrigger"]
