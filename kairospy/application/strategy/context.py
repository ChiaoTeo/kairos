from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping, Protocol, Sequence, overload

from kairospy.core.account import AccountViewReader
from kairospy.core.intent import Intent, TradeIntent
from kairospy.core.market import MarketSelector, MarketViewReader
from kairospy.core.reference import MarketRef
from kairospy.core.reference import ExchangeId, MarketTypeId, ReferenceViewReader
from .control import ControlFactory


class Context(Protocol):
    @property
    def now(self) -> datetime | None:
        ...

    def intent(
        self,
        intent: Intent,
    ) -> None:
        ...

    def trace(
        self,
        name: str,
        payload: Mapping[str, object],
    ) -> None:
        ...

    @overload
    def subscribe(
        self,
        subject: MarketRef,
        *,
        selectors: Sequence[MarketSelector | type],
        identity: str | None = None,
    ) -> object:
        ...

    @overload
    def subscribe(
        self,
        subject: object | MarketRef,
        *,
        selectors: Sequence[MarketSelector | type] | None = None,
        exchange: ExchangeId | str | None = None,
        market_type: MarketTypeId | str | None = None,
        identity: str | None = None,
    ) -> object:
        ...

    def unsubscribe(
        self,
        subscription: object,
    ) -> None:
        ...

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
        ...

    def account(self, key: str | int | None = None) -> object:
        ...

    @property
    def accounts(self) -> AccountViewReader:
        ...

    def view(self, key: str, default: object = None) -> object:
        ...

    def require_view(self, key: str) -> object:
        ...

    @property
    def market(self) -> MarketViewReader:
        ...

    @property
    def reference(self) -> ReferenceViewReader:
        ...

    @property
    def control(self) -> ControlFactory:
        ...


StrategyContext = Context

__all__ = ["Context", "StrategyContext"]
