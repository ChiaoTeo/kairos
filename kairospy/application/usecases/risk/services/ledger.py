from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from threading import RLock

from ..domain import (
    RiskAllocation,
    RiskBudget,
    RiskDecision,
    RiskReservation,
    RiskReservationStatus,
    RiskUsage,
)


class RiskBudgetLedger:
    """In-memory budget and reservation ledger.

    Composition may replace this implementation with a durable transactional
    store. The application contract intentionally does not expose this class.
    """

    def __init__(self, budgets: tuple[RiskBudget, ...] = (), *, allow_unbudgeted: bool = True) -> None:
        self._lock = RLock()
        self._budgets = {budget.budget_id: budget for budget in budgets}
        self._reservations: dict[str, RiskReservation] = {}
        self._allow_unbudgeted = allow_unbudgeted

    def replace_budgets(self, budgets: tuple[RiskBudget, ...]) -> None:
        with self._lock:
            if self._reservations:
                raise ValueError("cannot replace risk budgets while reservations exist")
            if len({budget.budget_id for budget in budgets}) != len(budgets):
                raise ValueError("risk budget ids must be unique")
            self._budgets = {budget.budget_id: budget for budget in budgets}

    def assess(self, usages: tuple[RiskUsage, ...], *, at: datetime) -> tuple[RiskDecision, tuple[RiskAllocation, ...], tuple[str, ...]]:
        with self._lock:
            allocations: list[RiskAllocation] = []
            violations: list[str] = []
            available = {budget.budget_id: budget.available for budget in self._budgets.values()}
            for usage in usages:
                matches = self._matching(usage, at)
                if not matches:
                    if not self._allow_unbudgeted and usage.amount:
                        violations.append(f"no active budget for {usage.metric}")
                    continue
                remaining = min(available[item.budget_id] for item in matches)
                if usage.amount > remaining:
                    violations.append(
                        f"{usage.metric} requires {usage.amount} but only {remaining} is available"
                    )
                approved = min(usage.amount, remaining)
                for budget in matches:
                    allocations.append(RiskAllocation(budget.budget_id, usage.metric, approved))
                    available[budget.budget_id] -= approved
            if violations:
                return RiskDecision.REJECTED, tuple(allocations), tuple(violations)
            requested = sum((usage.amount for usage in usages), Decimal("0"))
            approved = sum((item.amount for item in allocations), Decimal("0"))
            if requested and approved < requested:
                return RiskDecision.REDUCED, tuple(allocations), ()
            return RiskDecision.ALLOWED, tuple(allocations), ()

    def reserve(self, reservation: RiskReservation, *, usages: tuple[RiskUsage, ...]) -> RiskReservation:
        with self._lock:
            existing = self._reservations.get(reservation.reservation_id)
            if existing is not None:
                if existing.request_id != reservation.request_id or existing.allocations != reservation.allocations:
                    raise ValueError(f"conflicting risk reservation: {reservation.reservation_id}")
                return existing
            decision, allocations, violations = self.assess(usages, at=reservation.created_at)
            if decision is RiskDecision.REJECTED:
                raise ValueError("; ".join(violations))
            for allocation in allocations:
                budget = self._budgets[allocation.budget_id]
                self._budgets[budget.budget_id] = replace(budget, reserved=budget.reserved + allocation.amount)
            stored = replace(reservation, allocations=allocations)
            self._reservations[stored.reservation_id] = stored
            return stored

    def release(self, reservation_id: str) -> RiskReservation:
        return self._transition(reservation_id, RiskReservationStatus.RELEASED)

    def consume(self, reservation_id: str) -> RiskReservation:
        with self._lock:
            reservation = self._get_active(reservation_id)
            for allocation in reservation.allocations:
                budget = self._budgets[allocation.budget_id]
                self._budgets[budget.budget_id] = replace(
                    budget,
                    reserved=budget.reserved - allocation.amount,
                    used=budget.used + allocation.amount,
                )
            updated = replace(reservation, status=RiskReservationStatus.CONSUMED)
            self._reservations[reservation_id] = updated
            return updated

    def budgets(self) -> tuple[RiskBudget, ...]:
        with self._lock:
            return tuple(self._budgets.values())

    def reservations(self) -> tuple[RiskReservation, ...]:
        with self._lock:
            return tuple(self._reservations.values())

    def reservation(self, reservation_id: str) -> RiskReservation:
        with self._lock:
            return self._reservations[reservation_id]

    def _matching(self, usage: RiskUsage, at: datetime) -> tuple[RiskBudget, ...]:
        refs = set(usage.budgets)
        return tuple(
            budget
            for budget in self._budgets.values()
            if budget.metric is usage.metric and budget.ref in refs and budget.active_at(at)
        )

    def _transition(self, reservation_id: str, status: RiskReservationStatus) -> RiskReservation:
        with self._lock:
            reservation = self._get_active(reservation_id)
            for allocation in reservation.allocations:
                budget = self._budgets[allocation.budget_id]
                self._budgets[budget.budget_id] = replace(
                    budget,
                    reserved=budget.reserved - allocation.amount,
                )
            updated = replace(reservation, status=status)
            self._reservations[reservation_id] = updated
            return updated

    def _get_active(self, reservation_id: str) -> RiskReservation:
        reservation = self._reservations.get(reservation_id)
        if reservation is None:
            raise KeyError(reservation_id)
        if reservation.status not in {RiskReservationStatus.RESERVED, RiskReservationStatus.PARTIALLY_CONSUMED}:
            raise ValueError(f"risk reservation is not active: {reservation_id}")
        return reservation


__all__ = ["RiskBudgetLedger"]
