from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, TYPE_CHECKING

from kairospy.runtime.clock import Clock, SystemClock
from kairospy.infrastructure.storage.codec import to_primitive
from kairospy.portfolio.account_ports import AccountPort, AccountState
from kairospy.identity import AccountRef, AssetId, InstrumentId
from kairospy.portfolio.ledger import Ledger, LedgerBook
from kairospy.risk.strategy_positions import StrategyPositionBook

if TYPE_CHECKING:
    from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore


@dataclass(frozen=True, slots=True)
class ReconciliationDifference:
    kind: str
    key: str
    local: Decimal
    venue: Decimal


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    account: AccountRef
    checked_at: datetime
    differences: tuple[ReconciliationDifference, ...]

    @property
    def matched(self) -> bool:
        return not self.differences


def unknown_external_open_order_ids(report: ReconciliationReport | dict[str, object]) -> tuple[str, ...]:
    """Return venue-open order ids that are absent from the local durable order book."""

    if isinstance(report, ReconciliationReport):
        differences = report.differences
    else:
        raw_report = report.get("report", report)
        if not isinstance(raw_report, dict):
            return ()
        raw_differences = raw_report.get("differences", ())
        if not isinstance(raw_differences, (tuple, list)):
            return ()
        differences = tuple(raw_differences)
    ids = []
    for difference in differences:
        kind = getattr(difference, "kind", None)
        key = getattr(difference, "key", None)
        local = getattr(difference, "local", None)
        venue = getattr(difference, "venue", None)
        if isinstance(difference, dict):
            kind = difference.get("kind")
            key = difference.get("key")
            local = difference.get("local")
            venue = difference.get("venue")
        if kind == "open_order" and _decimal(local) == Decimal("0") and _decimal(venue) == Decimal("1"):
            ids.append(str(key))
    return tuple(dict.fromkeys(ids))


def reconciliation_payload(report: ReconciliationReport, run_id: str) -> dict[str, object]:
    external_open_order_ids = unknown_external_open_order_ids(report)
    return {
        "run_id": run_id,
        "phase": "matched" if report.matched else "mismatched",
        "matched": report.matched,
        "report": to_primitive(report),
        "difference_kinds": tuple(dict.fromkeys(difference.kind for difference in report.differences)),
        "unknown_external_open_order_ids": external_open_order_ids,
        "unknown_external_open_order_count": len(external_open_order_ids),
        "checked_at": report.checked_at.isoformat(),
    }


