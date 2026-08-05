"""Account Actor trade leases and execution authorization."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone

from kairospy.application.support.messaging import Message
from kairospy.application.usecases.account.application.runtime import LiveAccountService, SimulatedAccountService
from kairospy.application.usecases.account.application.trading import AccountTradeAuthorizationRequest, TradingAuthorizationService
from kairospy.application.usecases.execution.application.runtime import LiveExecutionService, SimulatedExecutionRuntimeService
from kairospy.application.usecases.workspace.domain.workspace import AccountLease, AccountLeaseError, AccountLeaseManager
from kairospy.domain.account import AccountBookRef, AccountCapability, AccountContext
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
        self._contexts: dict[str, AccountContext] = {}
        self._authorization = TradingAuthorizationService()

    def acquire_available(self, accounts: tuple[AccountContext, ...]) -> None:
        for context in accounts:
            key = _account_key(context.book)
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

    def can_trade(self, account: AccountBookRef) -> bool:
        return not self.reject_reason(account)

    def reject_reason(self, account: AccountBookRef) -> str:
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

    def accounts(self) -> tuple[AccountContext, ...]: return self.port.accounts()
    def directory(self): return self.port.directory()
    def snapshot(self, account: AccountBookRef | None = None): return self.port.snapshot(account)
    def state(self, account: AccountBookRef | None = None): return self.port.state(account)
    def fees(self, account: AccountBookRef | None = None): return self.port.fees(account)
    def market_profile(self, account: AccountBookRef, market: object, *, at: datetime | None = None, refresh: bool = False): return self.port.market_profile(account, market, at=at, refresh=refresh)
    def update_market_profile(self, profile: object) -> None: self.port.update_market_profile(profile)
    def market_profiles(self, account: AccountBookRef | None = None): return self.port.market_profiles(account)

    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]:
        capabilities = tuple(self.port.capabilities(account))
        tradable = {item.book for item in capabilities if item.can_trade}
        self.authority.acquire_available(tuple(context for context in self.accounts() if context.book in tradable))
        return tuple(_authorized_capability(item, authority=self.authority) for item in capabilities)


class AuthorizingTradingExecutionService:
    def __init__(self, port: ExecutionRuntime, authority: AccountTradeAuthority) -> None:
        self.port, self.authority = port, authority
        self.coordinator = getattr(port, "coordinator", None)

    async def events(self) -> AsyncIterator[Message]:
        async for event in self.port.events():
            yield event

    def execute_intent(self, intent: TradeIntent, context: object) -> object:
        account = _resolve_intent_account(self.port, intent)
        if account is not None:
            reason = self.authority.reject_reason(account.book)
            if reason:
                _reject_intent(context, intent, reason)
                return None
        return self.port.execute_intent(intent, context)

    def plan_order(self, request: OrderRequest, *, at: datetime, **kwargs: object) -> OrderState:
        reason = self.authority.reject_reason(request.context.book)
        return _reject_order(self.coordinator, request, at=at, reason=reason) if reason else self.port.plan_order(request, at=at, **kwargs)

    def submit_order(self, order_id: str, *, at: datetime, params: Mapping[str, object] | None = None) -> OrderState:
        request = _order_request(self.coordinator, order_id)
        reason = None if request is None else self.authority.reject_reason(request.context.book)
        return _reject_existing_order(self.coordinator, order_id, at=at, reason=reason) if reason else self.port.submit_order(order_id, at=at, params=params)

    def cancel_order(self, order_id: str, *, at: datetime, params: Mapping[str, object] | None = None) -> OrderState:
        request = _order_request(self.coordinator, order_id)
        reason = None if request is None else self.authority.reject_reason(request.context.book)
        return _reject_existing_order(self.coordinator, order_id, at=at, reason=reason) if reason else self.port.cancel_order(order_id, at=at, params=params)


def _authorized_capability(capability: AccountCapability, *, authority: AccountTradeAuthority) -> AccountCapability:
    if not capability.can_trade:
        return capability
    return AccountCapability(capability.book, can_trade=authority.can_trade(capability.book), can_hold_cash=capability.can_hold_cash, can_hold_position=capability.can_hold_position, can_borrow=capability.can_borrow, can_transfer_in=capability.can_transfer_in, can_transfer_out=capability.can_transfer_out, supported_order_types=capability.supported_order_types, settlement_currencies=capability.settlement_currencies)


def _resolve_intent_account(port: ExecutionRuntime, intent: TradeIntent) -> AccountContext | None:
    resolver = getattr(port, "_resolve_account", None)
    return resolver(intent) if callable(resolver) else getattr(port, "account", None)


def _reject_intent(context: object, intent: TradeIntent, reason: str) -> None:
    intents = getattr(context, "intents", None)
    if intents is not None:
        intents.record(IntentEvent(intent.intent_id, IntentEventKind.REJECTED, getattr(context, "now", None) or datetime.now(timezone.utc), reason=reason))


def _reject_order(coordinator: object, request: OrderRequest, *, at: datetime, reason: str) -> OrderState:
    orders = getattr(coordinator, "orders")
    state = orders.plan(request)
    orders.record(OrderEvent(state.order_id, OrderEventKind.REJECTED, at, reason=reason))
    return orders.get(state.order_id)


def _reject_existing_order(coordinator: object, order_id: str, *, at: datetime, reason: str) -> OrderState:
    orders = getattr(coordinator, "orders")
    orders.record(OrderEvent(order_id, OrderEventKind.REJECTED, at, reason=reason))
    return orders.get(order_id)


def _order_request(coordinator: object, order_id: str) -> OrderRequest | None:
    try:
        return getattr(coordinator, "orders").get(order_id).request
    except Exception:
        return None


def _account_key(account: AccountBookRef) -> str:
    return ".".join(_key_part(part) for part in (account.broker, account.account_id) if part)


def _key_part(value: object) -> str:
    text = str(value).strip().lower()
    return "_".join(part for part in "".join(character if character.isalnum() else "_" for character in text).split("_") if part)


__all__ = ["AccountTradeAuthority", "AuthorizingAccountRuntime", "AuthorizingTradingExecutionService"]
