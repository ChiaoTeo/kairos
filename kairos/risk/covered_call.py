from __future__ import annotations

from decimal import Decimal

from kairos.reference import ReferenceCatalog
from kairos.reference.access import contract_spec, definition_at
from kairos.domain.intent import CoveredCallIntent
from kairos.domain.ledger import Ledger, LedgerBook
from kairos.domain.identity import AccountKey, AssetId
from kairos.domain.product import ListedOptionSpec


def validate_covered_call(intent: CoveredCallIntent, account: AccountKey, ledger: Ledger, catalog: ReferenceCatalog, at) -> None:
    definition = definition_at(catalog, intent.option_id, at)
    spec = contract_spec(definition)
    if not isinstance(spec, ListedOptionSpec) or spec.underlying != intent.equity_id:
        raise ValueError("covered call option underlying mismatch")
    shares = ledger.book_balance(account, LedgerBook.POSITION, AssetId(f"POSITION:{intent.equity_id.value}"))
    required = intent.contracts * spec.multiplier
    if shares < required:
        raise ValueError(f"naked call prohibited: shares={shares}, required={required}")
