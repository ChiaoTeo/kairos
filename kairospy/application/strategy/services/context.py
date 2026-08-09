from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from kairospy.strategy import StrategyContextProtocol, StrategyLogger
from kairospy.infrastructure.contracts.reference import ReferenceSnapshotClient
from ..domain.messages import CommandHandle, ContextRequest, EventEnvelope, SnapshotEnvelope, SubscriptionRequest, TargetPositionRequest
from ..protocol import ContextBus, EventStream, IntentCommandPort, MarketCommandPort, SnapshotReader


@dataclass(frozen=True, slots=True)
class StrategyClientBundle:
    """All process-boundary capabilities owned by one strategy instance."""

    commands: ContextBus
    market_commands: MarketCommandPort
    execution_commands: IntentCommandPort
    market_snapshots: SnapshotReader
    market_events: EventStream
    reference: ReferenceSnapshotClient | None = None


class StrategyContext(StrategyContextProtocol):
    """The only interaction surface given to user-authored strategies."""

    def __init__(
        self,
        strategy_id: str,
        *,
        instance_id: str = "",
        clients: StrategyClientBundle,
        state: dict[str, object] | None = None,
        request_observer: Callable[[ContextRequest, CommandHandle], None] | None = None,
        logger: StrategyLogger | None = None,
    ) -> None:
        if not strategy_id.strip():
            raise ValueError("strategy_id is required")
        self.strategy_id = strategy_id
        self.instance_id = instance_id
        self.clients = clients
        # Compatibility aliases for existing diagnostics; new code uses the
        # explicit client bundle.
        self._bus = clients.commands
        self._snapshots = clients.market_snapshots
        self.reference = clients.reference
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
        self.logger.info(
            "strategy command submitted",
            event="strategy_command_submitted",
            operation=operation,
            request_id=request.request_id,
            payload_type=type(payload).__name__,
            **self._command_observability(payload),
        )
        try:
            handle = self.clients.commands.submit(request)
        except Exception as error:
            self.logger.error(
                "strategy command raised",
                event="strategy_command_raised",
                operation=operation,
                request_id=request.request_id,
                error=str(error),
            )
            raise
        self.logger.info(
            "strategy command result",
            event="strategy_command_result",
            operation=operation,
            request_id=request.request_id,
            command_status=handle.status,
            error=handle.error,
            error_code=handle.error_code,
            retryable=handle.retryable,
            result=dict(handle.result),
        )
        if self._request_observer is not None:
            self._request_observer(request, handle)
        return handle

    @staticmethod
    def _command_observability(payload: object) -> dict[str, object]:
        """Return business request facts without leaking provider objects."""
        if isinstance(payload, SubscriptionRequest):
            return {
                "subject": payload.subject,
                "selectors": list(payload.selectors),
                "exchange": payload.exchange,
                "market_type": payload.market_type,
                "asset_type": payload.asset_type,
                "identity": payload.identity,
                "dynamic": payload.dynamic,
                "params": dict(payload.params),
            }
        return {}

    def subscribe(self, subject: str, *, selectors: Sequence[str] = (), exchange: str | None = None, market_type: str | None = None, asset_type: str | None = None, identity: str | None = None, params: Mapping[str, object] | None = None, dynamic: bool = False) -> CommandHandle:
        request = SubscriptionRequest(subject=subject, selectors=tuple(selectors), exchange=exchange, market_type=market_type, asset_type=asset_type, identity=identity, params=params or {}, dynamic=dynamic)
        return self._submit("market.subscribe", request)


    def unsubscribe(self, subscription: object) -> CommandHandle:
        return self._submit("market.unsubscribe", subscription)

    def target_position(self, instrument: str, quantity: Decimal | str | int | float, *, account: str | None = None, accounts: Sequence[str] | None = None, limit_price: Decimal | str | int | float | None = None, reason: str = "", intent_id: str | None = None) -> CommandHandle:
        return self._submit("intent.target_position", TargetPositionRequest(
            instrument_id=instrument,
            quantity=Decimal(str(quantity)),
            account_id=account,
            account_ids=tuple(accounts or ()),
            limit_price=None if limit_price is None else Decimal(str(limit_price)),
            reason=reason,
            intent_id=intent_id,
            source_snapshot_id=None if self._event is None else self._event.stream_id,
            source_event_sequence=None if self._event is None else self._event.sequence,
        ))

    def view(self, view_key: str, default: object = None) -> object:
        try:
            return self._views.get(view_key, self.clients.market_snapshots.read(view_key)).payload
        except (KeyError, FileNotFoundError):
            return default

    def require_view(self, view_key: str) -> object:
        return self._snapshot(view_key).payload

    def _install_snapshot(self, snapshot: SnapshotEnvelope) -> None:
        self._views[snapshot.view_key] = snapshot

    def _snapshot(self, view_key: str) -> SnapshotEnvelope:
        snapshot = self._views.get(view_key)
        if snapshot is None:
            snapshot = self.clients.market_snapshots.read(view_key)
            self._views[view_key] = snapshot
        return snapshot

    def _request_id(self, operation: str) -> str:
        self._request_counter += 1
        sequence = self._event_sequence() or 0
        return f"{self.strategy_id}:{self.instance_id}:{operation}:{sequence}:{self._request_counter}"

    def _event_sequence(self) -> int | None:
        return None if self._event is None else self._event.sequence
