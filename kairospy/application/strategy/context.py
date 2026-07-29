from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping, Protocol, Sequence, overload

from kairospy.core.intent import Intent, TradeIntent
from kairospy.core.market import MarketSelector
from kairospy.core.reference import MarketRef
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
        market: MarketRef,
        *,
        selectors: Sequence[MarketSelector | type],
        identity: str | None = None,
    ) -> object:
        ...

    @overload
    def subscribe(
        self,
        instrument: object | MarketRef,
        *,
        selectors: Sequence[MarketSelector | type] | None = None,
        venue: str | None = None,
        market: str | None = None,
        identity: str | None = None,
    ) -> object:
        ...

    def unsubscribe(
        self,
        subscription: object,
    ) -> None:
        ...

    def request_quote(self, instrument: object, *, venue: str | None = None, market: str | None = None) -> object | None:
        ...

    def target_position(
        self,
        instrument: object,
        quantity: Decimal | str | int | float,
        *,
        account: int | str | None = None,
        limit_price: Decimal | str | int | float | None = None,
        reason: str = "",
        intent_id: str | None = None,
    ) -> TradeIntent:
        ...

    def account(self, key: str | None = None) -> object:
        ...

    def view(self, key: str, default: object = None) -> object:
        ...

    def require_view(self, key: str) -> object:
        ...

    @property
    def control(self) -> ControlFactory:
        ...


StrategyContext = Context

__all__ = ["Context", "StrategyContext"]
