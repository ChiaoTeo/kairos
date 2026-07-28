from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from kairospy.core.account import AccountContext, AccountDifference, AccountProjection, AccountSnapshot, compare_account_state
from kairospy.core.execution import ExecutionCoordinator
from kairospy.runtime import RuntimeDataEnvelope

from .bootstrap import AccountBootstrapGateway, AccountBootstrapParser, AccountBootstrapResult, bootstrap_account


AccountEventFactory = Callable[[datetime, AccountSnapshot], RuntimeDataEnvelope]


@dataclass(frozen=True, slots=True)
class AccountReconciliationResult:
    bootstrap: AccountBootstrapResult
    differences: tuple[AccountDifference, ...]
    event: RuntimeDataEnvelope


@dataclass(frozen=True, slots=True)
class AccountReconciliationService:
    account: AccountContext
    gateway: AccountBootstrapGateway
    coordinator: ExecutionCoordinator
    parser: AccountBootstrapParser
    account_event: AccountEventFactory

    def reconcile(
        self,
        *,
        previous: AccountProjection | None = None,
        symbol: str | None = None,
        at: datetime | None = None,
        balance_params: Mapping[str, object] | None = None,
        order_params: Mapping[str, object] | None = None,
    ) -> AccountReconciliationResult:
        observed_at = at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise ValueError("reconciliation timestamp must be timezone-aware")
        bootstrap = bootstrap_account(
            self.account,
            self.gateway,
            self.coordinator,
            self.parser,
            symbol=symbol,
            at=observed_at,
            balance_params=balance_params,
            order_params=order_params,
        )
        differences = () if previous is None else compare_account_state(previous, bootstrap.snapshot)
        return AccountReconciliationResult(
            bootstrap,
            differences,
            self.account_event(observed_at, bootstrap.snapshot),
        )


__all__ = ["AccountEventFactory", "AccountReconciliationResult", "AccountReconciliationService"]
