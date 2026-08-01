from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone
from typing import Protocol

from kairospy.application.support.runtime.contracts import AccountCatalog, AccountRuntime, ExecutionRuntime
from kairospy.application.support.system.workspace import AccountLease, AccountLeaseError, AccountLeaseManager
from kairospy.core.account import AccountBookRef, AccountCapability, AccountContext, AccountSnapshot, AccountState
from kairospy.core.intent import IntentEvent, IntentEventKind, TradeIntent
from kairospy.core.order import OrderEvent, OrderEventKind, OrderRequest, OrderState
from kairospy.application.support.runtime.events import RuntimeEnvelope


class _AuthorizableExecutionRuntime(ExecutionRuntime, Protocol):
    def plan_order(self, request: OrderRequest, **kwargs: object) -> OrderState:
        ...

    def submit_order(self, order_id: str, *, at: datetime, params: Mapping[str, object] | None = None) -> OrderState:
        ...

    def cancel_order(self, order_id: str, *, at: datetime, params: Mapping[str, object] | None = None) -> OrderState:
        ...


class AccountTradeAuthority:
    def __init__(
        self,
        manager: AccountLeaseManager,
        *,
        launch_id: str,
        launch_instance_id: str,
        mode: str,
    ) -> None:
        self.manager = manager
        self.launch_id = launch_id
        self.launch_instance_id = launch_instance_id
        self.mode = mode
        self._leases: dict[str, AccountLease] = {}
        self._contexts: dict[str, AccountContext] = {}

    def acquire_available(self, accounts: tuple[AccountContext, ...]) -> None:
        for context in accounts:
            key = _account_key(context.book)
            self._contexts[key] = context
            if self._lease_still_owned(key):
                continue
            self._leases.pop(key, None)
            try:
                self._leases[key] = self.manager.acquire(
                    context.identity,
                    environment=context.environment.value,
                    launch_id=self.launch_id,
                    launch_instance_id=self.launch_instance_id,
                    mode=self.mode,
                )
            except AccountLeaseError:
                continue

    def _lease_still_owned(self, key: str) -> bool:
        if key not in self._leases:
            return False
        record = self.manager.get(key)
        return record is not None and record.launch_instance_id == self.launch_instance_id and not record.stale

    def can_trade(self, account: AccountBookRef) -> bool:
        context = self._contexts.get(_account_key(account))
        if context is not None:
            self.acquire_available((context,))
        record = self.manager.get(_account_key(account))
        return record is not None and record.launch_instance_id == self.launch_instance_id and not record.stale

    def reject_reason(self, account: AccountBookRef) -> str:
        if self.can_trade(account):
            return ""
        record = self.manager.get(_account_key(account))
        if record is None:
            return f"account {account.identity.value} has no trade lock"
        return f"account {account.identity.value} trading is locked by {record.launch_id} ({record.launch_instance_id})"

    def release(self) -> None:
        for lease in reversed(tuple(self._leases.values())):
            lease.release()
        self._leases.clear()


class AuthorizingAccountRuntime(AccountRuntime, AccountCatalog):
    def __init__(self, port: AccountRuntime, authority: AccountTradeAuthority) -> None:
        self.port = port
        self.authority = authority

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        async for event in self.port.events():
            yield event

    def accounts(self) -> tuple[AccountContext, ...]:
        return self.port.accounts()

    def directory(self):
        return self.port.directory()

    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None:
        return self.port.snapshot(account)

    def state(self, account: AccountBookRef | None = None) -> AccountState | None:
        return self.port.state(account)

    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]:
        capabilities = tuple(self.port.capabilities(account))
        tradable_books = {capability.book for capability in capabilities if capability.can_trade}
        self.authority.acquire_available(tuple(context for context in self.accounts() if context.book in tradable_books))
        return tuple(_authorized_capability(item, authority=self.authority) for item in capabilities)

    def fees(self, account: AccountBookRef | None = None):
        return self.port.fees(account)


