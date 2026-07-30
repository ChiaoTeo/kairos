from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone

from kairospy.application.ports import AccountPort, TradingExecutionPort
from kairospy.application.system.workspace import AccountLease, AccountLeaseError, AccountLeaseManager
from kairospy.core.account import AccountCapability, AccountContext, AccountRef, AccountSnapshot, AccountState
from kairospy.core.intent import IntentEvent, IntentEventKind, TradeIntent
from kairospy.core.order import OrderEvent, OrderEventKind, OrderRequest, OrderState
from kairospy.application.protocol import RuntimeEnvelope


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
            key = _account_key(context.account)
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

    def can_trade(self, account: AccountRef) -> bool:
        context = self._contexts.get(_account_key(account))
        if context is not None:
            self.acquire_available((context,))
        record = self.manager.get(_account_key(account))
        return record is not None and record.launch_instance_id == self.launch_instance_id and not record.stale

    def reject_reason(self, account: AccountRef) -> str:
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


class AuthorizingAccountPort(AccountPort):
    def __init__(self, port: AccountPort, authority: AccountTradeAuthority) -> None:
        self.port = port
        self.authority = authority

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        async for event in self.port.events():
            yield event

    def accounts(self) -> tuple[AccountContext, ...]:
        return self.port.accounts()

    def directory(self):
        return self.port.directory()

    def snapshot(self, account: AccountRef | None = None) -> AccountSnapshot | None:
        return self.port.snapshot(account)

    def state(self, account: AccountRef | None = None) -> AccountState | None:
        return self.port.state(account)

    def capabilities(self, account: AccountRef | None = None) -> tuple[AccountCapability, ...]:
        self.authority.acquire_available(self.accounts())
        return tuple(_authorized_capability(item, authority=self.authority) for item in self.port.capabilities(account))

    def fees(self, account: AccountRef | None = None):
        return self.port.fees(account)


class AuthorizingTradingExecutionService(TradingExecutionPort):
    def __init__(self, port: TradingExecutionPort, authority: AccountTradeAuthority) -> None:
        self.port = port
        self.authority = authority
        self.coordinator = getattr(port, "coordinator", None)

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        async for event in self.port.events():
            yield event

    def execute_intent(self, intent: TradeIntent, context: object, *, hook: str = "") -> object:
        account = _resolve_intent_account(self.port, intent)
        if account is not None:
            reason = self.authority.reject_reason(account.account)
            if reason:
                _reject_intent(context, intent, reason)
                return None
        return self.port.execute_intent(intent, context, hook=hook)

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
        reason = self.authority.reject_reason(request.context.account)
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
            reason = self.authority.reject_reason(request.context.account)
            if reason:
                return _reject_existing_order(self.coordinator, order_id, at=at, reason=reason)
        return self.port.submit_order(order_id, at=at, params=params)

    def cancel_order(self, order_id: str, *, at: datetime, params: Mapping[str, object] | None = None) -> OrderState:
        request = _order_request(self.coordinator, order_id)
        if request is not None:
            reason = self.authority.reject_reason(request.context.account)
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


def _resolve_intent_account(port: TradingExecutionPort, intent: TradeIntent) -> AccountContext | None:
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


def _account_key(account: AccountRef) -> str:
    return ".".join(_key_part(part) for part in (account.broker, account.account_id) if part)


def _key_part(value: object) -> str:
    text = str(value).strip().lower()
    return "_".join(part for part in ("".join(character if character.isalnum() else "_" for character in text)).split("_") if part)


__all__ = ["AccountTradeAuthority", "AuthorizingAccountPort", "AuthorizingTradingExecutionService"]
