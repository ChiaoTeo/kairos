from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from kairospy.domain.reference import InstrumentId

from .model import AccountBookRef


class AccountEventKind(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    FILL = "fill"
    FEE = "fee"
    FUNDING = "funding"
    SETTLEMENT = "settlement"
    ADJUSTMENT = "adjustment"


@dataclass(frozen=True, slots=True)
class AccountEvent:
    event_id: UUID
    account: AccountBookRef
    kind: AccountEventKind
    occurred_at: datetime
    currency: str
    cash_delta: Decimal = Decimal("0")
    instrument_id: InstrumentId | str | None = None
    position_delta: Decimal = Decimal("0")
    reference_id: str = ""

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("account event timestamp must be timezone-aware")
        if not self.currency.strip():
            raise ValueError("account event currency cannot be empty")
        object.__setattr__(self, "instrument_id", None if self.instrument_id is None else _id(self.instrument_id, InstrumentId, "instrument_id"))
        if self.position_delta and not self.instrument_id:
            raise ValueError("position delta requires instrument_id")
        if self.cash_delta == 0 and self.position_delta == 0:
            raise ValueError("account event must change cash or position")


class AccountLedger:
    def __init__(self, events: tuple[AccountEvent, ...] = ()) -> None:
        self._events: list[AccountEvent] = []
        self._ids: set[UUID] = set()
        for event in events:
            self.record(event)

    def record(self, event: AccountEvent) -> None:
        if event.event_id in self._ids:
            raise ValueError(f"duplicate account event: {event.event_id}")
        if self._events and event.occurred_at < self._events[-1].occurred_at:
            raise ValueError("account events must be time ordered")
        self._events.append(event)
        self._ids.add(event.event_id)

    @property
    def events(self) -> tuple[AccountEvent, ...]:
        return tuple(self._events)

    def cash(self, account: AccountBookRef) -> dict[str, Decimal]:
        balances: dict[str, Decimal] = {}
        for event in self._events:
            if event.account != account or event.cash_delta == 0:
                continue
            balances[event.currency] = balances.get(event.currency, Decimal("0")) + event.cash_delta
        return {currency: amount for currency, amount in balances.items() if amount != 0}

    def positions(self, account: AccountBookRef) -> dict[str, Decimal]:
        positions: dict[str, Decimal] = {}
        for event in self._events:
            if event.account != account or event.position_delta == 0 or event.instrument_id is None:
                continue
            instrument_id = str(event.instrument_id)
            positions[instrument_id] = positions.get(instrument_id, Decimal("0")) + event.position_delta
        return {instrument: quantity for instrument, quantity in positions.items() if quantity != 0}


__all__ = ["AccountEvent", "AccountEventKind", "AccountLedger"]


def _required_text(value: object, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} cannot be empty")
    return text


def _id(value, id_type, label: str):
    if isinstance(value, id_type):
        return value
    return id_type(_required_text(value, label))
