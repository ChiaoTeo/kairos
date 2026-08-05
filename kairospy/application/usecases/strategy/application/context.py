from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Sequence, overload

from kairospy.application.support.runtime.application.interaction import SystemCall, SystemCallResult
from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.support.runtime.domain.commands import CommandHandle, RuntimeCommand
from kairospy.application.usecases.account.application.queries import AccountViewQueryService
from kairospy.domain.intent import Intent, IntentJournalView, IntentViewKeys, TradeIntent, target_position_intent
from kairospy.domain.market import MarketViewReader
from kairospy.domain.reference import MarketResolver
from kairospy.application.usecases.strategy.protocol import StrategyReferenceCapability, StrategySubscriptionGroupRequest, StrategySubscriptionRequest
from kairospy.application.usecases.strategy.application.option_chain import OptionChainView, build_option_chain_view
from kairospy.domain.account import AccountModel


class StrategyContext:
    """The only context facade exposed to a strategy callback.

    Runtime owns the invocation lifecycle, but this object owns the strategy
    view of that lifecycle.  It deliberately stores only the current event,
    read models, state and the minimal SystemCall surface; no Runtime kernel
    or dispatcher object is reachable from it.

    """

    def __init__(
        self,
        strategy_id: str,
        *,
        state: Mapping[str, object] | None = None,
        system_call: SystemCall | object | None = None,
        views: ViewStore | None = None,
        reference: StrategyReferenceCapability | None = None,
    ) -> None:
        if not strategy_id.strip():
            raise ValueError("strategy_id is required")
        self.strategy_id = strategy_id
        self.state = MappingProxyType(dict(state or {}))
        self.system_call = system_call
        self.views = views if views is not None else ViewStore()
        self._reference = reference
        self._event: object | None = None
        self._emitted_intents: list[object] = []

    def bind(self, event: object | None) -> "StrategyContext":
        self._event = event
        self._emitted_intents.clear()
        return self

    @property
    def now(self) -> datetime | None:
        value = getattr(self._event, "time", None)
        return value if isinstance(value, datetime) else None

    @property
    def event(self) -> object | None:
        """The current input for System-owned result processing.

        Strategy protocols do not require this property; it is intentionally
        a read-only facet used by the System bridge when collecting outputs.
        """
        return self._event

    def intent(self, intent: Intent) -> None:
        if str(intent.strategy_id) != self.strategy_id:
            raise ValueError("intent strategy_id does not match strategy")
        emit_intent = getattr(self.system_call, "emit_intent", None)
        if callable(emit_intent):
            emit_intent(intent, context=self)
            return
        self._emitted_intents.append(intent)

    def view(self, key: str, default: object = None) -> object:
        return self.views.get(key, default)

    def require_view(self, key: str) -> object:
        return self.views.require(key)

    @property
    def emitted_intents(self) -> tuple[object, ...]:
        return tuple(self._emitted_intents)

    @property
    def accounts(self) -> AccountViewQueryService:
        return AccountViewQueryService(self.views)

    @property
    def intents(self) -> IntentJournalView | None:
        """Current read-only Intent projection for this strategy."""
        return self.views.get(IntentViewKeys.system_intents)

    @property
    def reference(self) -> StrategyReferenceCapability:
        if self._reference is None:
            raise RuntimeError("strategy reference capability is not available")
        return self._reference

    def target_position(
        self,
        instrument: object,
        quantity: Decimal | str | int | float,
        *,
        account: int | str | None = None,
        account_segment: str | None = None,
        limit_price: Decimal | str | int | float | None = None,
        reason: str = "",
        intent_id: str | None = None,
    ) -> TradeIntent:
        if isinstance(account, bool):
            raise ValueError("account must be an integer index or account id")
        account_index = account if isinstance(account, int) else None
        account_id = str(account) if account is not None and account_index is None else None
        try:
            resolved = self.reference.resolve(instrument)
        except (KeyError, ValueError):
            resolved = MarketResolver().resolve(instrument)
        intent = target_position_intent(
            strategy_id=self.strategy_id,
            instrument_id=resolved.instrument_id,
            market_id=resolved.market_id,
            account_id=account_id,
            account_index=account_index,
            account_segment=account_segment,
            target_quantity=Decimal(str(quantity)),
            at=self.now,
            limit_price=None if limit_price is None else Decimal(str(limit_price)),
            reason=reason,
            intent_id=intent_id,
        )
        self.intent(intent)
        return intent

    def account(self, key: str | int | None = None) -> object:
        if key is not None and self.accounts.has_account(key):
            return self.accounts.account(key)
        if isinstance(key, int):
            raise KeyError(f"unknown account: {key}")
        return self.accounts.current(key)

    @property
    def market(self) -> MarketViewReader:
        return MarketViewReader(self.views)

    def option_chain(self, contracts: Sequence[object], *, underlying: Decimal | str | int | float | None = None) -> OptionChainView:
        typed = tuple(item for item in contracts if hasattr(item, "market"))
        return build_option_chain_view(
            typed,  # type: ignore[arg-type]
            self.market,
            underlying=None if underlying is None else Decimal(str(underlying)),
        )

    def submit(self, command: RuntimeCommand) -> SystemCallResult:
        if self.system_call is None:
            raise RuntimeError("strategy context has no system call")
        return self.system_call.call(command)

    @overload
    def subscribe(
        self,
        subject: object,
        *,
        selectors: Sequence[object],
        identity: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> SystemCallResult:
        ...

    @overload
    def subscribe(
        self,
        subject: object,
        *,
        selectors: Sequence[object] | None = None,
        exchange: str | None = None,
        market_type: str | None = None,
        identity: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> SystemCallResult:
        ...

    def subscribe(
        self,
        subject: object,
        *,
        selectors: Sequence[object] | None = None,
        exchange: str | None = None,
        market_type: str | None = None,
        identity: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> SystemCallResult:
        selected_markets = getattr(subject, "markets", None)
        request = StrategySubscriptionRequest(
            subject=subject,
            selectors=() if selectors is None else tuple(selectors),
            exchange=None if exchange is None else str(exchange),
            market_type=None if market_type is None else str(market_type),
            identity=identity,
            params={} if params is None else params,
        )
        if selected_markets is not None:
            requests = tuple(
                StrategySubscriptionRequest(
                    subject=market,
                    selectors=request.selectors,
                    exchange=request.exchange,
                    market_type=request.market_type,
                    identity=request.identity,
                    params=request.params,
                )
                for market in tuple(selected_markets)
            )
            return self.submit(
                RuntimeCommand(
                    "market.subscribe.batch",
                    StrategySubscriptionGroupRequest(requests),
                    actor=self.strategy_id,
                )
            )
        if selectors is None and not (isinstance(subject, str) and subject.startswith("market.")):
            raise ValueError("data subscription selectors are required unless subscribing by dataset id")
        return self.submit(RuntimeCommand("market.subscribe", request, actor=self.strategy_id))

    def unsubscribe(self, subscription: object) -> SystemCallResult:
        if isinstance(subscription, (CommandHandle, SystemCallResult)):
            subscription_id = subscription.result.get("subscription_id")
            if subscription_id is None:
                raise ValueError("subscription handle has no subscription id")
            subscription = str(subscription_id)
        elif not isinstance(subscription, str):
            subscription = getattr(subscription, "key", str(subscription))
        return self.submit(RuntimeCommand("market.unsubscribe", subscription, actor=self.strategy_id))


__all__ = ["StrategyContext"]
