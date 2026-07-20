from __future__ import annotations

from decimal import Decimal

from kairospy.reference import ReferenceCatalog
from kairospy.reference.access import contract_spec, definition_at
from kairospy.domain.intent import CoveredCallIntent
from kairospy.domain.ledger import Ledger, LedgerBook
from kairospy.domain.identity import AccountKey, AssetId
from kairospy.domain.product import ListedOptionSpec


def validate_covered_call(intent: CoveredCallIntent, account: AccountKey, ledger: Ledger, catalog: ReferenceCatalog, at) -> None:
    definition = definition_at(catalog, intent.option_id, at)
    spec = contract_spec(definition)
    if not isinstance(spec, ListedOptionSpec) or spec.underlying != intent.equity_id:
        raise ValueError("covered call option underlying mismatch")
    shares = ledger.book_balance(account, LedgerBook.POSITION, AssetId(f"POSITION:{intent.equity_id.value}"))
    required = intent.contracts * spec.multiplier
    if shares < required:
        raise ValueError(f"naked call prohibited: shares={shares}, required={required}")
