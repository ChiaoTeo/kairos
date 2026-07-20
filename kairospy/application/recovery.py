from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable, Mapping, Protocol

from kairospy.accounting.conversion import AssetConversionGraph
from kairospy.accounting.portfolio import PortfolioSnapshot, Portfolio
from kairospy.ports import AccountPort
from kairospy.application.clock import FixedClock
from kairospy.reference import ReferenceCatalog
from kairospy.reference.access import contract_spec, definition_at
from kairospy.domain.identity import AccountKey, AssetId, InstrumentId
from kairospy.domain.ledger import Ledger
from kairospy.domain.product import FutureSpec, is_option_spec
from kairospy.orchestration.reconciliation import ReconciliationReport, ReconciliationService
from kairospy.orchestration.runtime_store import SQLiteRuntimeStore
from kairospy.risk.view import UnifiedRiskView, build_risk_view
from kairospy.storage.codec import to_primitive


@dataclass(frozen=True, slots=True)
class RuntimeRecoveryResult:
    recovered_at: datetime
    ledger: Ledger
    portfolio: PortfolioSnapshot
    risk: UnifiedRiskView
    reconciliations: tuple[ReconciliationReport, ...]
    ready: bool
    reason: str


class RuntimeRecovery(Protocol):
    def recover(self, at: datetime) -> RuntimeRecoveryResult: ...


class RuntimeRecoveryService:
    """Rebuild runtime projections from durable facts and reconcile external state."""

    STATE_KEY = "runtime_recovery"

    def __init__(
        self,
        store: SQLiteRuntimeStore,
        catalog: ReferenceCatalog,
        reporting_asset: AssetId,
        account_gateways: Mapping[AccountKey, AccountPort],
        *,
        marks: Mapping[InstrumentId, Decimal] | Callable[[datetime], Mapping[InstrumentId, Decimal]] = (),
        conversions: AssetConversionGraph | Callable[[datetime], AssetConversionGraph] | None = None,
        reconciliation_tolerance: Decimal = Decimal("0.00000001"),
        maximum_conversion_age: timedelta = timedelta(minutes=5),
    ) -> None:
        if reconciliation_tolerance < 0:
            raise ValueError("reconciliation tolerance cannot be negative")
        if maximum_conversion_age <= timedelta(0):
            raise ValueError("maximum conversion age must be positive")
        self.store = store
        self.catalog = catalog
        self.reporting_asset = reporting_asset
        self.account_gateways = dict(account_gateways)
        self.marks = marks
        self.conversions = conversions
        self.reconciliation_tolerance = reconciliation_tolerance
        self.maximum_conversion_age = maximum_conversion_age

    def recover(self, at: datetime) -> RuntimeRecoveryResult:
        if at.tzinfo is None:
            raise ValueError("runtime recovery time must be timezone-aware")
        ledger = self.store.load_ledger()
        marks = dict(self.marks(at) if callable(self.marks) else self.marks)
        conversions = self.conversions(at) if callable(self.conversions) else self.conversions
        conversions = conversions or AssetConversionGraph()
        portfolio = Portfolio(ledger, self.catalog, self.reporting_asset).snapshot(
            at,
            marks,
            conversions,
            max_conversion_age=self.maximum_conversion_age,
        )
        risk = build_risk_view(portfolio, self.catalog)
        reports = tuple(
            ReconciliationService(
                ledger,
                gateway,
                tolerance=self.reconciliation_tolerance,
                clock=FixedClock(at),
                runtime_store=self.store,
            ).reconcile(account)
            for account, gateway in sorted(self.account_gateways.items(), key=lambda item: item[0].value)
        )
        reasons = []
        if portfolio.status != "complete":
            reasons.append(
                "portfolio valuation incomplete: "
                f"assets={','.join(portfolio.unpriced_assets) or '-'};"
                f"positions={','.join(portfolio.unpriced_positions) or '-'}"
            )
        expired_positions = []
        for position in portfolio.positions:
            definition = definition_at(self.catalog, position.instrument_id, at)
            spec = contract_spec(definition)
            expiry = spec.expiry if is_option_spec(spec) or isinstance(spec, FutureSpec) else None
            if expiry is not None and at >= expiry and position.quantity:
                expired_positions.append(position.instrument_id.value)
        if expired_positions:
            reasons.append("expired positions require durable settlement: " + ",".join(sorted(expired_positions)))
        mismatches = tuple(report for report in reports if not report.matched)
        if mismatches:
            reasons.append("reconciliation mismatches: " + ",".join(
                f"{report.account.value}={len(report.differences)}" for report in mismatches
            ))
        ready = not reasons
        reason = "recovery projections rebuilt and reconciliation matched" if ready else "; ".join(reasons)
        result = RuntimeRecoveryResult(at, ledger, portfolio, risk, reports, ready, reason)
        self.store.set_runtime_state(self.STATE_KEY, {
            "recovered_at": at,
            "ready": ready,
            "reason": reason,
            "ledger_transaction_count": len(ledger.transactions),
            "ledger_entry_count": len(ledger.entries),
            "portfolio": to_primitive(portfolio),
            "risk": to_primitive(risk),
            "reconciliations": to_primitive(reports),
        }, at)
        return result
