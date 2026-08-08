from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from .events import EventEnvelope
from .requests import SubscriptionRequest, TargetPositionRequest
from .results import CommandResult


class StrategyContextProtocol(Protocol):
    """Only capability surface exposed to user-authored strategy code."""

    strategy_id: str
    instance_id: str
    state: MutableMapping[str, object]
    logger: "StrategyLogger"

    @property
    def now(self) -> datetime | None: ...

    @property
    def event(self) -> EventEnvelope | None: ...

    def subscribe(
        self,
        subject: str,
        *,
        selectors: Sequence[str] = (),
        exchange: str | None = None,
        market_type: str | None = None,
        asset_type: str | None = None,
        identity: str | None = None,
        params: Mapping[str, object] | None = None,
        dynamic: bool = False,
    ) -> CommandResult: ...

    def unsubscribe(self, subscription: object) -> CommandResult: ...

    def target_position(
        self,
        instrument: str,
        quantity: Decimal | str | int | float,
        *,
        account: str | None = None,
        accounts: Sequence[str] | None = None,
        limit_price: Decimal | str | int | float | None = None,
        reason: str = "",
        intent_id: str | None = None,
    ) -> CommandResult: ...

    def view(self, view_key: str, default: object = None) -> object: ...

    def require_view(self, view_key: str) -> object: ...


class StrategyProtocol(Protocol):
    """Lifecycle protocol implemented by every user strategy."""

    strategy_id: str

    def on_start(self, context: StrategyContextProtocol) -> None: ...
    def on_data(self, context: StrategyContextProtocol, event: EventEnvelope) -> None: ...
    def on_intent(self, context: StrategyContextProtocol, event: EventEnvelope) -> None: ...
    def on_clock(self, context: StrategyContextProtocol, event: EventEnvelope) -> None: ...
    def on_system(self, context: StrategyContextProtocol, event: EventEnvelope) -> None: ...
    def on_end(self, context: StrategyContextProtocol) -> None: ...


class StrategyBase:
    """Convenience implementation of the complete public lifecycle contract."""

    strategy_id = "strategy"

    def on_start(self, context: StrategyContextProtocol) -> None:
        return None

    def on_data(self, context: StrategyContextProtocol, event: EventEnvelope) -> None:
        return None

    def on_intent(self, context: StrategyContextProtocol, event: EventEnvelope) -> None:
        return None

    def on_clock(self, context: StrategyContextProtocol, event: EventEnvelope) -> None:
        return None

    def on_system(self, context: StrategyContextProtocol, event: EventEnvelope) -> None:
        return None

    def on_end(self, context: StrategyContextProtocol) -> None:
        return None
