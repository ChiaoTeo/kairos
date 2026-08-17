from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from kairospy.application.reference.models import Market
from kairospy.domain_types import MarketId

from .events import BarEvent, MarketEvent, TradeEvent
from .models import Bar, OptionGreeks, Quote, Trade
from .requests import SubscriptionRequest


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


class MarketApplication:
    """Concrete strategy-facing Market use cases.

    The Python SDK has one Market implementation: the Unix command client and
    mmap projection reader.  Keeping those concrete dependencies here avoids a
    second port hierarchy inside the SDK while the Rust Market application
    remains the authoritative process boundary and state owner.
    """

    def __init__(
        self,
        commands: Any,
        snapshots: Any,
        event_source: Any | None = None,
        *,
        strategy_id: str,
        instance_id: str,
        launch_id: str | None = None,
        source_id: str = "default",
    ) -> None:
        if not strategy_id.strip() or not instance_id.strip():
            raise ValueError("strategy_id and instance_id are required")
        self._commands = commands
        self._snapshots = snapshots
        self._event_source = event_source
        self._event_cursor: int | None = None
        self._strategy_id = strategy_id
        self._instance_id = instance_id
        self._launch_id = launch_id
        self._source_id = source_id
        self._event_sequence: int | None = None
        self._latest_trades: dict[MarketId, Trade] = {}
        self._event_source_ready = event_source is None
        self._request_counter = 0
        self._handles: dict[str, Any] = {}
        self._subscription_requests: dict[str, SubscriptionRequest] = {}
        self._subscription_request_ids: dict[str, str] = {}

    def check_event_source_ready(self) -> None:
        """Validate the configured Market event source without reading mmap."""

        if self._event_source_ready:
            return
        check_ready = getattr(self._event_source, "check_ready", None)
        if callable(check_ready):
            check_ready()
        self._event_source_ready = True

    async def events(self) -> AsyncIterator[MarketEvent]:
        """Yield typed Market events from the configured incremental source."""

        if self._event_source is None:
            raise RuntimeError("Market event source is unavailable")
        from .mapping import map_market_event
        from .events import (
            BarEvent,
            EventStreamGap,
            GreeksEvent,
            QuoteEvent,
            TradeEvent,
        )

        cursor = self._event_cursor or 0
        subscribe_live = getattr(self._event_source, "subscribe_live", None)
        if callable(subscribe_live):
            records = subscribe_live()
            live = True
        else:
            records = self._event_source.replay_from(cursor)
            live = False
        async for record in records:
            typed = isinstance(record, (BarEvent, QuoteEvent, TradeEvent, GreeksEvent))
            stream_id = record.metadata.stream_id if typed else record.stream_id
            sequence = record.metadata.sequence if typed else record.sequence
            if stream_id != "market.events":
                raise RuntimeError(
                    f"Market event stream identity is invalid: {stream_id}"
                )
            if not typed:
                if self._launch_id is not None and record.launch_id != self._launch_id:
                    raise RuntimeError("Market event belongs to another launch")
                if (
                    self._launch_id is not None
                    and record.instance_id != self._instance_id
                ):
                    raise RuntimeError(
                        "Market event belongs to another launch instance"
                    )
            if cursor == 0 and live:
                cursor = sequence - 1
            if sequence <= cursor:
                continue
            expected = cursor + 1
            if sequence != expected:
                raise EventStreamGap(stream_id, expected, sequence)
            cursor = sequence
            self._event_cursor = cursor
            if not typed and record.kind not in {"bar", "quote", "trade", "greeks"}:
                continue
            event = record if typed else map_market_event(record)
            if isinstance(event, TradeEvent):
                self._latest_trades[event.value.market_id] = event.value
            if self._matches_subscription(event):
                yield event

    @property
    def events_replayable(self) -> bool:
        return bool(getattr(self._event_source, "replayable", False))

    @property
    def events_enabled(self) -> bool:
        """Whether this Strategy currently owns Market event demand."""

        return self.events_replayable or bool(self._subscription_requests)

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

    def subscribe_greeks(self, market: Market | MarketId) -> Subscription:
        market_id = _market_id(market)
        return self._subscribe(
            SubscriptionRequest(
                subject=str(market_id), selectors=("greeks",), identity=str(market_id)
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
        owner_request_id = self._subscription_request_ids.pop(
            subscription.subscription_id, None
        )
        if owner_request_id is not None:
            self._subscription_requests.pop(owner_request_id, None)

    def latest_bar(
        self, market: Market | MarketId, *, timeframe: str, source_id: str | None = None
    ) -> Bar | None:
        reader = getattr(self._snapshots, "read_bar", None)
        if callable(reader):
            return reader(str(_market_id(market)), source_id or self._source_id, timeframe)
        raise RuntimeError("Market v2 bar view reader is unavailable")

    def latest_quote(
        self, market: Market | MarketId, *, source_id: str | None = None
    ) -> Quote | None:
        reader = getattr(self._snapshots, "read_quote", None)
        if callable(reader):
            return reader(str(_market_id(market)), source_id or self._source_id)
        raise RuntimeError("Market v2 quote view reader is unavailable")

    def latest_trade(self, market: Market | MarketId) -> Trade | None:
        """Return the latest consumed trade event.

        Trade is event-only in Market v2 and has no mmap current-view
        resource. The result is therefore available after the event stream has
        delivered a trade, rather than through an aggregate snapshot read.
        """

        return self._latest_trades.get(_market_id(market))

    def latest_greeks(
        self, market: Market | MarketId, *, source_id: str | None = None
    ) -> OptionGreeks | None:
        reader = getattr(self._snapshots, "read_greeks", None)
        if callable(reader):
            return reader(str(_market_id(market)), source_id or self._source_id)
        raise RuntimeError("Market v2 greeks view reader is unavailable")

    def current_view(
        self,
        market: Market | MarketId,
        *,
        source_id: str,
        kind: Any,
        qualifier: str | None = None,
    ):
        """Read one independent Market v2 current-view resource.

        Current views are partitioned by market, source, data kind, and an
        optional qualifier. This API intentionally does not reconstruct an
        aggregate snapshot when one field changes.
        """

        reader = getattr(self._snapshots, "read_view", None)
        if not callable(reader):
            raise RuntimeError("Market v2 view reader is unavailable")
        return reader(str(_market_id(market)), source_id, kind, qualifier)

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
        subscription = _subscription(handle)
        self._subscription_request_ids[subscription.subscription_id] = request_id
        return subscription

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
            raise KeyError(
                f"unknown Market subscription request: {request_id}"
            ) from error
        handle = self._command_status(request_id)
        subscription_id = handle.result.get("subscription_id")
        return SubscriptionStatus(
            request_id=handle.request_id,
            subscription_id=(None if subscription_id is None else str(subscription_id)),
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
        self._subscription_requests.clear()
        self._subscription_request_ids.clear()
        return SubscriptionReleaseResult(
            request_id=handle.request_id,
            status=handle.status,
            removed_subscription_ids=removed_ids,
            error=handle.error,
        )

    def _command_status(self, request_id: str) -> Any:
        status = getattr(self._commands, "status", None)
        if callable(status):
            return status(request_id)
        return self._handles[request_id]

    def _release_owner(self) -> Any:
        request_id = self._request_id("market.release_owner")
        handle = self._commands.release_owner(
            strategy_id=self._strategy_id,
            instance_id=self._instance_id,
            request_id=request_id,
        )
        self._handles[request_id] = handle
        return handle

    def _matches_subscription(self, event: MarketEvent) -> bool:
        """Apply this Strategy instance's requested Market demand."""

        if self.events_replayable or not self._subscription_requests:
            return True
        market_id = str(event.data.market_id)
        selector = (
            f"bar:{event.data.timeframe}" if isinstance(event, BarEvent) else event.kind
        )
        return any(
            request.subject == market_id and selector in request.selectors
            for request in self._subscription_requests.values()
        )


def _market_id(value: Market | MarketId) -> MarketId:
    return value.id if isinstance(value, Market) else value


def _subscription(value: Any) -> Subscription:
    request_id = value.request_id
    subscription_id = value.result.get("subscription_id", request_id)
    return Subscription(
        str(subscription_id),
        request_id,
        value.status,
        value.error,
    )
