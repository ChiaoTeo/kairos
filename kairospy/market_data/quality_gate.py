from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from kairospy.backtest.calendar import TradingCalendar

from .events import MarketEventEnvelope, MarketEventType


class QualitySeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class EventQualityIssue:
    code: str
    severity: QualitySeverity
    message: str
    event_key: tuple | None = None


@dataclass(frozen=True, slots=True)
class EventQualityReport:
    event_count: int
    issues: tuple[EventQualityIssue, ...]

    @property
    def publishable(self) -> bool:
        return not any(item.severity is QualitySeverity.ERROR for item in self.issues)


def validate_events(events: tuple[MarketEventEnvelope, ...], *, known_exchange_codes: set[str] | None = None,
                    known_condition_codes: set[str] | None = None) -> EventQualityReport:
    issues: list[EventQualityIssue] = []
    calendar = TradingCalendar()
    seen: dict[tuple, MarketEventEnvelope] = {}
    ordered = sorted(events, key=lambda item: item.event_key)
    if list(events) != ordered:
        issues.append(EventQualityIssue("out_of_order", QualitySeverity.ERROR, "events are not sorted by available_time and source_order"))
    for event in events:
        key = (event.source, event.source_namespace, event.source_instrument_id, event.record_type, event.event_time, event.source_order)
        previous = seen.get(key)
        if previous is not None:
            severity = QualitySeverity.WARNING if previous == event else QualitySeverity.ERROR
            code = "duplicate_event" if previous == event else "duplicate_conflict"
            issues.append(EventQualityIssue(code, severity, "duplicate event key", event.event_key))
        else:
            seen[key] = event
        if event.available_time < event.event_time:
            issues.append(EventQualityIssue("future_visibility", QualitySeverity.ERROR, "available_time precedes event_time", event.event_key))
        if event.receive_time is not None and event.receive_time < event.event_time:
            issues.append(EventQualityIssue("negative_receive_latency", QualitySeverity.ERROR, "receive_time precedes event_time", event.event_key))
        local_time = event.event_time.astimezone(calendar.timezone)
        if not calendar.is_trading_day(local_time.date()):
            issues.append(EventQualityIssue("non_trading_day", QualitySeverity.WARNING, "event occurs on a US market non-trading day", event.event_key))
        else:
            session = calendar.session(local_time.date())
            if not session.opens_at <= local_time <= session.closes_at:
                issues.append(EventQualityIssue("outside_regular_session", QualitySeverity.WARNING, "event occurs outside the regular US session", event.event_key))
        if event.record_type is MarketEventType.QUOTE:
            bid, ask = _decimal(event.payload.get("bid")), _decimal(event.payload.get("ask"))
            if bid is None or ask is None:
                issues.append(EventQualityIssue("missing_two_sided_quote", QualitySeverity.WARNING, "quote is not two-sided", event.event_key))
            if bid is not None and ask is not None and bid > ask:
                issues.append(EventQualityIssue("crossed_quote", QualitySeverity.ERROR, "bid exceeds ask", event.event_key))
            if bid is not None and bid < 0 or ask is not None and ask < 0:
                issues.append(EventQualityIssue("negative_quote", QualitySeverity.ERROR, "quote price cannot be negative", event.event_key))
        if event.record_type is MarketEventType.TRADE:
            price, size = _decimal(event.payload.get("price")), _decimal(event.payload.get("size"))
            if price is None or size is None or price <= 0 or size <= 0:
                issues.append(EventQualityIssue("invalid_trade", QualitySeverity.ERROR, "trade price and size must be positive", event.event_key))
        if known_exchange_codes is not None:
            for field in ("exchange", "bid_exchange", "ask_exchange"):
                value = event.payload.get(field)
                if value is not None and str(value) not in known_exchange_codes:
                    issues.append(EventQualityIssue("unknown_exchange_code", QualitySeverity.ERROR, f"unknown Massive exchange code {value}", event.event_key))
        if known_condition_codes is not None:
            for value in event.payload.get("conditions", ()) or ():
                if str(value) not in known_condition_codes:
                    issues.append(EventQualityIssue("unknown_condition_code", QualitySeverity.ERROR, f"unknown Massive condition code {value}", event.event_key))
    return EventQualityReport(len(events), tuple(issues))


def require_publishable(report: EventQualityReport) -> None:
    if not report.publishable:
        codes = sorted({item.code for item in report.issues if item.severity is QualitySeverity.ERROR})
        raise ValueError(f"market event dataset failed quality gate: {', '.join(codes)}")


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))
