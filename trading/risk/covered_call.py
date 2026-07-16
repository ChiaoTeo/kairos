from __future__ import annotations

from decimal import Decimal

from trading.catalog.service import InstrumentCatalog
from trading.domain.intent import CoveredCallIntent
from trading.domain.ledger import Ledger, LedgerBook
from trading.domain.identity import AccountKey, AssetId
from trading.domain.product import ListedOptionSpec


def validate_covered_call(intent: CoveredCallIntent, account: AccountKey, ledger: Ledger, catalog: InstrumentCatalog, at) -> None:
    definition = catalog.get(intent.option_id, at)
    spec = definition.product_spec
    if not isinstance(spec, ListedOptionSpec) or spec.underlying != intent.equity_id:
        raise ValueError("covered call option underlying mismatch")
    shares = ledger.book_balance(account, LedgerBook.POSITION, AssetId(f"POSITION:{intent.equity_id.value}"))
    required = intent.contracts * spec.multiplier
    if shares < required:
        raise ValueError(f"naked call prohibited: shares={shares}, required={required}")