class AuthorizingTradingExecutionService:
    def __init__(self, port: _AuthorizableExecutionRuntime, authority: AccountTradeAuthority) -> None:
        self.port = port
        self.authority = authority
        self.coordinator = getattr(port, "coordinator", None)

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        async for event in self.port.events():
            yield event

    def submit_intent(self, intent: TradeIntent, context: object) -> object:
        account = _resolve_intent_account(self.port, intent)
        if account is not None:
            reason = self.authority.reject_reason(account.book)
            if reason:
                _reject_intent(context, intent, reason)
                return None
        submit_intent = getattr(self.port, "submit_intent", None)
        if not callable(submit_intent):
            raise RuntimeError("authorized trading execution port has no intent executor")
        return submit_intent(intent, context)

    def plan_order(
        self,
        request: OrderRequest,
        *,
        reserve_currency: str | None = None,
        reserve_amount=None,
        margin_notional=None,
        margin_leverage=None,
        margin_instrument_id: str | None = None,
        venue_snapshot: AccountSnapshot | None = None,
        at: datetime,
    ) -> OrderState:
        reason = self.authority.reject_reason(request.context.book)
        if reason:
            return _reject_order(self.coordinator, request, at=at, reason=reason)
        kwargs = {
            "reserve_currency": reserve_currency,
            "reserve_amount": reserve_amount,
            "margin_notional": margin_notional,
            "margin_instrument_id": margin_instrument_id,
            "venue_snapshot": venue_snapshot,
            "at": at,
        }
        if margin_leverage is not None:
            kwargs["margin_leverage"] = margin_leverage
        return self.port.plan_order(request, **kwargs)

    def submit_order(self, order_id: str, *, at: datetime, params: Mapping[str, object] | None = None) -> OrderState:
        request = _order_request(self.coordinator, order_id)
        if request is not None:
            reason = self.authority.reject_reason(request.context.book)
            if reason:
                return _reject_existing_order(self.coordinator, order_id, at=at, reason=reason)
        return self.port.submit_order(order_id, at=at, params=params)

    def cancel_order(self, order_id: str, *, at: datetime, params: Mapping[str, object] | None = None) -> OrderState:
        request = _order_request(self.coordinator, order_id)
        if request is not None:
            reason = self.authority.reject_reason(request.context.book)
            if reason:
                return _reject_existing_order(self.coordinator, order_id, at=at, reason=reason)
        return self.port.cancel_order(order_id, at=at, params=params)


def _authorized_capability(capability: AccountCapability, *, authority: AccountTradeAuthority) -> AccountCapability:
    if not capability.can_trade:
        return capability
    return AccountCapability(
        capability.book,
        can_trade=authority.can_trade(capability.book),
        can_hold_cash=capability.can_hold_cash,
        can_hold_position=capability.can_hold_position,
        can_borrow=capability.can_borrow,
        can_transfer_in=capability.can_transfer_in,
        can_transfer_out=capability.can_transfer_out,
        supported_order_types=capability.supported_order_types,
        settlement_currencies=capability.settlement_currencies,
    )


def _resolve_intent_account(port: _AuthorizableExecutionRuntime, intent: TradeIntent) -> AccountContext | None:
    resolver = getattr(port, "_resolve_account", None)
    if callable(resolver):
        return resolver(intent)
    return getattr(port, "account", None)


def _reject_intent(context: object, intent: TradeIntent, reason: str) -> None:
    now = getattr(context, "now", None) or datetime.now(timezone.utc)
    intents = getattr(context, "intents", None)
    if intents is not None:
        intents.record(IntentEvent(intent.intent_id, IntentEventKind.REJECTED, now, reason=reason))


def _reject_order(coordinator: object, request: OrderRequest, *, at: datetime, reason: str) -> OrderState:
    orders = getattr(coordinator, "orders", None)
    state = orders.plan(request)
    orders.record(OrderEvent(state.order_id, OrderEventKind.REJECTED, at, reason=reason))
    return orders.get(state.order_id)


def _reject_existing_order(coordinator: object, order_id: str, *, at: datetime, reason: str) -> OrderState:
    orders = getattr(coordinator, "orders", None)
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
    return "_".join(part for part in ("".join(character if character.isalnum() else "_" for character in text)).split("_") if part)


__all__ = ["AccountTradeAuthority", "AuthorizingAccountRuntime", "AuthorizingTradingExecutionService"]