class ReconciliationService:
    def __init__(self, ledger: Ledger, account_gateway: AccountPort,
                 tolerance: Decimal = Decimal("0.00000001"), clock: Clock | None = None,
                 runtime_store: "SQLiteRuntimeStore | None" = None,
                 strategy_positions: StrategyPositionBook | None = None) -> None:
        self.ledger, self.account_gateway, self.tolerance = ledger, account_gateway, tolerance
        self.clock = clock or SystemClock()
        self.runtime_store = runtime_store
        self.strategy_positions = strategy_positions

    def reconcile(self, account: AccountRef) -> ReconciliationReport:
        venue = self.account_gateway.account_state(account)
        ledger = self.runtime_store.load_ledger() if self.runtime_store is not None else self.ledger
        local_balances = defaultdict(Decimal)
        local_positions = defaultdict(Decimal)
        for entry in ledger.entries:
            if entry.account != account:
                continue
            if entry.book in {LedgerBook.CASH, LedgerBook.AVAILABLE, LedgerBook.LOCKED, LedgerBook.MARGIN, LedgerBook.COLLATERAL, LedgerBook.BORROWED}:
                local_balances[entry.asset] += entry.amount
            elif entry.book is LedgerBook.POSITION and entry.instrument_id is not None:
                local_positions[entry.instrument_id] += entry.amount
        venue_balances = defaultdict(Decimal)
        venue_positions = defaultdict(Decimal)
        for balance in venue.balances:
            venue_balances[balance.asset] += balance.total
        for instrument_id, quantity in venue.positions:
            venue_positions[instrument_id] += quantity
        differences = []
        for asset in sorted(set(local_balances) | set(venue_balances), key=lambda item: item.value):
            if abs(local_balances[asset] - venue_balances[asset]) > self.tolerance:
                differences.append(ReconciliationDifference("balance", asset.value, local_balances[asset], venue_balances[asset]))
        for instrument in sorted(set(local_positions) | set(venue_positions), key=lambda item: item.value):
            if abs(local_positions[instrument] - venue_positions[instrument]) > self.tolerance:
                differences.append(ReconciliationDifference("position", instrument.value, local_positions[instrument], venue_positions[instrument]))
        if self.runtime_store is not None:
            local_open = set(self.runtime_store.local_open_order_ids(account))
            venue_open = set(venue.open_order_ids)
            for order_id in sorted(local_open | venue_open):
                if (order_id in local_open) != (order_id in venue_open):
                    differences.append(ReconciliationDifference(
                        "open_order", order_id,
                        Decimal(int(order_id in local_open)), Decimal(int(order_id in venue_open)),
                    ))
        strategy_positions = self.strategy_positions
        if strategy_positions is None and self.runtime_store is not None:
            strategy_positions = self.runtime_store.load_strategy_position_book(account)
        if strategy_positions is not None:
            for message in strategy_positions.reconcile(dict(local_positions)):
                instrument, values = message.split(": virtual=", 1)
                virtual, account_value = values.split(" account=", 1)
                differences.append(ReconciliationDifference(
                    "strategy_position", instrument, Decimal(virtual), Decimal(account_value),
                ))
        return ReconciliationReport(account, self.clock.now(), tuple(differences))


class ReconciliationMonitorService:
    """Managed service that periodically persists account reconciliation status."""

    STATE_KEY_PREFIX = "reconciliation"

    def __init__(
        self,
        service: ReconciliationService,
        account: AccountRef,
        store: "SQLiteRuntimeStore",
        *,
        run_id: str,
        interval_seconds: float = 5.0,
        clock: Clock | None = None,
        on_mismatch: Callable[[ReconciliationReport], None] | None = None,
    ) -> None:
        if not str(run_id).strip():
            raise ValueError("reconciliation monitor requires run_id")
        if interval_seconds <= 0:
            raise ValueError("reconciliation monitor interval must be positive")
        self.service = service
        self.account = account
        self.store = store
        self.run_id = str(run_id)
        self.interval_seconds = interval_seconds
        self.clock = clock or SystemClock()
        self.on_mismatch = on_mismatch

    @property
    def state_key(self) -> str:
        return f"{self.STATE_KEY_PREFIX}:{self.run_id}:{self.account.value}"

    def managed_service(self, name: str | None = None):
        from kairospy.runtime.service_supervisor import ManagedServiceSpec

        return ManagedServiceSpec(name or f"account-reconciliation:{self.account.value}", self.run)

    async def run(self) -> None:
        self._persist_phase("running", {"reason": "started"})
        try:
            while True:
                self.check_once()
                await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            self._persist_phase("stopped", {"reason": "service stopped"})
            raise
        except Exception as error:
            self._persist_phase("failed", {
                "error_type": type(error).__name__,
                "message": str(error),
            })
            raise

    def check_once(self) -> ReconciliationReport:
        report = self.service.reconcile(self.account)
        payload = reconciliation_payload(report, self.run_id)
        self.store.set_runtime_state(self.state_key, payload, report.checked_at)
        self.store.set_runtime_state(f"{self.STATE_KEY_PREFIX}:last", payload, report.checked_at)
        if not report.matched and self.on_mismatch is not None:
            self.on_mismatch(report)
        return report

    def _persist_phase(self, phase: str, evidence: dict[str, object]) -> None:
        at = self.clock.now()
        self.store.set_runtime_state(
            self.state_key,
            {
                "run_id": self.run_id,
                "phase": phase,
                "matched": phase == "matched",
                "updated_at": at.isoformat(),
                **evidence,
            },
            at,
        )


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None
