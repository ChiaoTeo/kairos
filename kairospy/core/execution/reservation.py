from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from kairospy.core.account import AccountRef


class ReservationStatus(StrEnum):
    HELD = "held"
    REFLECTED = "reflected"
    RELEASED = "released"
    CONSUMED = "consumed"


@dataclass(frozen=True, slots=True)
class Reservation:
    reservation_id: str
    account: AccountRef
    currency: str
    amount: Decimal
    reason: str
    created_at: datetime
    order_id: str | None = None
    status: ReservationStatus = ReservationStatus.HELD

    def __post_init__(self) -> None:
        if not self.reservation_id.strip() or not self.currency.strip() or not self.reason.strip():
            raise ValueError("reservation identity fields cannot be empty")
        if self.amount <= 0:
            raise ValueError("reservation amount must be positive")
        if self.created_at.tzinfo is None:
            raise ValueError("reservation timestamp must be timezone-aware")


class ReservationBook:
    def __init__(self, reservations: tuple[Reservation, ...] = ()) -> None:
        self._reservations: dict[str, Reservation] = {}
        for reservation in reservations:
            self.hold(reservation)

    def hold(self, reservation: Reservation) -> None:
        existing = self._reservations.get(reservation.reservation_id)
        if existing is not None and existing != reservation:
            raise ValueError(f"conflicting reservation: {reservation.reservation_id}")
        self._reservations[reservation.reservation_id] = reservation

    def release(self, reservation_id: str) -> Reservation:
        return self._transition(reservation_id, ReservationStatus.RELEASED)

    def consume(self, reservation_id: str) -> Reservation:
        return self._transition(reservation_id, ReservationStatus.CONSUMED)

    def reflect(self, reservation_id: str) -> Reservation:
        return self._transition(reservation_id, ReservationStatus.REFLECTED)

    def active_amounts(self, account: AccountRef) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for reservation in self._reservations.values():
            if reservation.account != account or reservation.status is not ReservationStatus.HELD:
                continue
            totals[reservation.currency] = totals.get(reservation.currency, Decimal("0")) + reservation.amount
        return totals

    @property
    def reservations(self) -> tuple[Reservation, ...]:
        return tuple(self._reservations.values())

    def _transition(self, reservation_id: str, status: ReservationStatus) -> Reservation:
        reservation = self._reservations.get(reservation_id)
        if reservation is None:
            raise KeyError(reservation_id)
        if reservation.status not in {ReservationStatus.HELD, ReservationStatus.REFLECTED}:
            raise ValueError("only held reservations can transition")
        updated = Reservation(
            reservation.reservation_id,
            reservation.account,
            reservation.currency,
            reservation.amount,
            reservation.reason,
            reservation.created_at,
            reservation.order_id,
            status,
        )
        self._reservations[reservation_id] = updated
        return updated


__all__ = ["Reservation", "ReservationBook", "ReservationStatus"]
