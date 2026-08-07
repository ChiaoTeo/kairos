from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Callable, Mapping, Sequence

from kairospy.strategy import StrategyContextProtocol, StrategyLogger

from ..domain.messages import CommandHandle, ContextRequest, EventEnvelope, SnapshotEnvelope, SubscriptionRequest, TargetPositionRequest
from ..protocol import ContextBus, SnapshotReader


class StrategyContext(StrategyContextProtocol):
    """The only interaction surface given to user-authored strategies."""

    def __init__(
        self,
        strategy_id: str,
        *,
        instance_id: str = "",
        bus: ContextBus,
        snapshots: SnapshotReader,
        state: dict[str, object] | None = None,
        request_observer: Callable[[ContextRequest, CommandHandle], None] | None = None,
        logger: StrategyLogger | None = None,
    ) -> None:
        if not strategy_id.strip():
            raise ValueError("strategy_id is required")
        self.strategy_id = strategy_id
        self.instance_id = instance_id
        self._bus = bus
        self._snapshots = snapshots
        self._request_observer = request_observer
        self.state = state if state is not None else {}
        self.logger = logger or StrategyLogger(fields={"strategy_id": strategy_id, "instance_id": instance_id})
        self._event: EventEnvelope | None = None
        self._views: dict[str, SnapshotEnvelope] = {}
        self._request_counter = 0

    def _bind(self, event: EventEnvelope | None) -> "StrategyContext":
        self._event = event
        return self

    @property
    def now(self) -> datetime | None:
        return None if self._event is None else self._event.occurred_at

    @property
    def event(self) -> EventEnvelope | None:
        return self._event

    def _submit(self, operation: str, payload: object) -> CommandHandle:
        request = ContextRequest(operation, payload, self.strategy_id, self._request_id(operation), self.instance_id)
        handle = self._bus.submit(request)
        if self._request_observer is not None:
            self._request_observer(request, handle)
        return handle

    def subscribe(self, subject: str, *, selectors: Sequence[str] = (), exchange: str | None = None, market_type: str | None = None, asset_type: str | None = None, identity: str | None = None, params: Mapping[str, object] | None = None, dynamic: bool = False) -> CommandHandle:
        request = SubscriptionRequest(subject=subject, selectors=tuple(selectors), exchange=exchange, market_type=market_type, asset_type=asset_type, identity=identity, params=params or {}, dynamic=dynamic)
        return self._submit("market.subscribe", request)

    def unsubscribe(self, subscription: object) -> CommandHandle:
        return self._submit("market.unsubscribe", subscription)

    def target_position(self, instrument: str, quantity: Decimal | str | int | float, *, account: str | None = None, limit_price: Decimal | str | int | float | None = None, reason: str = "", intent_id: str | None = None) -> CommandHandle:
        return self._submit("intent.target_position", TargetPositionRequest(
            instrument_id=instrument,
            quantity=Decimal(str(quantity)),
            account_id=account,
            limit_price=None if limit_price is None else Decimal(str(limit_price)),
            reason=reason,
            intent_id=intent_id,
        ))

    def view(self, view_key: str, default: object = None) -> object:
        try:
            return self._views.get(view_key, self._snapshots.read(view_key)).payload
        except (KeyError, FileNotFoundError):
            return default

    def require_view(self, view_key: str) -> object:
        return self._snapshot(view_key).payload

    def _install_snapshot(self, snapshot: SnapshotEnvelope) -> None:
        self._views[snapshot.view_key] = snapshot

    def _snapshot(self, view_key: str) -> SnapshotEnvelope:
        snapshot = self._views.get(view_key)
        if snapshot is None:
            snapshot = self._snapshots.read(view_key)
            self._views[view_key] = snapshot
        return snapshot

    def _request_id(self, operation: str) -> str:
        self._request_counter += 1
        sequence = self._event_sequence() or 0
        return f"{self.strategy_id}:{operation}:{sequence}:{self._request_counter}"

    def _event_sequence(self) -> int | None:
        return None if self._event is None else self._event.sequence
