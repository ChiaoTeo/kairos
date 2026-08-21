from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

from kairospy.application.reference import Instrument, InstrumentRef, Market
from kairospy.domain_types import InstrumentId, MarketId

from .events import BarEvent, MarketEvent, TradeEvent
from .models import Bar, ObservationScope, OptionGreeks, Quote, Trade
from .requests import (
    MarketData,
    OptionFilter,
    Options,
    ParticipantSet,
    SourceSet,
    StrikeRange,
    SubscriptionRequest,
)


@dataclass(frozen=True, slots=True)
class Subscription:
    subscription_id: str
    request_id: str
    status: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SubscriptionGroup:
    """A strategy subscription intent expanded into one or more source requests."""

    subscriptions: tuple[Subscription, ...]

    def __post_init__(self) -> None:
        if not self.subscriptions:
            raise ValueError("subscription group must contain at least one subscription")

    @property
    def subscription_id(self) -> str:
        return self.subscriptions[0].subscription_id

    @property
    def request_id(self) -> str:
        return self.subscriptions[0].request_id

    @property
    def status(self) -> str:
        statuses = {subscription.status for subscription in self.subscriptions}
        if statuses == {"accepted"}:
            return "accepted"
        if "rejected" in statuses:
            return "rejected"
        if "accepted" in statuses:
            return "partial"
        return self.subscriptions[0].status


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
        self._latest_trades: dict[str, Trade] = {}
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
            records = cast(AsyncIterator[Any], subscribe_live())
            live = True
        else:
            records = cast(AsyncIterator[Any], self._event_source.replay_from(cursor))
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
                self._latest_trades[event.data.scope.key()] = event.data
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
        self,
        market: Market | MarketId,
        *,
        timeframe: str,
        source_id: str | None = None,
        sources: SourceSet | tuple[str, ...] | list[str] | None = None,
        participants: ParticipantSet | tuple[str, ...] | list[str] | None = None,
    ) -> Subscription | SubscriptionGroup:
        if sum(value is not None for value in (source_id, sources, participants)) > 1:
            raise ValueError("use only one of source_id, sources, or participants")
        if not timeframe.strip():
            raise ValueError("bar timeframe is required")
        if sources is not None or participants is not None:
            return self.subscribe(
                market,
                data=[MarketData.bar(timeframe)],
                sources=sources,
                participants=participants,
            )
        return self._subscribe(
            _market_subscription_request(
                market, (f"bar:{timeframe}",), source_id=source_id
            )
        )

    def subscribe_quotes(
        self,
        market: Market | MarketId,
        *,
        source_id: str | None = None,
        sources: SourceSet | tuple[str, ...] | list[str] | None = None,
        participants: ParticipantSet | tuple[str, ...] | list[str] | None = None,
    ) -> Subscription | SubscriptionGroup:
        if sum(value is not None for value in (source_id, sources, participants)) > 1:
            raise ValueError("use only one of source_id, sources, or participants")
        if sources is not None or participants is not None:
            return self.subscribe(
                market,
                data=[MarketData.QUOTE],
                sources=sources,
                participants=participants,
            )
        return self._subscribe(
            _market_subscription_request(market, ("quote",), source_id=source_id)
        )

    def subscribe(
        self,
        market: Market | MarketId | Options,
        *,
        data: tuple[MarketData | str, ...] | list[MarketData | str],
        sources: SourceSet | tuple[str, ...] | list[str] | None = None,
        participants: ParticipantSet | tuple[str, ...] | list[str] | None = None,
    ) -> SubscriptionGroup:
        """Subscribe to typed Market data for one explicit market identity."""

        if sources is not None and participants is not None:
            raise ValueError("use either sources or participants, not both")
        selectors = tuple(_selector(value) for value in data)
        if not selectors:
            raise ValueError("market subscription data selectors are required")
        if isinstance(market, Options):
            market = self._resolve_spot_relative_options_target(market)
            source_ids = self._source_ids(market, sources, participants)
            subscription = self._subscribe(
                _options_subscription_request(
                    market,
                    selectors,
                    source_ids=tuple(source_id for source_id in source_ids if source_id),
                )
            )
            return SubscriptionGroup((subscription,))
        source_ids = self._source_ids(market, sources, participants)
        subscription = self._subscribe(
            _market_subscription_request(
                market,
                selectors,
                source_id=source_ids[0] if source_ids == (None,) else None,
                source_ids=tuple(source_id for source_id in source_ids if source_id),
            )
        )
        return SubscriptionGroup((subscription,))

    def _resolve_spot_relative_options_target(self, target: Options) -> Options:
        strike = target.filter.strike
        if strike is None or strike.mode != "around_spot":
            return target
        if strike.percent is None:
            raise ValueError("around_spot strike range requires a positive percent")
        if not isinstance(target.underlying, (Market, MarketId)):
            raise RuntimeError(
                "around_spot option subscription requires a MarketId or Reference Market "
                "underlying so the current quote can be read"
            )
        underlying_market_id = _market_id(target.underlying)
        quote = self.latest_quote(underlying_market_id)
        if quote is None:
            raise RuntimeError(
                "around_spot option subscription requires a current underlying quote"
            )
        spot = _quote_midpoint(quote)
        lower = spot * (Decimal("1") - strike.percent)
        upper = spot * (Decimal("1") + strike.percent)
        resolved_filter = OptionFilter(
            expiry=target.filter.expiry,
            strike=StrikeRange.between(lower, upper),
            right=target.filter.right,
            limit=target.filter.limit,
        )
        return Options(target.underlying, resolved_filter)

    def subscribe_quotes_for_sources(
        self,
        market: Market | MarketId,
        *,
        sources: SourceSet | tuple[str, ...] | list[str] | None = None,
        participants: ParticipantSet | tuple[str, ...] | list[str] | None = None,
    ) -> SubscriptionGroup:
        return self.subscribe(
            market,
            data=[MarketData.QUOTE],
            sources=sources,
            participants=participants,
        )

    def subscribe_consolidated_quotes(
        self,
        instrument: Instrument | InstrumentRef | InstrumentId,
        *,
        provider_id: str,
        provider_product: str,
        subscription_symbol: str,
        source_id: str,
        network_id: str | None = None,
    ) -> Subscription:
        """Subscribe to an instrument-wide quote route without inventing a Market."""

        instrument_id = (
            instrument.id
            if isinstance(instrument, (Instrument, InstrumentRef))
            else instrument
        )
        params: dict[str, object] = {
            "scope": "consolidated",
            "instrument_id": str(instrument_id),
            "provider_id": provider_id,
        }
        if network_id is not None:
            params["network_id"] = network_id
        return self._subscribe(
            SubscriptionRequest(
                subject=subscription_symbol,
                selectors=("quote",),
                identity=ObservationScope.consolidated(instrument_id, network_id).key(),
                source_id=source_id,
                market_type=provider_product,
                params=params,
            )
        )

    def subscribe_trades(
        self, market: Market | MarketId, *, source_id: str | None = None
    ) -> Subscription:
        return self._subscribe(
            _market_subscription_request(market, ("trade",), source_id=source_id)
        )

    def subscribe_greeks(
        self, market: Market | MarketId, *, source_id: str | None = None
    ) -> Subscription:
        return self._subscribe(
            _market_subscription_request(market, ("greeks",), source_id=source_id)
        )

    def unsubscribe(self, subscription: Subscription | SubscriptionGroup) -> None:
        if isinstance(subscription, SubscriptionGroup):
            for item in subscription.subscriptions:
                self.unsubscribe(item)
            return
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
            return cast(
                Bar | None,
                reader(
                    str(_market_id(market)), source_id or self._source_id, timeframe
                ),
            )
        raise RuntimeError("Market v2 bar view reader is unavailable")

    def latest_quote(
        self, market: Market | MarketId, *, source_id: str | None = None
    ) -> Quote | None:
        reader = getattr(self._snapshots, "read_quote", None)
        if callable(reader):
            return cast(
                Quote | None,
                reader(str(_market_id(market)), source_id or self._source_id),
            )
        raise RuntimeError("Market v2 quote view reader is unavailable")

    def latest_trade(self, market: Market | MarketId) -> Trade | None:
        """Return the latest consumed trade event.

        Trade is event-only in Market v2 and has no mmap current-view
        resource. The result is therefore available after the event stream has
        delivered a trade, rather than through an aggregate snapshot read.
        """

        return self._latest_trades.get(str(_market_id(market)))

    def latest_greeks(
        self, market: Market | MarketId, *, source_id: str | None = None
    ) -> OptionGreeks | None:
        reader = getattr(self._snapshots, "read_greeks", None)
        if callable(reader):
            return cast(
                OptionGreeks | None,
                reader(str(_market_id(market)), source_id or self._source_id),
            )
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

    def _source_ids(
        self,
        market: Market | MarketId | Options,
        sources: SourceSet | tuple[str, ...] | list[str] | None,
        participants: ParticipantSet | tuple[str, ...] | list[str] | None,
    ) -> tuple[str | None, ...]:
        if participants is not None:
            return self._source_ids_for_participants(market, participants)
        if sources is None:
            return (None,)
        if isinstance(sources, SourceSet):
            if sources.all:
                discovered = self._discover_source_ids(market)
                if not discovered:
                    raise RuntimeError(
                        "Market source discovery is unavailable; pass explicit sources"
                    )
                return discovered
            assert sources.source_ids is not None
            return sources.source_ids
        source_ids = tuple(_wire_value(source) for source in sources)
        if not source_ids:
            raise ValueError("sources must not be empty")
        if any(not source_id.strip() for source_id in source_ids):
            raise ValueError("sources must be non-empty strings")
        return source_ids

    def _source_ids_for_participants(
        self,
        market: Market | MarketId | Options,
        participants: ParticipantSet | tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        discovered = self._discover_source_ids(market)
        if not discovered:
            raise RuntimeError(
                "Market participant routing is unavailable; source discovery returned no sources"
            )
        if isinstance(participants, ParticipantSet):
            if participants.all:
                return discovered
            assert participants.participant_ids is not None
            participant_ids = participants.participant_ids
        else:
            participant_ids = tuple(_participant_id(participant) for participant in participants)
            if not participant_ids:
                raise ValueError("participants must not be empty")
        selected = tuple(
            source_id
            for source_id in discovered
            if any(_source_belongs_to_participant(source_id, participant_id) for participant_id in participant_ids)
        )
        if not selected:
            wanted = ", ".join(participant_ids)
            raise RuntimeError(f"Market has no configured sources for participants: {wanted}")
        return selected

    def _discover_source_ids(self, market: Market | MarketId | Options) -> tuple[str, ...]:
        data_sources = getattr(self._commands, "data_sources", None)
        if not callable(data_sources):
            return ()
        query = _source_discovery_query(market)
        value = data_sources(query)
        sources = value.get("sources", ()) if isinstance(value, Mapping) else ()
        if not isinstance(sources, (list, tuple)):
            return ()
        result = []
        for source in sources:
            if not isinstance(source, Mapping):
                continue
            source_id = source.get("source_id")
            if isinstance(source_id, str) and source_id.strip():
                result.append(source_id)
        return tuple(result)

    def _matches_subscription(self, event: MarketEvent) -> bool:
        """Apply this Strategy instance's requested Market demand."""

        if self.events_replayable or not self._subscription_requests:
            return True
        scope_key = event.data.scope.key()
        selector = (
            f"bar:{event.data.timeframe}" if isinstance(event, BarEvent) else event.kind
        )
        return any(
            _request_accepts_scope(request, scope_key, event)
            and selector in request.selectors
            and _request_accepts_source(request, event.data.source_id)
            for request in self._subscription_requests.values()
        )


def _market_id(value: Market | MarketId) -> MarketId:
    return value.id if isinstance(value, Market) else value


def _selector(value: MarketData | str) -> str:
    return value.selector if isinstance(value, MarketData) else str(value)


def _quote_midpoint(quote: Quote) -> Decimal:
    if quote.bid_price is not None and quote.ask_price is not None:
        return (quote.bid_price + quote.ask_price) / Decimal("2")
    if quote.bid_price is not None:
        return quote.bid_price
    if quote.ask_price is not None:
        return quote.ask_price
    raise RuntimeError("around_spot option subscription requires a priced quote")


def _wire_value(value: object) -> str:
    enum_value = getattr(value, "value", None)
    return enum_value if isinstance(enum_value, str) else str(value)


def _participant_id(value: object) -> str:
    text = _wire_value(value).strip().lower()
    if text.startswith("data_provider:"):
        return text.removeprefix("data_provider:")
    if text.startswith("provider:"):
        return text.removeprefix("provider:")
    return text


def _source_belongs_to_participant(source_id: str, participant_id: str) -> bool:
    participant_id = _participant_id(participant_id)
    source_id = source_id.strip().lower()
    return source_id == participant_id or source_id.startswith(f"{participant_id}-")


def _request_accepts_source(
    request: SubscriptionRequest, event_source_id: str | None
) -> bool:
    if request.source_id is None and not request.source_ids:
        return True
    if event_source_id is None:
        return False
    if request.source_id is not None:
        return event_source_id == request.source_id
    return event_source_id in request.source_ids


def _request_accepts_scope(
    request: SubscriptionRequest, scope_key: str, event: MarketEvent
) -> bool:
    if (request.identity or request.subject) == scope_key:
        return True
    if request.params.get("target") != "options":
        return False
    instrument_id = str(event.data.instrument.id)
    return instrument_id.startswith("instrument:option:")


def _market_subscription_request(
    market: Market | MarketId,
    selectors: tuple[str, ...],
    *,
    source_id: str | None,
    source_ids: tuple[str, ...] = (),
) -> SubscriptionRequest:
    market_id = _market_id(market)
    if isinstance(market, Market):
        return SubscriptionRequest(
            subject=str(market_id),
            selectors=selectors,
            identity=str(market_id),
            source_id=source_id,
            source_ids=source_ids,
            params={"market_id": str(market_id)},
        )
    return SubscriptionRequest(
        subject=str(market_id),
        selectors=selectors,
        identity=str(market_id),
        source_id=source_id,
        source_ids=source_ids,
    )


def _options_subscription_request(
    target: Options,
    selectors: tuple[str, ...],
    *,
    source_ids: tuple[str, ...],
) -> SubscriptionRequest:
    underlying_params = _underlying_params(target.underlying)
    subject = f"options:{next(iter(underlying_params.values()))}"
    params: dict[str, object] = {
        "target": "options",
        "mode": "chain",
        **underlying_params,
        "filter": target.filter.params(),
    }
    return SubscriptionRequest(
        subject=subject,
        selectors=selectors,
        source_ids=source_ids,
        params=params,
        dynamic=True,
    )


def _source_discovery_query(target: Market | MarketId | Options) -> dict[str, object]:
    if isinstance(target, Options):
        query: dict[str, object] = {"target": "options"}
        query.update(_underlying_params(target.underlying))
        return query
    return {"market_id": str(_market_id(target))}


def _underlying_params(underlying: object) -> dict[str, str]:
    if isinstance(underlying, Market):
        return {"underlying_market_id": str(underlying.id)}
    if isinstance(underlying, MarketId):
        return {"underlying_market_id": str(underlying)}
    if isinstance(underlying, (Instrument, InstrumentRef)):
        return {"underlying_instrument_id": str(underlying.id)}
    if isinstance(underlying, InstrumentId):
        return {"underlying_instrument_id": str(underlying)}
    value = str(underlying).strip()
    if not value:
        raise ValueError("options underlying is required")
    if value.startswith("market:"):
        return {"underlying_market_id": value}
    if value.startswith("instrument:"):
        return {"underlying_instrument_id": value}
    return {"underlying": value.upper()}


def _subscription(value: Any) -> Subscription:
    request_id = value.request_id
    subscription_id = value.result.get("subscription_id", request_id)
    return Subscription(
        str(subscription_id),
        request_id,
        value.status,
        value.error,
    )
