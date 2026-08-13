from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from kairospy.application.reference.models import Market
from kairospy.domain_types import MarketId

from .models import Bar, Quote, Trade
from .requests import SubscriptionRequest

if TYPE_CHECKING:
    from kairospy.infrastructure.transport.commands import MarketCommandClient
    from kairospy.infrastructure.transport.market import MmapMarketSnapshotReader
    from kairospy.strategy.results import CommandResult


@dataclass(frozen=True, slots=True)
class Subscription:
    subscription_id: str
    request_id: str
    status: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SubscriptionStatus:
    """Market-owned status for one Strategy subscription request."""

    request_id: str
    subscription_id: str | None
    status: str
    request: SubscriptionRequest
    result: Mapping[str, object]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SubscriptionReleaseResult:
    """Result of releasing every subscription owned by this Strategy access."""

    request_id: str
    status: str
    removed_subscription_ids: tuple[str, ...]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class MarketJoinPoint:
    """Stable snapshot watermark used to join the Market event stream."""

    snapshot_id: str
    event_stream_id: str
    event_sequence: int


class MarketApplication:
    """Concrete strategy-facing Market use cases.

    The Python SDK has one Market implementation: the Unix command client and
    mmap projection reader.  Keeping those concrete dependencies here avoids a
    second port hierarchy inside the SDK while the Rust Market application
    remains the authoritative process boundary and state owner.
    """

    def __init__(
        self,
        commands: MarketCommandClient,
        snapshots: MmapMarketSnapshotReader,
        *,
        strategy_id: str,
        instance_id: str,
    ) -> None:
        if not strategy_id.strip() or not instance_id.strip():
            raise ValueError("strategy_id and instance_id are required")
        self._commands = commands
        self._snapshots = snapshots
        self._strategy_id = strategy_id
        self._instance_id = instance_id
        self._event_sequence: int | None = None
        self._request_counter = 0
        self._handles: dict[str, CommandResult] = {}
        self._subscription_requests: dict[str, SubscriptionRequest] = {}

    def bind_event(self, sequence: int | None) -> None:
        """Bind command causation to the currently dispatched strategy event."""

        self._event_sequence = sequence

    def subscribe_bars(
        self, market: Market | MarketId, *, timeframe: str
    ) -> Subscription:
        if not timeframe.strip():
            raise ValueError("bar timeframe is required")
        market_id = _market_id(market)
        return self._subscribe(
            SubscriptionRequest(
                subject=str(market_id),
                selectors=(f"bar:{timeframe}",),
                identity=str(market_id),
            )
        )

    def subscribe_quotes(self, market: Market | MarketId) -> Subscription:
        market_id = _market_id(market)
        return self._subscribe(
            SubscriptionRequest(
                subject=str(market_id), selectors=("quote",), identity=str(market_id)
            )
        )

    def subscribe_trades(self, market: Market | MarketId) -> Subscription:
        market_id = _market_id(market)
        return self._subscribe(
            SubscriptionRequest(
                subject=str(market_id), selectors=("trade",), identity=str(market_id)
            )
        )

    def unsubscribe(self, subscription: Subscription) -> None:
        request_id = self._request_id("market.unsubscribe")
        handle = self._commands.unsubscribe(
            subscription.subscription_id,
            strategy_id=self._strategy_id,
            instance_id=self._instance_id,
            request_id=request_id,
        )
        self._handles[request_id] = handle

    def latest_bar(self, market: Market | MarketId, *, timeframe: str) -> Bar | None:
        return self._snapshot().latest_bar(_market_id(market), timeframe)

    def latest_quote(self, market: Market | MarketId) -> Quote | None:
        return self._snapshot().latest_quote(_market_id(market))

    def latest_trade(self, market: Market | MarketId) -> Trade | None:
        return self._snapshot().latest_trade(_market_id(market))

    def _subscribe(self, request: SubscriptionRequest) -> Subscription:
        request_id = self._request_id("market.subscribe")
        handle = self._commands.subscribe(
            request,
            strategy_id=self._strategy_id,
            instance_id=self._instance_id,
            request_id=request_id,
        )
        self._handles[request_id] = handle
        self._subscription_requests[request_id] = request
        return _subscription(handle)

    def _snapshot(self):
        return self._snapshots.read("market.current")

    def _request_id(self, operation: str) -> str:
        self._request_counter += 1
        return (
            f"{self._strategy_id}:{self._instance_id}:{operation}:"
            f"{self._event_sequence or 0}:{self._request_counter}"
        )

    def subscription_status(self, request_id: str) -> SubscriptionStatus:
        """Return a business status without exposing the transport result."""

        try:
            request = self._subscription_requests[request_id]
        except KeyError as error:
            raise KeyError(f"unknown Market subscription request: {request_id}") from error
        handle = self._command_status(request_id)
        subscription_id = handle.result.get("subscription_id")
        return SubscriptionStatus(
            request_id=handle.request_id,
            subscription_id=(
                None if subscription_id is None else str(subscription_id)
            ),
            status=handle.status,
            request=request,
            result=dict(handle.result),
            error=handle.error,
        )

    def subscription_statuses(self) -> tuple[SubscriptionStatus, ...]:
        """Return statuses in the deterministic order requests were created."""

        return tuple(
            self.subscription_status(request_id)
            for request_id in self._subscription_requests
        )

    def release_strategy_subscriptions(self) -> SubscriptionReleaseResult:
        """Release the owner-scoped Market demand for this Strategy instance."""

        handle = self._release_owner()
        removed = handle.result.get("removed_subscription_ids", ())
        removed_ids = (
            tuple(str(value) for value in removed if isinstance(value, str))
            if isinstance(removed, (list, tuple, set))
            else ()
        )
        return SubscriptionReleaseResult(
            request_id=handle.request_id,
            status=handle.status,
            removed_subscription_ids=removed_ids,
            error=handle.error,
        )

    def join_point(self, view_key: str = "market.current") -> MarketJoinPoint:
        snapshot = self._snapshots.read(view_key)
        return MarketJoinPoint(
            snapshot_id=snapshot.snapshot_id,
            event_stream_id=snapshot.event_stream_id,
            event_sequence=snapshot.event_sequence,
        )

    def _command_status(self, request_id: str) -> CommandResult:
        status = getattr(self._commands, "status", None)
        if callable(status):
            return cast("CommandResult", status(request_id))
        return self._handles[request_id]

    def _release_owner(self) -> CommandResult:
        request_id = self._request_id("market.release_owner")
        handle = self._commands.release_owner(
            strategy_id=self._strategy_id,
            instance_id=self._instance_id,
            request_id=request_id,
        )
        self._handles[request_id] = handle
        return handle


def _market_id(value: Market | MarketId) -> MarketId:
    return value.id if isinstance(value, Market) else value


def _subscription(value: CommandResult) -> Subscription:
    request_id = value.request_id
    subscription_id = value.result.get("subscription_id", request_id)
    return Subscription(
        str(subscription_id),
        request_id,
        value.status,
        value.error,
    )
