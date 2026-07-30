from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Mapping

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.market import Bar, MarketEvent, Quote, RateObservation, TradePrint
from kairospy.core.views import ViewStore


@dataclass(frozen=True, slots=True)
class TimelineTrigger:
    time: datetime
    sequence: int | None
    trigger: str
    event: Mapping[str, object]


class TimelineProcessor:
    def __init__(self, journal: object, *, sample_interval: str | timedelta | None = "1m") -> None:
        self.journal = journal
        self.sample_interval = _sample_interval(sample_interval)
        self._pending: list[TimelineTrigger] = []
        self._last_sample_time: datetime | None = None
        self._last_written_marker: object | None = None

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
        trigger = _special_trigger(event)
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
                        "intent_ids": tuple(str(getattr(intent, "intent_id", "")) for intent in intents),
                    },
                )
            )

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        append = getattr(self.journal, "append", None)
        if not callable(append):
            return
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
            append(record)
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


def _special_trigger(event: RuntimeEnvelope) -> str | None:
    domain = str(event.domain)
    kind = event.kind.lower()
    if domain == "execution" and "fill" in kind:
        return "fill"
    if domain in {"order", "execution"} and "cancel" in kind:
        return "order_cancelled"
    if domain in {"order", "execution"} and "order" in kind:
        return "order_update"
    if domain == "intent":
        if any(part in kind for part in ("complete", "satisfied", "filled")):
            return "intent_completed"
        return "intent_update"
    if domain == "account":
        return "account_update"
    if domain == "system" and "risk" in kind:
        return "risk"
    return None


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
    if isinstance(value, Bar) or value.__class__.__name__ == "MarketBarSummary":
        return getattr(value, "close", None)
    if isinstance(value, TradePrint) or value.__class__.__name__ == "MarketTradeSummary":
        return getattr(value, "price", None)
    if isinstance(value, RateObservation) or value.__class__.__name__ == "MarketRateSummary":
        return getattr(value, "mark_price", None)
    if isinstance(value, Quote) or value.__class__.__name__ == "MarketQuoteSummary":
        return getattr(value, "midpoint", None) or getattr(value, "bid", None) or getattr(value, "ask", None)
    return None


__all__ = ["TimelineProcessor", "TimelineTrigger"]
