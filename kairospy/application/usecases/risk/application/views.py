from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairospy.domain.views import ViewFieldSchema, ViewSchema

from ..domain import RiskBudget, RiskReservation


class RiskViewKeys:
    events = "risk.events"
    budget = "risk.budget"


@dataclass(frozen=True, slots=True)
class RiskEventView:
    event_count: int = 0
    last_name: str | None = None
    last_event_time: datetime | None = None
    last_payload: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class RiskBudgetView:
    budgets: tuple[RiskBudget, ...] = ()
    reservations: tuple[RiskReservation, ...] = ()
    as_of: datetime | None = None


RISK_EVENTS_SCHEMA = ViewSchema(
    RiskViewKeys.events,
    "system",
    fields=(
        ViewFieldSchema("event_count", "consumed risk event count", "runtime sequence", "risk event"),
        ViewFieldSchema("last_name", "latest risk event name", "event time", "risk event"),
        ViewFieldSchema("last_event_time", "latest risk event time", "event time", "risk event"),
        ViewFieldSchema("last_payload", "latest risk event payload", "event time", "risk event"),
    ),
    mutability="runtime_writable",
    evidence="runtime risk event view state",
)

RISK_BUDGET_SCHEMA = ViewSchema(
    RiskViewKeys.budget,
    "risk",
    fields=(
        ViewFieldSchema("budgets", "configured risk budgets and available capacity", "risk snapshot", "risk budget ledger"),
        ViewFieldSchema("reservations", "active and terminal risk reservations", "risk snapshot", "risk reservation ledger"),
        ViewFieldSchema("as_of", "risk snapshot timestamp", "risk snapshot", "risk budget ledger"),
    ),
    mutability="runtime_writable",
    evidence="risk budget and reservation ledger view",
)


__all__ = ["RISK_BUDGET_SCHEMA", "RISK_EVENTS_SCHEMA", "RiskBudgetView", "RiskEventView", "RiskViewKeys"]
