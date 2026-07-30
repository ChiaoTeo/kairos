from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Sequence, overload

from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.ports import DataSubscription, MarketDataPort, MarketDataSubscriptionSpec
from kairospy.application.service.domain.market import parse_market_dataset_id
from kairospy.application.service.runtime import AccountQueryService
from kairospy.application.strategy import Context, ControlFactory, ControlJournal
from kairospy.core.intent import Intent, IntentJournal, TradeIntent, target_position_intent
from kairospy.core.market import MarketSelector, MarketViewReader
from kairospy.core.reference import ExchangeId, MarketRef, MarketResolver, MarketTypeId, ReferenceViewReader
from kairospy.core.views import ViewStore


@dataclass(slots=True)
class RuntimeContext(Context):
    strategy_id: str
    event: RuntimeEnvelope | None = None
    state: Mapping[str, object] = field(default_factory=dict)
    intents: IntentJournal = field(default_factory=IntentJournal)
    data: MarketDataPort | None = None
    views: ViewStore = field(default_factory=ViewStore)
    controls: ControlJournal = field(default_factory=ControlJournal)
    emitted_intents: list[Intent] = field(default_factory=list)
    emitted_traces: list[Mapping[str, object]] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_id", str(self.strategy_id).strip())
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        object.__setattr__(self, "state", MappingProxyType(dict(self.state)))

    def bind(self, event: RuntimeEnvelope | None) -> "RuntimeContext":
        self.event = event
        self.emitted_intents.clear()
        self.emitted_traces.clear()
        return self

    @property
    def control(self) -> ControlFactory:
        return ControlFactory(strategy_id=self.strategy_id, requested_at=self.now, journal=self.controls)

    @property
    def now(self) -> datetime | None:
        return None if self.event is None else self.event.time

    def intent(self, intent: Intent) -> None:
        if str(intent.strategy_id) != self.strategy_id:
            raise ValueError("intent strategy_id does not match runtime strategy")
        at = self.now or datetime.now(timezone.utc)
        self.intents.record_intent(intent, at=at)
        self.emitted_intents.append(intent)

    def trace(self, name: str, payload: Mapping[str, object]) -> None:
        label = str(name).strip()
        if not label:
            raise ValueError("trace name is required")
        self.emitted_traces.append(
            {
                "name": label,
                "time": self.now,
                "strategy_id": self.strategy_id,
                "payload": dict(payload),
            }
        )

    @overload
    def subscribe(
        self,
        subject: MarketRef,
        *,
        selectors: Sequence[MarketSelector | type],
        identity: str | None = None,
    ) -> DataSubscription:
        ...

    @overload
    def subscribe(
        self,
        subject: object | MarketRef,
        *,
        selectors: Sequence[MarketSelector | type],
        exchange: ExchangeId | str | None = None,
        market_type: MarketTypeId | str | None = None,
        identity: str | None = None,
    ) -> DataSubscription:
        ...

    def subscribe(
        self,
        subject: object | MarketRef,
        *,
        selectors: Sequence[MarketSelector | type] | None = None,
        exchange: ExchangeId | str | None = None,
        market_type: MarketTypeId | str | None = None,
        identity: str | None = None,
    ) -> DataSubscription:
        if self.data is None:
            raise RuntimeError("runtime context has no data port")
        if selectors is None and isinstance(subject, str) and subject.startswith("market."):
            dataset = parse_market_dataset_id(subject)
            return self.data.subscribe(
                MarketDataSubscriptionSpec(
                    dataset.market_ref,
                    (dataset.selector,),
                    identity=identity,
                    dataset_id=dataset.dataset_id,
                )
            )
        if selectors is None:
            raise ValueError("data subscription selectors are required unless subscribing by dataset id")
        exchange_text = None if exchange is None else str(exchange)
        market_type_text = None if market_type is None else str(market_type)
        market_ref = MarketResolver(default_venue=exchange_text, default_market=market_type_text).resolve(
            subject,
            venue=exchange_text,
            market=market_type_text,
        )
        return self.data.subscribe(MarketDataSubscriptionSpec(market_ref, selectors, identity=identity))

    def unsubscribe(self, subscription: object) -> None:
        if self.data is None:
            raise RuntimeError("runtime context has no data port")
        self.data.unsubscribe(subscription if isinstance(subscription, str) else getattr(subscription, "key", str(subscription)))

    def view(self, key: str, default: object = None) -> object:
        return self.views.get(key, default)

    def require_view(self, key: str) -> object:
        return self.views.require(key)

    @property
    def market(self) -> MarketViewReader:
        return MarketViewReader(self.views)

    @property
    def accounts(self) -> AccountQueryService:
        return AccountQueryService(self.views)

    @property
    def reference(self) -> ReferenceViewReader:
        return ReferenceViewReader(self.views)

    def target_position(
        self,
        instrument: object,
        quantity: Decimal | str | int | float,
        *,
        account: int | str | None = None,
        book: object | None = None,
        limit_price: Decimal | str | int | float | None = None,
        reason: str = "",
        intent_id: str | None = None,
    ) -> TradeIntent:
        if isinstance(account, bool):
            raise ValueError("account must be an integer index or account id")
        account_index = account if isinstance(account, int) else None
        account_id = str(account) if account is not None and account_index is None else None
        resolved = self._resolve_intent_market(instrument)
        intent = target_position_intent(
            strategy_id=self.strategy_id,
            instrument_id=resolved.instrument_id,
            market_id=resolved.market_id,
            account_id=account_id,
            account_index=account_index,
            account_book=book,
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

    def _resolve_intent_market(self, instrument: object) -> MarketRef:
        try:
            return self.reference.resolve(instrument)
        except (KeyError, ValueError):
            return MarketResolver().resolve(instrument)


__all__ = ["RuntimeContext"]
