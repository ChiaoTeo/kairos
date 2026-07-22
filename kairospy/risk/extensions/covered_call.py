from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kairospy.identity import AccountRef, AssetId, InstrumentId
from kairospy.portfolio.ledger import Ledger, LedgerBook
from kairospy.reference import ReferenceCatalog
from kairospy.reference.access import contract_spec, definition_at
from kairospy.reference.contracts import ListedOptionSpec


@dataclass(frozen=True, slots=True)
class CoveredCallCollateralRequest:
    equity_id: InstrumentId
    option_id: InstrumentId
    contracts: Decimal

    def __post_init__(self) -> None:
        if self.contracts <= 0:
            raise ValueError("covered call collateral request requires positive contracts")


@dataclass(frozen=True, slots=True)
class CoveredCallCollateralEvidence:
    equity_id: InstrumentId
    option_id: InstrumentId
    contracts: Decimal
    required_shares: Decimal
    held_shares: Decimal
    passed: bool
    reason: str


def covered_call_collateral_evidence(
    request: CoveredCallCollateralRequest,
    account: AccountRef,
    ledger: Ledger,
    catalog: ReferenceCatalog,
    at: datetime,
) -> CoveredCallCollateralEvidence:
    definition = definition_at(catalog, request.option_id, at)
    spec = contract_spec(definition)
    if not isinstance(spec, ListedOptionSpec) or spec.underlying != request.equity_id:
        return CoveredCallCollateralEvidence(
            request.equity_id,
            request.option_id,
            request.contracts,
            Decimal("0"),
            Decimal("0"),
            False,
            "covered call option underlying mismatch",
        )
    held = ledger.book_balance(account, LedgerBook.POSITION, AssetId(f"POSITION:{request.equity_id.value}"))
    required = request.contracts * spec.multiplier
    if held < required:
        return CoveredCallCollateralEvidence(
            request.equity_id,
            request.option_id,
            request.contracts,
            required,
            held,
            False,
            "naked call prohibited",
        )
    return CoveredCallCollateralEvidence(
        request.equity_id,
        request.option_id,
        request.contracts,
        required,
        held,
        True,
        "covered call collateral requirement satisfied",
    )


def validate_covered_call_collateral(
    request: CoveredCallCollateralRequest,
    account: AccountRef,
    ledger: Ledger,
    catalog: ReferenceCatalog,
    at: datetime,
) -> CoveredCallCollateralEvidence:
    evidence = covered_call_collateral_evidence(request, account, ledger, catalog, at)
    if not evidence.passed:
        raise ValueError(
            f"{evidence.reason}: held_shares={evidence.held_shares}, required_shares={evidence.required_shares}"
        )
    return evidence
