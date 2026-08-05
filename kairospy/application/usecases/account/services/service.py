from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from kairospy.application.usecases.account.protocol import AccountQueryRequest, AccountRefreshRequest, AccountReadPort
from kairospy.application.usecases.account.services.read import (
    AccountQueryResult,
    AccountReadResult,
    AccountReadService,
    AccountRefreshResult,
    query_account,
)
from kairospy.application.usecases.account.protocol import AccountLoginPort, AccountLoginRequest, AccountLoginResult, AccountMarketProfilePort, AccountSession
from kairospy.application.usecases.account.services.market_profiles import AccountMarketProfileService
from kairospy.application.usecases.account.services.provisioning import AccountProvisioningService
from kairospy.application.usecases.account.services.queries import AccountViewQueryService
from kairospy.application.usecases.account.application.reconciliation import AccountEventFactory, AccountReconciliationResult
from kairospy.application.usecases.account.services.reconciliation import AccountReconciliationService
from kairospy.application.usecases.account.application.snapshots import AccountSnapshotService, AccountSnapshotStore
from kairospy.application.usecases.account.domain.segments import default_account_segments
from kairospy.application.usecases.account.domain.private_stream import PrivateStreamCheckpoint
from kairospy.application.usecases.account.domain.routing import AccountSegmentRoute, account_segment_route
from kairospy.application.usecases.account.domain.simulated import SimulatedAccount
from kairospy.domain.account import AccountSegment, AccountCapability, AccountRuntimeContext, AccountFeeSchedule, AccountLedger, AccountMarketProfile, AccountSnapshot, AccountState, derive_account_state
from kairospy.domain.reference import MarketRef


