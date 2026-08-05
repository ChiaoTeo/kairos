from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from kairospy.application.support.messaging import Message
from kairospy.application.usecases.account.application.directory import AccountDirectory
from kairospy.application.usecases.account.services.service import AccountService
from kairospy.application.usecases.account.protocol import AccountLoginResult, AccountSession
from kairospy.application.usecases.account.application.read import AccountQueryRequest, AccountQueryResult, AccountRefreshRequest, AccountRefreshResult
from kairospy.application.usecases.account.domain.simulated import SimulatedAccount
from kairospy.domain.account import (
    AccountSegment,
    AccountCapability,
    AccountRuntimeContext,
    AccountEvent,
    AccountEventKind,
    AccountFeeSchedule,
    AccountLedger,
    AccountSnapshot,
    AccountSource,
    AccountState,
    derive_account_state,
)
from kairospy.domain.account import AccountMarketProfile
from kairospy.domain.reference import MarketRef


class SimulatedAccountService:
    def __init__(
        self,
        account: SimulatedAccount,
        ledger: AccountLedger,
        *,
        initialized_at: datetime | None = None,
        directory: AccountDirectory | None = None,
        capabilities: tuple[AccountCapability, ...] = (),
        fees: tuple[AccountFeeSchedule, ...] = (),
    ) -> None:
        self.account = account
        self.ledger = ledger
        self.initialized_at = initialized_at or datetime(1970, 1, 1, tzinfo=timezone.utc)
        self._directory = directory
        contexts = (account.context,) if directory is None else directory.contexts()
        self.accounts_service = AccountService(
            contexts,
            ledger=ledger,
            capabilities=capabilities,
            fees=fees,
            provision_missing_capabilities=False,
        )
        self._deposit_initial_balances()

    async def events(self) -> AsyncIterator[Message]:
        if False:
            yield

    def accounts(self) -> tuple[AccountRuntimeContext, ...]:
        return self.accounts_service.accounts()

    def login(self, account: AccountSegment | None = None, *, credential_ref: str | None = None, connection_ids: tuple[str, ...] = (), at: datetime | None = None) -> AccountLoginResult:
        return self.accounts_service.login(account, credential_ref=credential_ref, connection_ids=connection_ids, at=at)

    def logout(self, session: AccountSession) -> None:
        self.accounts_service.logout(session)

    def directory(self) -> AccountDirectory:
        return self._directory or AccountDirectory.from_contexts((self.account.context,))

    def capabilities(self, account: AccountSegment | None = None) -> tuple[AccountCapability, ...]:
        return self.accounts_service.capabilities(account)

    def fees(self, account: AccountSegment | None = None) -> tuple[AccountFeeSchedule, ...]:
        return self.accounts_service.fees(account)

    def market_profile(self, account: AccountSegment, market: MarketRef, *, at: datetime | None = None, refresh: bool = False) -> AccountMarketProfile | None:
        return self.accounts_service.market_profile(account, market, at=at, refresh=refresh)  # type: ignore[arg-type]

    def update_market_profile(self, profile: AccountMarketProfile) -> None:
        self.accounts_service.update_market_profile(profile)

    def market_profiles(self, account: AccountSegment | None = None):
        return self.accounts_service.market_profiles(account)

    def snapshot(self, account: AccountSegment | None = None) -> AccountSnapshot | None:
        context = self._context_for(account)
        if context is None:
            return None
        snapshot = self.accounts_service.snapshot(context.segment)
        if snapshot is not None:
            return snapshot
        state = self.state(account)
        if state is None:
            return None
        return AccountSnapshot(
            context,
            balances=state.balances,
            margins=state.margins,
            positions=state.positions,
            open_orders=state.open_orders,
            observed_at=state.observed_at,
            source=AccountSource.SIMULATED,
        )

    def state(self, account: AccountSegment | None = None, *, max_snapshot_age_seconds: int | None = None, now: datetime | None = None) -> AccountState | None:
        context = self._context_for(account)
        if context is None:
            return None
        snapshot = self.accounts_service.snapshot(context.segment)
        if context.segment != self.account.context.segment:
            if snapshot is not None:
                return derive_account_state(context, ledger=self.ledger, venue=snapshot, max_snapshot_age_seconds=max_snapshot_age_seconds, now=now)
            return AccountState(context, (), (), (), (), self.initialized_at, AccountSource.SIMULATED)
        return derive_account_state(self.account.context, ledger=self.ledger, venue=snapshot, max_snapshot_age_seconds=max_snapshot_age_seconds, now=now)

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        if snapshot.context not in self.accounts():
            raise ValueError("simulated account snapshot context does not match service account directory")
        self.accounts_service.update_snapshot(snapshot)

    def query(self, request: AccountQueryRequest) -> AccountQueryResult:
        context = self._context_for(request.account)
        if context is None:
            raise ValueError(f"account is not configured: {request.account}")
        return self.accounts_service.query(request)

    def refresh_account(self, request: AccountRefreshRequest) -> AccountRefreshResult:
        context = self._context_for(request.account)
        if context is None:
            raise ValueError(f"account is not configured: {request.account}")
        return self.accounts_service.refresh(request)

    def _context_for(self, account: AccountSegment | None) -> AccountRuntimeContext | None:
        if account is None:
            return self.account.context
        if account == self.account.context.segment:
            return self.account.context
        for context in self.accounts():
            if context.segment == account:
                return context
        return None

    def _deposit_initial_balances(self) -> None:
        existing = self.ledger.balances(self.account.context.segment)
        for balance in self.account.initial_balances:
            if balance.quantity == 0 or existing.get(balance.asset, Decimal("0")):
                continue
            self.ledger.record(
                AccountEvent(
                    uuid4(),
                    self.account.context.segment,
                    AccountEventKind.DEPOSIT,
                    self.initialized_at,
                    balance.asset,
                    balance_delta=balance.quantity,
                    reference_id=f"initial_balance:{balance.asset}",
                )
            )


__all__ = ["SimulatedAccountService"]
