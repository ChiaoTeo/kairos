from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping, Protocol, Sequence, overload

from kairospy.domain.account import AccountViewReader
from kairospy.domain.account import AccountModel
from kairospy.domain.intent import Intent, TradeIntent
from kairospy.domain.market import MarketSelector, MarketViewReader
from kairospy.domain.reference import MarketRef
from kairospy.domain.market.selection import MarketSelection, MarketSelectionQuery
from kairospy.domain.reference import ExchangeId, MarketTypeId


class StrategyReferenceCapability(Protocol):
    def query(self, request: MarketSelectionQuery) -> MarketSelection:
        ...

    def resolve(
        self,
        value: object,
        *,
        venue: str | None = None,
        market: str | None = None,
        as_of: datetime | None = None,
    ) -> object:
        ...

    def option_contracts(self, request: MarketSelectionQuery | None = None) -> tuple[object, ...]:
        ...


class StrategyContextProtocol(Protocol):
    @property
    def now(self) -> datetime | None:
        ...

    def intent(
        self,
        intent: Intent,
    ) -> None:
        ...

    def submit(self, command: object) -> object:
        ...

    @overload
    def subscribe(
        self,
        subject: MarketRef,
        *,
        selectors: Sequence[MarketSelector | type],
        identity: str | None = None,
        params: Mapping[str, object] | None = None,
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
        params: Mapping[str, object] | None = None,
    ) -> object:
        ...

    def unsubscribe(
        self,
        subscription: object,
    ) -> object:
        ...

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

    def option_chain(self, contracts: Sequence[object], *, underlying: Decimal | str | int | float | None = None) -> object:
        ...

    @property
    def reference(self) -> StrategyReferenceCapability:
        ...


# Compatibility aliases for strategy implementations migrating from the
# shorter names. New strategy contracts use StrategyContextProtocol.
Context = StrategyContextProtocol
StrategyContext = StrategyContextProtocol

__all__ = ["Context", "StrategyContext", "StrategyContextProtocol"]
