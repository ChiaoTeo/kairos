"""ExternalAccount Actor trade leases and execution authorization."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone

from kairospy.application.support.messaging import Message
from kairospy.application.usecases.account.application.runtime import LiveAccountService, SimulatedAccountService
from kairospy.application.usecases.account.application.trading import AccountTradeAuthorizationRequest, TradingAuthorizationService
from kairospy.application.usecases.execution.application.runtime import LiveExecutionService, SimulatedExecutionRuntimeService
from kairospy.application.usecases.execution.application.runtime import ExecutionCoordinator
from kairospy.application.actor.account.application.commands import IntentExecutionContext
from kairospy.application.usecases.workspace.application.leases import AccountLease, AccountLeaseError, AccountLeaseManager
from kairospy.domain.account import AccountSegment, AccountCapability, AccountRuntimeContext, AccountMarketProfile
from kairospy.domain.reference import MarketRef
from kairospy.domain.intent import IntentEvent, IntentEventKind, TradeIntent
from kairospy.domain.order import OrderEvent, OrderEventKind, OrderRequest, OrderState


AccountRuntime = LiveAccountService | SimulatedAccountService
ExecutionRuntime = LiveExecutionService | SimulatedExecutionRuntimeService


class AccountTradeAuthority:
    def __init__(self, manager: AccountLeaseManager, *, launch_id: str, launch_instance_id: str, mode: str) -> None:
        self.manager = manager
        self.launch_id = launch_id
        self.launch_instance_id = launch_instance_id
        self.mode = mode
        self._leases: dict[str, AccountLease] = {}
        self._contexts: dict[str, AccountRuntimeContext] = {}
        self._authorization = TradingAuthorizationService()

    def acquire_available(self, accounts: tuple[AccountRuntimeContext, ...]) -> None:
        for context in accounts:
            key = _account_key(context.segment)
            self._contexts[key] = context
            if self._lease_still_owned(key):
                continue
            self._leases.pop(key, None)
            try:
                self._leases[key] = self.manager.acquire(context.identity, environment=context.environment.value, launch_id=self.launch_id, launch_instance_id=self.launch_instance_id, mode=self.mode)
            except AccountLeaseError:
                continue

    def _lease_still_owned(self, key: str) -> bool:
        if key not in self._leases:
            return False
        record = self.manager.get(key)
        return record is not None and record.launch_instance_id == self.launch_instance_id and not record.stale

    def can_trade(self, account: AccountSegment) -> bool:
        return not self.reject_reason(account)

    def reject_reason(self, account: AccountSegment) -> str:
        context = self._contexts.get(_account_key(account))
        if context is not None:
            self.acquire_available((context,))
        record = self.manager.get(_account_key(account))
        return self._authorization.authorize(AccountTradeAuthorizationRequest(context or account, lock=record, lock_owned=record is not None and record.launch_instance_id == self.launch_instance_id, require_trade_lock=True)).reason

    def release(self) -> None:
        for lease in reversed(tuple(self._leases.values())):
            lease.release()
        self._leases.clear()


class AuthorizingAccountRuntime:
    def __init__(self, port: AccountRuntime, authority: AccountTradeAuthority) -> None:
        self.port, self.authority = port, authority

    async def events(self) -> AsyncIterator[Message]:
        async for event in self.port.events():
            yield event

    def accounts(self) -> tuple[AccountRuntimeContext, ...]: return self.port.accounts()
    def directory(self): return self.port.directory()
    def snapshot(self, account: AccountSegment | None = None): return self.port.snapshot(account)
    def state(self, account: AccountSegment | None = None): return self.port.state(account)
    def fees(self, account: AccountSegment | None = None): return self.port.fees(account)
    def market_profile(self, account: AccountSegment, market: MarketRef, *, at: datetime | None = None, refresh: bool = False) -> AccountMarketProfile | None: return self.port.market_profile(account, market, at=at, refresh=refresh)
    def update_market_profile(self, profile: AccountMarketProfile) -> None: self.port.update_market_profile(profile)
    def market_profiles(self, account: AccountSegment | None = None): return self.port.market_profiles(account)

    def capabilities(self, account: AccountSegment | None = None) -> tuple[AccountCapability, ...]:
        capabilities = tuple(self.port.capabilities(account))
        tradable = {item.segment for item in capabilities if item.can_trade}
        self.authority.acquire_available(tuple(context for context in self.accounts() if context.segment in tradable))
        return tuple(_authorized_capability(item, authority=self.authority) for item in capabilities)


class AuthorizingTradingExecutionService:
    def __init__(self, port: ExecutionRuntime, authority: AccountTradeAuthority) -> None:
        self.port, self.authority = port, authority
        coordinator = getattr(port, "coordinator", None)
        self.coordinator: ExecutionCoordinator | None = coordinator if isinstance(coordinator, ExecutionCoordinator) else None

    async def events(self) -> AsyncIterator[Message]:
        async for event in self.port.events():
            yield event

    def execute_intent(self, intent: TradeIntent, context: IntentExecutionContext) -> OrderState | None:
        account = _resolve_intent_account(self.port, intent)
        if account is not None:
            reason = self.authority.reject_reason(account.segment)
            if reason:
                _reject_intent(context, intent, reason)
                return None
        return self.port.execute_intent(intent, context)

    def plan_order(self, request: OrderRequest, *, at: datetime) -> OrderState:
        reason = self.authority.reject_reason(request.context.segment)
        return _reject_order(self.coordinator, request, at=at, reason=reason) if reason else self.port.plan_order(request, at=at)

    def submit_order(self, order_id: str, *, at: datetime) -> OrderState:
        request = _order_request(self.coordinator, order_id)
        reason = None if request is None else self.authority.reject_reason(request.context.segment)
        return _reject_existing_order(self.coordinator, order_id, at=at, reason=reason) if reason else self.port.submit_order(order_id, at=at)

    def cancel_order(self, order_id: str, *, at: datetime) -> OrderState:
        request = _order_request(self.coordinator, order_id)
        reason = None if request is None else self.authority.reject_reason(request.context.segment)
        return _reject_existing_order(self.coordinator, order_id, at=at, reason=reason) if reason else self.port.cancel_order(order_id, at=at)


def _authorized_capability(capability: AccountCapability, *, authority: AccountTradeAuthority) -> AccountCapability:
    if not capability.can_trade:
        return capability
    return AccountCapability(capability.segment, can_trade=authority.can_trade(capability.segment), can_hold_assets=capability.can_hold_assets, can_hold_position=capability.can_hold_position, can_borrow=capability.can_borrow, can_transfer_in=capability.can_transfer_in, can_transfer_out=capability.can_transfer_out, supported_order_types=capability.supported_order_types, settlement_assets=capability.settlement_assets)


def _resolve_intent_account(port: ExecutionRuntime, intent: TradeIntent) -> AccountRuntimeContext | None:
    resolver = getattr(port, "_resolve_account", None)
    return resolver(intent) if callable(resolver) else getattr(port, "account", None)


def _reject_intent(context: IntentExecutionContext, intent: TradeIntent, reason: str) -> None:
    intents = getattr(context, "intents", None)
    if intents is not None:
        intents.record(IntentEvent(intent.intent_id, IntentEventKind.REJECTED, getattr(context, "now", None) or datetime.now(timezone.utc), reason=reason))


def _reject_order(coordinator: ExecutionCoordinator | None, request: OrderRequest, *, at: datetime, reason: str) -> OrderState:
    if coordinator is None:
        raise RuntimeError("execution coordinator is required to reject an order")
    state = coordinator.orders.plan(request)
    coordinator.orders.record(OrderEvent(state.order_id, OrderEventKind.REJECTED, at, reason=reason))
    return coordinator.orders.get(state.order_id)


def _reject_existing_order(coordinator: ExecutionCoordinator | None, order_id: str, *, at: datetime, reason: str) -> OrderState:
    if coordinator is None:
        raise RuntimeError("execution coordinator is required to reject an order")
    coordinator.orders.record(OrderEvent(order_id, OrderEventKind.REJECTED, at, reason=reason))
    return coordinator.orders.get(order_id)


def _order_request(coordinator: ExecutionCoordinator | None, order_id: str) -> OrderRequest | None:
    if coordinator is None:
        return None
    try:
        return coordinator.orders.get(order_id).request
    except Exception:
        return None


def _account_key(account: AccountSegment) -> str:
    return ".".join(_key_part(part) for part in (account.broker, account.account_id) if part)


def _key_part(value: object) -> str:
    text = str(value).strip().lower()
    return "_".join(part for part in "".join(character if character.isalnum() else "_" for character in text).split("_") if part)


__all__ = ["AccountTradeAuthority", "AuthorizingAccountRuntime", "AuthorizingTradingExecutionService"]
