from __future__ import annotations

from dataclasses import dataclass
from kairospy.application.runtime.contracts import AccountCatalog, AccountRuntime, ExecutionRuntime, MarketRuntime, ReferenceRuntime


@dataclass(frozen=True, slots=True)
class RuntimeComponents:
    """Already-composed roles consumed by the runtime kernel."""

    market: MarketRuntime | None = None
    account: AccountRuntime | None = None
    account_catalog: AccountCatalog | None = None
    execution: ExecutionRuntime | None = None
    reference: ReferenceRuntime | None = None


__all__ = [
    "RuntimeComponents",
]
