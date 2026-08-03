from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

from kairospy.application.usecases.account.services.authorization import (
    AccountAuthorizationService,
    AccountTradeAuthorizationRequest,
    AccountTradeAuthorizationResult,
    trade_lock_state,
)
from kairospy.application.usecases.account.services.bootstrap import AccountBootstrapRequest, AccountBootstrapResult, AccountBootstrapService
from kairospy.application.usecases.account.services.provisioning import AccountProvisioningService
from kairospy.application.usecases.account.services.queries import AccountQueryService
from kairospy.application.usecases.account.services.reconciliation import AccountEventFactory, AccountReconciliationResult, AccountReconciliationService
from kairospy.application.usecases.account.services.snapshots import AccountSnapshotService, AccountSnapshotStore
from kairospy.application.usecases.account.domain.books import default_account_books
from kairospy.application.usecases.account.domain.private_stream import PrivateStreamCheckpoint
from kairospy.application.usecases.account.domain.routing import AccountBookRoute, account_book_route
from kairospy.application.usecases.account.domain.simulated import SimulatedAccount
from kairospy.domain.account import AccountBookRef, AccountCapability, AccountContext, AccountFeeSchedule, AccountSnapshot, AccountState, derive_account_state


@dataclass(slots=True)
class AccountService:
    """Application facade for account use cases.

    This service is the account module's public business API. Runtime adapters
    may compose it, but it intentionally has no dependency on runtime envelopes
    or runtime protocols.
    """

    contexts: tuple[AccountContext, ...]
    coordinator: object | None = None
    bootstrap_gateway: object | None = None
    snapshot_store: AccountSnapshotStore | None = None
    capability_items: tuple[AccountCapability, ...] = ()
    fee_items: tuple[AccountFeeSchedule, ...] = ()
    account_event: AccountEventFactory | None = None
    provision_missing_capabilities: bool = True
    _snapshots: dict[AccountBookRef, AccountSnapshot] = field(default_factory=dict, init=False, repr=False)

    def __init__(
        self,
        contexts: Iterable[AccountContext] | AccountContext,
        *,
        coordinator: object | None = None,
        bootstrap_gateway: object | None = None,
        snapshot_store: AccountSnapshotStore | None = None,
        capabilities: Iterable[AccountCapability] = (),
        fees: Iterable[AccountFeeSchedule] = (),
        snapshots: Iterable[AccountSnapshot] = (),
        account_event: AccountEventFactory | None = None,
        provision_missing_capabilities: bool = True,
    ) -> None:
        resolved_contexts = (contexts,) if isinstance(contexts, AccountContext) else tuple(contexts)
        if not resolved_contexts:
            raise ValueError("account service requires at least one account context")
        self.contexts = resolved_contexts
        self.coordinator = coordinator
        self.bootstrap_gateway = bootstrap_gateway
        self.snapshot_store = snapshot_store
        self.capability_items = tuple(capabilities)
        self.fee_items = tuple(fees)
        self.account_event = account_event
        self.provision_missing_capabilities = provision_missing_capabilities
        self._snapshots = {}
        for snapshot in snapshots:
            self.update_snapshot(snapshot)

    def accounts(self) -> tuple[AccountContext, ...]:
        return self.contexts

    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]:
        capabilities = self.capability_items
        if not capabilities and self.provision_missing_capabilities:
            capabilities = tuple(AccountProvisioningService().capability(context.book) for context in self.contexts)
        if account is None:
            return capabilities
        return tuple(item for item in capabilities if item.book == account)

    def fees(self, account: AccountBookRef | None = None) -> tuple[AccountFeeSchedule, ...]:
        if account is None:
            return self.fee_items
        return tuple(item for item in self.fee_items if item.book == account)

    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None:
        context = self._context_for(account)
        if context is None:
            return None
        return self._snapshots.get(context.book)

    def state(self, account: AccountBookRef | None = None) -> AccountState | None:
        context = self._context_for(account)
        if context is None:
            return None
        snapshot = self._snapshots.get(context.book)
        if self.coordinator is not None and callable(getattr(self.coordinator, "account_projection", None)):
            return self.coordinator.account_projection(context, venue_snapshot=snapshot)
        if snapshot is None:
            return None
        return derive_account_state(context, venue=snapshot)

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        if snapshot.context not in self.contexts:
            raise ValueError("account snapshot context does not belong to account service")
        self._snapshots[snapshot.context.book] = snapshot
        snapshots = AccountSnapshotService.from_store(self.snapshot_store)
        if snapshots is not None:
            snapshots.apply(snapshot)

    def refresh(
        self,
        account: AccountBookRef | None = None,
        *,
        symbol: str | None = None,
        at: datetime | None = None,
        balance_params: Mapping[str, object] | None = None,
        order_params: Mapping[str, object] | None = None,
        fetch_orders: bool = True,
    ) -> AccountBootstrapResult:
        if self.bootstrap_gateway is None:
            raise RuntimeError("account service requires a bootstrap gateway to refresh")
        if self.coordinator is None:
            raise RuntimeError("account service requires a coordinator to refresh")
        context = self._require_context(account)
        result = AccountBootstrapService(self.bootstrap_gateway, self.coordinator).bootstrap(
            context,
            symbol=symbol,
            at=at or datetime.now(timezone.utc),
            balance_params=balance_params,
            order_params=order_params,
            fetch_orders=fetch_orders,
        )
        self.update_snapshot(result.snapshot)
        return result

    def reconcile(
        self,
        account: AccountBookRef | None = None,
        *,
        previous: AccountState | None = None,
        symbol: str | None = None,
        at: datetime | None = None,
        balance_params: Mapping[str, object] | None = None,
        order_params: Mapping[str, object] | None = None,
    ) -> AccountReconciliationResult:
        if self.bootstrap_gateway is None:
            raise RuntimeError("account service requires a bootstrap gateway to reconcile")
        if self.coordinator is None:
            raise RuntimeError("account service requires a coordinator to reconcile")
        if self.account_event is None:
            raise RuntimeError("account service requires an account event factory to reconcile")
        result = AccountReconciliationService(
            self._require_context(account),
            self.bootstrap_gateway,
            self.coordinator,
            self.account_event,
        ).reconcile(
            previous=previous,
            symbol=symbol,
            at=at,
            balance_params=balance_params,
            order_params=order_params,
        )
        self.update_snapshot(result.bootstrap.snapshot)
        return result

    def authorize_trade(self, request: AccountTradeAuthorizationRequest) -> AccountTradeAuthorizationResult:
        return AccountAuthorizationService(str(request.book.broker)).authorize_trade(request)

    def _context_for(self, account: AccountBookRef | None = None) -> AccountContext | None:
        if account is None:
            return self.contexts[0]
        for context in self.contexts:
            if context.book == account:
                return context
        return None

    def _require_context(self, account: AccountBookRef | None = None) -> AccountContext:
        context = self._context_for(account)
        if context is None:
            raise ValueError(f"unknown account: {account}")
        return context


__all__ = [
    "AccountBookRoute",
    "AccountBootstrapRequest",
    "AccountAuthorizationService",
    "AccountProvisioningService",
    "AccountQueryService",
    "AccountService",
    "AccountSnapshotService",
    "AccountSnapshotStore",
    "AccountTradeAuthorizationRequest",
    "AccountTradeAuthorizationResult",
    "PrivateStreamCheckpoint",
    "SimulatedAccount",
    "account_book_route",
    "default_account_books",
    "trade_lock_state",
]