@dataclass(slots=True)
class AccountService:
    """Application facade for account use cases.

    This service is the account module's public business API. Runtime adapters
    may compose it, but it intentionally has no dependency on runtime envelopes
    or runtime protocols.
    """

    contexts: tuple[AccountRuntimeContext, ...]
    ledger: AccountLedger | None = None
    account_reader: AccountReadPort | None = None
    login_port: AccountLoginPort | None = None
    snapshot_store: AccountSnapshotStore | None = None
    capability_items: tuple[AccountCapability, ...] = ()
    fee_items: tuple[AccountFeeSchedule, ...] = ()
    account_event: AccountEventFactory | None = None
    provision_missing_capabilities: bool = True
    market_profile_port: AccountMarketProfilePort | None = None
    _snapshots: dict[AccountSegment, AccountSnapshot] = field(default_factory=dict, init=False, repr=False)
    _market_profile_service: AccountMarketProfileService | None = field(default=None, init=False, repr=False)
    _market_profiles: dict[tuple[AccountSegment, str], AccountMarketProfile] = field(default_factory=dict, init=False, repr=False)

    def __init__(
        self,
        contexts: Iterable[AccountRuntimeContext] | AccountRuntimeContext,
        *,
        ledger: AccountLedger | None = None,
        account_reader: AccountReadPort | None = None,
        login_port: AccountLoginPort | None = None,
        snapshot_store: AccountSnapshotStore | None = None,
        capabilities: Iterable[AccountCapability] = (),
        fees: Iterable[AccountFeeSchedule] = (),
        snapshots: Iterable[AccountSnapshot] = (),
        account_event: AccountEventFactory | None = None,
        provision_missing_capabilities: bool = True,
        market_profile_port: AccountMarketProfilePort | None = None,
    ) -> None:
        resolved_contexts = (contexts,) if isinstance(contexts, AccountRuntimeContext) else tuple(contexts)
        if not resolved_contexts:
            raise ValueError("account service requires at least one account context")
        self.contexts = resolved_contexts
        self.ledger = ledger
        self.account_reader = account_reader
        self.login_port = login_port
        self.snapshot_store = snapshot_store
        self.capability_items = tuple(capabilities)
        self.fee_items = tuple(fees)
        self.account_event = account_event
        self.provision_missing_capabilities = provision_missing_capabilities
        self.market_profile_port = market_profile_port
        self._market_profile_service = None if market_profile_port is None else AccountMarketProfileService(market_profile_port)
        self._snapshots = {}
        self._market_profiles = {}
        for snapshot in snapshots:
            self.update_snapshot(snapshot)

    def accounts(self) -> tuple[AccountRuntimeContext, ...]:
        return self.contexts

    def login(
        self,
        account: AccountSegment | None = None,
        *,
        credential_ref: str | None = None,
        connection_ids: tuple[str, ...] = (),
        at: datetime | None = None,
    ) -> AccountLoginResult:
        context = self._require_context(account)
        if self.login_port is None:
            session = AccountSession(
                session_id=f"local.{context.segment.value}",
                account=context.segment,
                connection_ids=connection_ids,
                logged_in_at=at or datetime.now(timezone.utc),
            )
            return AccountLoginResult(session)
        result = self.login_port.login(
            AccountLoginRequest(
                context=context,
                credential_ref=credential_ref,
                connection_ids=connection_ids,
                observed_at=at or datetime.now(timezone.utc),
            )
        )
        if result.snapshot is not None:
            self.update_snapshot(result.snapshot)
        return result

    def logout(self, session: AccountSession) -> None:
        if self.login_port is not None:
            self.login_port.logout(session)

    def capabilities(self, account: AccountSegment | None = None) -> tuple[AccountCapability, ...]:
        capabilities = self.capability_items
        if not capabilities and self.provision_missing_capabilities:
            capabilities = tuple(AccountProvisioningService().capability(context.segment) for context in self.contexts)
        if account is None:
            return capabilities
        return tuple(item for item in capabilities if item.segment == account)

    def fees(self, account: AccountSegment | None = None) -> tuple[AccountFeeSchedule, ...]:
        if account is None:
            return self.fee_items
        return tuple(item for item in self.fee_items if item.segment == account)

    def market_profile(self, account: AccountSegment, market: MarketRef, *, at: datetime | None = None, refresh: bool = False) -> AccountMarketProfile | None:
        key = (account, str(market.market_id))
        if not refresh and key in self._market_profiles:
            return self._market_profiles[key]
        context = self._context_for(account)
        if context is None:
            raise KeyError(f"unknown account segment: {account.value}")
        if self._market_profile_service is None:
            return self._market_profiles.get(key)
        profile = self._market_profile_service.read(context, market, at=at)
        self._market_profiles[key] = profile
        return profile

    def update_market_profile(self, profile: AccountMarketProfile) -> None:
        if profile.account not in self.contexts:
            raise ValueError("account market profile context does not belong to account service")
        self._market_profiles[(profile.account.segment, str(profile.market.market_id))] = profile

    def market_profiles(self, account: AccountSegment | None = None) -> tuple[AccountMarketProfile, ...]:
        values = tuple(self._market_profiles.values())
        return values if account is None else tuple(item for item in values if item.account.segment == account)

    def snapshot(self, account: AccountSegment | None = None) -> AccountSnapshot | None:
        context = self._context_for(account)
        if context is None:
            return None
        return self._snapshots.get(context.segment)

    def state(
        self,
        account: AccountSegment | None = None,
        *,
        max_snapshot_age_seconds: int | None = None,
        now: datetime | None = None,
    ) -> AccountState | None:
        context = self._context_for(account)
        if context is None:
            return None
        snapshot = self._snapshots.get(context.segment)
        if self.ledger is not None:
            return derive_account_state(
                context,
                ledger=self.ledger,
                venue=snapshot,
                max_snapshot_age_seconds=max_snapshot_age_seconds,
                now=now,
            )
        if snapshot is None:
            return None
        return derive_account_state(
            context,
            venue=snapshot,
            max_snapshot_age_seconds=max_snapshot_age_seconds,
            now=now,
        )

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        if snapshot.context not in self.contexts:
            raise ValueError("account snapshot context does not belong to account service")
        self._snapshots[snapshot.context.segment] = snapshot
        snapshots = AccountSnapshotService.from_store(self.snapshot_store)
        if snapshots is not None:
            snapshots.apply(snapshot)

    def read(
        self,
        account: AccountSegment | None = None,
        *,
        symbol: str | None = None,
        at: datetime | None = None,
        fetch_orders: bool = True,
    ) -> AccountReadResult:
        if self.account_reader is None:
            raise RuntimeError("account service requires an account reader")
        context = self._require_context(account)
        result = AccountReadService(self.account_reader).read(
            context,
            symbol=symbol,
            at=at or datetime.now(timezone.utc),
            fetch_orders=fetch_orders,
        )
        self.update_snapshot(result.snapshot)
        return result

    def query(self, request: AccountQueryRequest) -> AccountQueryResult:
        context = self._require_context(request.account)
        return query_account(self, context, request)

    def refresh(self, request: AccountRefreshRequest) -> AccountRefreshResult:
        if self.account_reader is None:
            context = self._require_context(request.account)
            snapshot = self.snapshot(context.segment)
            if snapshot is None:
                raise RuntimeError("account service has no refresh port or cached snapshot")
            return AccountRefreshResult(AccountReadResult(snapshot, derive_account_state(context, venue=snapshot)))
        result = self.read(
            request.account,
            symbol=request.symbol,
            at=request.at,
            fetch_orders=request.fetch_orders,
        )
        return AccountRefreshResult(result)

    def reconcile(
        self,
        account: AccountSegment | None = None,
        *,
        previous: AccountState | None = None,
        symbol: str | None = None,
        at: datetime | None = None,
    ) -> AccountReconciliationResult:
        if self.account_reader is None:
            raise RuntimeError("account service requires an account reader to reconcile")
        if self.account_event is None:
            raise RuntimeError("account service requires an account event factory to reconcile")
        result = AccountReconciliationService(
            self._require_context(account),
            self.account_reader,
            self.account_event,
        ).reconcile(
            previous=previous,
            symbol=symbol,
            at=at,
        )
        self.update_snapshot(result.read.snapshot)
        return result

    def _context_for(self, account: AccountSegment | None = None) -> AccountRuntimeContext | None:
        if account is None:
            return self.contexts[0]
        for context in self.contexts:
            if context.segment == account:
                return context
        return None

    def _require_context(self, account: AccountSegment | None = None) -> AccountRuntimeContext:
        context = self._context_for(account)
        if context is None:
            raise ValueError(f"unknown account: {account}")
        return context


__all__ = [
    "AccountSegmentRoute",
    "AccountReadResult",
    "AccountLoginResult",
    "AccountSession",
    "AccountProvisioningService",
    "AccountViewQueryService",
    "AccountService",
    "AccountSnapshotService",
    "AccountSnapshotStore",
    "PrivateStreamCheckpoint",
    "SimulatedAccount",
    "account_segment_route",
    "default_account_segments",
]
