from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from time import time_ns
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, cast

from kairospy.contracts.market.events import MarketEventVariant
from kairospy.infrastructure.protocol import LiveEventSource
from kairospy.investment.apps.reference.application import (
    Instrument,
    InstrumentRef,
    Market,
)
from kairospy.primitives.reference import InstrumentId, MarketId, MarketIdRead
from kairospy.primitives.decimal import DecimalValue
from kairospy.primitives.market import SubscriptionId
from kairospy.primitives.runtime import InstanceId, LaunchId, RequestId, StrategyId
from kairospy.primitives.time import Sequence, UnixNanos

from kairospy.contracts.market.types import (
    MarketSubscriptionRequest as SubscriptionRequest,
    MarketCommands,
    MarketReleaseResultRead,
    MarketSnapshots,
    MarketSubscriptionResultRead,
    MarketTarget,
    ObservationRequirement,
    Options,
    Provider,
    ProviderPreference,
)
from kairospy.contracts.market.view import (
    MarketBarCurrent,
    MarketFreshnessCurrent,
    MarketFundingRateCurrent,
    MarketGreeksCurrent,
    MarketIndexPriceCurrent,
    MarketMarkPriceCurrent,
    MarketOpenInterestCurrent,
    MarketOrderBookCurrent,
    MarketQuoteCurrent,
    MarketRateCurrent,
    MarketTicker24hCurrent,
    MarketViewKey,
    MarketViewKind,
)
from .requests import MarketData

class _MarketReplaySource(Protocol):
    def replay_from(
        self, after_sequence: int = 0
    ) -> AsyncIterator[MarketEventVariant]: ...


if TYPE_CHECKING:
    from kairospy.strategy.api.market import (
        Bar,
        ObservationScope,
        OptionGreeks,
        Quote,
        Trade,
    )


MarketCurrentValue: TypeAlias = (
    MarketQuoteCurrent
    | MarketBarCurrent
    | MarketGreeksCurrent
    | MarketRateCurrent
    | MarketTicker24hCurrent
    | MarketMarkPriceCurrent
    | MarketFundingRateCurrent
    | MarketOpenInterestCurrent
    | MarketIndexPriceCurrent
    | MarketOrderBookCurrent
    | MarketFreshnessCurrent
    | None
)
if TYPE_CHECKING:
    SubscribedObservation: TypeAlias = Bar | Quote | Trade | OptionGreeks


class SubscriptionState(StrEnum):
    RESOLVING = "resolving"
    ACTIVE = "active"
    PARTIALLY_ACTIVE = "partially_active"
    WAITING_FOR_PROVIDER = "waiting_for_provider"
    WAITING_FOR_MARKET = "waiting_for_market"
    DEGRADED = "degraded"
    FAILED = "failed"
    RELEASED = "released"


class SubscriptionGroupState(StrEnum):
    ACTIVE = "active"
    PARTIALLY_ACTIVE = "partially_active"
    FAILED = "failed"
    RELEASED = "released"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class Subscription:
    subscription_id: SubscriptionId
    request_id: RequestId
    status: SubscriptionState
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SubscriptionGroup:
    """One strategy subscription intent and its Market-owned result."""

    subscriptions: tuple[Subscription, ...]

    def __post_init__(self) -> None:
        if not self.subscriptions:
            raise ValueError(
                "subscription group must contain at least one subscription"
            )

    @property
    def subscription_id(self) -> SubscriptionId:
        return self.subscriptions[0].subscription_id

    @property
    def request_id(self) -> RequestId:
        return self.subscriptions[0].request_id

    @property
    def status(self) -> SubscriptionGroupState:
        statuses = {subscription.status for subscription in self.subscriptions}
        if statuses == {SubscriptionState.ACTIVE}:
            return SubscriptionGroupState.ACTIVE
        if statuses == {SubscriptionState.RELEASED}:
            return SubscriptionGroupState.RELEASED
        if SubscriptionState.FAILED in statuses:
            return SubscriptionGroupState.FAILED
        if SubscriptionState.ACTIVE in statuses:
            return SubscriptionGroupState.PARTIALLY_ACTIVE
        return SubscriptionGroupState.PENDING


@dataclass(frozen=True, slots=True)
class SubscriptionStatus:
    """Market-owned status for one Strategy subscription request."""

    request_id: RequestId
    subscription_id: SubscriptionId | None
    status: SubscriptionState
    request: SubscriptionRequest
    response: MarketSubscriptionResultRead
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SubscriptionReleaseResult:
    """Result of releasing every subscription owned by this Strategy access."""

    request_id: RequestId
    status: Literal["applied"]
    removed_subscription_ids: tuple[SubscriptionId, ...]
    error: str | None = None


class MarketApplication:
    """Concrete strategy-facing Market use cases.

    The Python SDK has one Market implementation: the Unix command client and
    indexed current-view reader. Keeping those concrete dependencies here avoids a
    second port hierarchy inside the SDK while the Rust Market application
    remains the authoritative process boundary and state owner.
    """

    def __init__(
        self,
        commands: MarketCommands | None,
        snapshots: MarketSnapshots | None,
        live_source: LiveEventSource[MarketEventVariant] | None = None,
        *,
        replay_source: _MarketReplaySource | None = None,
        strategy_id: str,
        instance_id: str,
        launch_id: str | None = None,
    ) -> None:
        if not strategy_id.strip() or not instance_id.strip():
            raise ValueError("strategy_id and instance_id are required")
        if live_source is not None and replay_source is not None:
            raise ValueError("Market live and replay sources are mutually exclusive")
        self._commands = commands
        self._snapshots = snapshots
        self._live_source = live_source
        self._replay_source = replay_source
        self._event_cursor: int | None = None
        self._event_cursor_key: tuple[str, str, int] | None = None
        self._strategy_id = StrategyId(strategy_id)
        self._instance_id = InstanceId(instance_id)
        self._launch_id = LaunchId(launch_id) if launch_id is not None else None
        self._event_sequence: Sequence | None = None
        self._event_occurred_at_unix_nanos: UnixNanos | None = None
        self._notification_gap_count = 0
        self._notification_incarnation_change_count = 0
        self._event_source_ready = live_source is None
        self._request_counter = 0
        self._subscription_handles: dict[RequestId, MarketSubscriptionResultRead] = {}
        self._subscription_requests: dict[RequestId, SubscriptionRequest] = {}
        self._subscription_request_ids: dict[SubscriptionId, RequestId] = {}

    def check_event_source_ready(self) -> None:
        """Validate the configured Market event source without reading current state."""

        if self._event_source_ready:
            return
        self._event_source_ready = True

    def visit_live(
        self,
        visitor: Callable[[MarketEventVariant], None],
        *,
        fragment_limit: int = 64,
    ) -> int:
        """Poll live Market once on the Strategy thread."""

        if self._live_source is None:
            return 0
        cursor = self._event_cursor or 0

        def accept(record: MarketEventVariant) -> None:
            nonlocal cursor
            if self._accept_event(record, cursor=cursor, live=True):
                cursor = int(record.metadata.sequence)
                visitor(record)
            else:
                cursor = self._event_cursor or cursor

        return self._live_source.poll_visit(accept, fragment_limit=fragment_limit)

    async def replay_events(self) -> AsyncIterator[MarketEventVariant]:
        """Yield owned Market replay events from the explicit replay source."""

        if self._replay_source is None:
            raise RuntimeError("Market replay source is unavailable")
        from .events import EventStreamGap

        cursor = self._event_cursor or 0
        async for record in self._replay_source.replay_from(cursor):
            sequence = int(record.metadata.sequence)
            expected = cursor + 1
            if sequence > expected:
                raise EventStreamGap(record.metadata.stream_id, expected, sequence)
            if self._accept_event(record, cursor=cursor, live=False):
                cursor = sequence
                yield record
            else:
                cursor = self._event_cursor or cursor

    def _accept_event(
        self,
        record: MarketEventVariant,
        *,
        cursor: int,
        live: bool,
    ) -> bool:
        metadata = record.metadata
        stream_id = metadata.stream_id
        sequence = int(metadata.sequence)
        cursor_key = (
            stream_id,
            str(metadata.producer),
            int(metadata.producer_incarnation),
        )
        if stream_id != "market.events":
            raise RuntimeError(f"Market event stream identity is invalid: {stream_id}")
        if self._launch_id is not None and metadata.launch_id != self._launch_id:
            raise RuntimeError("Market event belongs to another launch")
        if self._launch_id is not None and metadata.instance_id != self._instance_id:
            raise RuntimeError("Market event belongs to another launch instance")
        if self._event_cursor_key is not None and cursor_key != self._event_cursor_key:
            self._notification_incarnation_change_count += 1
            cursor = sequence - 1
        elif self._event_cursor_key is None:
            cursor = sequence - 1 if live else cursor
        self._event_cursor_key = cursor_key
        if cursor == 0 and live:
            cursor = sequence - 1
        if sequence <= cursor:
            return False
        expected = cursor + 1
        if sequence != expected:
            if not live:
                from .events import EventStreamGap

                raise EventStreamGap(stream_id, expected, sequence)
            self._notification_gap_count += 1
        self._event_cursor = sequence
        if record.kind not in {
            "bar_completed",
            "quote_updated",
            "trade_occurred",
            "greeks_updated",
        }:
            return False
        return self._matches_subscription(record)

    def notification_health(self) -> dict[str, object]:
        """Return diagnostics for the non-authoritative live notification path."""

        return {
            "cursor": self._event_cursor,
            "producer": None
            if self._event_cursor_key is None
            else self._event_cursor_key[1],
            "producer_incarnation": None
            if self._event_cursor_key is None
            else self._event_cursor_key[2],
            "gap_count": self._notification_gap_count,
            "incarnation_change_count": self._notification_incarnation_change_count,
        }

    def close_live(self) -> None:
        if self._live_source is not None:
            self._live_source.close()

    @property
    def events_replayable(self) -> bool:
        return self._replay_source is not None

    @property
    def events_enabled(self) -> bool:
        """Whether this Strategy currently owns Market event demand."""

        return self.events_replayable or bool(self._subscription_requests)

    def bind_event(
        self,
        sequence: Sequence | int | None,
        occurred_at_unix_nanos: UnixNanos | int | None = None,
    ) -> None:
        """Bind command causation to the currently dispatched strategy event."""

        self._event_sequence = None if sequence is None else Sequence(sequence)
        self._event_occurred_at_unix_nanos = (
            None
            if occurred_at_unix_nanos is None
            else UnixNanos(occurred_at_unix_nanos)
        )

    def subscribe_bars(
        self,
        market: Market | MarketId | MarketIdRead,
        *,
        timeframe: str,
        provider_preference: ProviderPreference | None = None,
    ) -> Subscription | SubscriptionGroup:
        if not timeframe.strip():
            raise ValueError("bar timeframe is required")
        return self.subscribe(
            market,
            data=[MarketData.bar(timeframe)],
            provider_preference=provider_preference,
        )

    def subscribe_quotes(
        self,
        market: Market | MarketId | MarketIdRead,
        *,
        provider_preference: ProviderPreference | None = None,
    ) -> Subscription | SubscriptionGroup:
        return self.subscribe(
            market,
            data=[MarketData.QUOTE],
            provider_preference=provider_preference,
        )

    def subscribe(
        self,
        market: Market | MarketId | MarketIdRead | Options,
        *,
        data: tuple[MarketData | str, ...] | list[MarketData | str],
        provider_preference: ProviderPreference | None = None,
    ) -> SubscriptionGroup:
        """Subscribe to typed Market data for one explicit market identity."""

        observations = tuple(
            ObservationRequirement.from_selector(
                value.selector if isinstance(value, MarketData) else str(value)
            )
            for value in data
        )
        if not observations:
            raise ValueError("market subscription data selectors are required")
        preference = provider_preference or ProviderPreference.automatic()
        if isinstance(market, Options):
            spot: Decimal | None = None
            strike = market.filter.strike
            if strike is not None and strike.mode == "around_spot":
                underlying = market.underlying
                if not underlying.startswith("market:"):
                    raise RuntimeError(
                        "around_spot option subscription requires a MarketId underlying "
                        "so the current quote can be read"
                    )
                requested_provider = (
                    preference.providers[0]
                    if preference.mode in {"prefer", "require"} and preference.providers
                    else None
                )
                quote = self.latest_quote(
                    MarketId(underlying), provider=requested_provider
                )
                if quote is None:
                    raise RuntimeError(
                        "around_spot option subscription requires a current underlying quote"
                    )
                spot = _quote_midpoint(quote)
            target = market.to_target(
                spot=None if spot is None else str(spot),
                now_unix_nanos=self._event_occurred_at_unix_nanos or time_ns(),
            )
            subscription = self._subscribe(
                SubscriptionRequest(target, observations, preference)
            )
            return SubscriptionGroup((subscription,))
        subscription = self._subscribe(
            SubscriptionRequest(
                MarketTarget.market(str(_market_id(market))),
                observations,
                preference,
            )
        )
        return SubscriptionGroup((subscription,))

    def subscribe_consolidated_quotes(
        self,
        instrument: Instrument | InstrumentRef | InstrumentId,
        *,
        network_id: str | None = None,
        provider_preference: ProviderPreference | None = None,
    ) -> Subscription:
        """Subscribe to an instrument-wide quote route without inventing a Market."""

        instrument_id = (
            instrument.id
            if isinstance(instrument, (Instrument, InstrumentRef))
            else instrument
        )
        return self._subscribe(
            SubscriptionRequest(
                MarketTarget.consolidated_instrument(str(instrument_id), network_id),
                (ObservationRequirement("quote"),),
                provider_preference or ProviderPreference.automatic(),
            )
        )

    def subscribe_trades(
        self,
        market: Market | MarketId | MarketIdRead,
        *,
        provider_preference: ProviderPreference | None = None,
    ) -> Subscription:
        return self.subscribe(
            market,
            data=[MarketData.TRADE],
            provider_preference=provider_preference,
        ).subscriptions[0]

    def subscribe_greeks(
        self,
        market: Market | MarketId | MarketIdRead,
        *,
        provider_preference: ProviderPreference | None = None,
    ) -> Subscription:
        return self.subscribe(
            market,
            data=[MarketData.GREEKS],
            provider_preference=provider_preference,
        ).subscriptions[0]

    def unsubscribe(self, subscription: Subscription | SubscriptionGroup) -> None:
        if isinstance(subscription, SubscriptionGroup):
            for item in subscription.subscriptions:
                self.unsubscribe(item)
            return
        request_id = self._request_id("market.unsubscribe")
        self._required_commands().unsubscribe(
            str(subscription.subscription_id),
            strategy_id=str(self._strategy_id),
            instance_id=str(self._instance_id),
            request_id=str(request_id),
            launch_id=None if self._launch_id is None else str(self._launch_id),
        )
        owner_request_id = self._subscription_request_ids.pop(
            subscription.subscription_id, None
        )
        if owner_request_id is not None:
            self._subscription_requests.pop(owner_request_id, None)

    def latest_bar(
        self,
        market: Market | MarketId | MarketIdRead,
        *,
        timeframe: str,
        provider: Provider | str | None = None,
    ) -> Bar | None:
        value = self._required_snapshots().bar(
            str(_market_id(market)), self._provider_for(market, provider), timeframe
        )
        if value is None:
            return None
        if not isinstance(value, MarketBarCurrent):
            raise TypeError("Market bar view returned a non-native value")
        return cast("Bar", value)

    def latest_quote(
        self,
        market: Market | MarketId | MarketIdRead,
        *,
        provider: Provider | str | None = None,
    ) -> Quote | None:
        value = self._required_snapshots().quote(
            str(_market_id(market)), self._provider_for(market, provider)
        )
        if value is None:
            return None
        if not isinstance(value, MarketQuoteCurrent):
            raise TypeError("Market quote view returned a non-native value")
        return cast("Quote", value)

    def latest_greeks(
        self,
        market: Market | MarketId | MarketIdRead,
        *,
        provider: Provider | str | None = None,
    ) -> OptionGreeks | None:
        value = self._required_snapshots().greeks(
            str(_market_id(market)), self._provider_for(market, provider)
        )
        if value is None:
            return None
        if not isinstance(value, MarketGreeksCurrent):
            raise TypeError("Market Greeks view returned a non-native value")
        return cast("OptionGreeks", value)

    def current_view(
        self,
        market: Market | MarketId | MarketIdRead,
        *,
        provider: Provider | str | None = None,
        kind: MarketViewKind | str,
        qualifier: str | None = None,
    ) -> MarketCurrentValue:
        """Read one independent Market v2 current-view resource.

        Current views are partitioned by market, provider, data kind, and an
        optional qualifier. This API intentionally does not reconstruct an
        aggregate snapshot when one field changes.
        """

        kind_value = kind.value if isinstance(kind, MarketViewKind) else kind
        value = self._required_snapshots().get(
            MarketViewKey(
                str(_market_id(market)),
                self._provider_for(market, provider),
                kind_value,
                qualifier,
            )
        )
        if value is None or isinstance(
            value,
            (
                MarketQuoteCurrent,
                MarketBarCurrent,
                MarketGreeksCurrent,
                MarketRateCurrent,
                MarketTicker24hCurrent,
                MarketMarkPriceCurrent,
                MarketFundingRateCurrent,
                MarketOpenInterestCurrent,
                MarketIndexPriceCurrent,
                MarketOrderBookCurrent,
                MarketFreshnessCurrent,
            ),
        ):
            return value
        raise TypeError("Market current view returned a non-native value")

    def _provider_for(
        self,
        market: Market | MarketId | MarketIdRead,
        requested: Provider | str | None,
    ) -> str:
        if requested is not None:
            value = str(requested).strip().lower()
            if not value:
                raise ValueError("Market provider is required when specified")
            return value
        market_id = str(_market_id(market))
        providers: set[str] = set()
        for request_id, request in self._subscription_requests.items():
            if request.target.kind != "market":
                continue
            if request.target.market_id != market_id:
                continue
            response = self._command_status(request_id)
            providers.update(
                str(value)
                for value in response.resolved_providers
                if str(value).strip()
            )
        if len(providers) == 1:
            return next(iter(providers))
        if not providers:
            raise RuntimeError(
                f"Market {market_id} has no resolved active provider; subscribe before reading"
            )
        raise RuntimeError(
            f"Market {market_id} has multiple active providers; specify provider explicitly"
        )

    def _subscribe(self, request: SubscriptionRequest) -> Subscription:
        request_id = self._request_id("market.subscribe")
        handle = self._required_commands().subscribe(
            request,
            strategy_id=str(self._strategy_id),
            instance_id=str(self._instance_id),
            request_id=str(request_id),
            launch_id=None if self._launch_id is None else str(self._launch_id),
        )
        self._subscription_handles[request_id] = handle
        self._subscription_requests[request_id] = request
        subscription = _subscription(handle, request_id=request_id)
        self._subscription_request_ids[subscription.subscription_id] = request_id
        return subscription

    def _request_id(self, operation: str) -> RequestId:
        self._request_counter += 1
        return RequestId(
            f"{self._strategy_id}:{self._instance_id}:{operation}:"
            f"{self._event_sequence or 0}:{self._request_counter}"
        )

    def subscription_status(self, request_id: RequestId | str) -> SubscriptionStatus:
        """Return a business status without exposing the transport result."""

        request_key = (
            request_id if isinstance(request_id, RequestId) else RequestId(request_id)
        )
        try:
            request = self._subscription_requests[request_key]
        except KeyError as error:
            raise KeyError(
                f"unknown Market subscription request: {request_key}"
            ) from error
        handle = self._command_status(request_key)
        subscription_id = handle.subscription_id
        return SubscriptionStatus(
            request_id=request_key,
            subscription_id=SubscriptionId(subscription_id),
            status=SubscriptionState(handle.state),
            request=request,
            response=handle,
            error=None,
        )

    def subscription_statuses(self) -> tuple[SubscriptionStatus, ...]:
        """Return statuses in the deterministic order requests were created."""

        return tuple(
            self.subscription_status(request_id)
            for request_id in self._subscription_requests
        )

    def release_strategy_subscriptions(self) -> SubscriptionReleaseResult:
        """Release the owner-scoped Market demand for this Strategy instance."""

        request_id, handle = self._release_owner()
        removed_ids = tuple(handle.released_subscription_ids)
        self._subscription_requests.clear()
        self._subscription_request_ids.clear()
        return SubscriptionReleaseResult(
            request_id=request_id,
            status="applied",
            removed_subscription_ids=tuple(
                SubscriptionId(value) for value in removed_ids
            ),
            error=None,
        )

    def _release_owner(self) -> tuple[RequestId, MarketReleaseResultRead]:
        request_id = self._request_id("market.release_owner")
        response = self._required_commands().release_owner(
            strategy_id=str(self._strategy_id),
            instance_id=str(self._instance_id),
            request_id=str(request_id),
            launch_id=None if self._launch_id is None else str(self._launch_id),
        )
        return request_id, response

    def _command_status(self, request_id: RequestId) -> MarketSubscriptionResultRead:
        return self._subscription_handles[request_id]

    def _required_commands(self) -> MarketCommands:
        if self._commands is None:
            raise RuntimeError("Market command capability is unavailable")
        return self._commands

    def _required_snapshots(self) -> MarketSnapshots:
        if self._snapshots is None:
            raise RuntimeError("Market current view is unavailable")
        return self._snapshots

    def _matches_subscription(self, event: MarketEventVariant) -> bool:
        """Apply this Strategy instance's requested Market demand."""

        if self.events_replayable or not self._subscription_requests:
            return True
        data = cast("SubscribedObservation", event.data)
        scope_key = _scope_key(data.scope)
        selector = {
            "quote_updated": "quote",
            "trade_occurred": "trade",
            "greeks_updated": "greeks",
        }.get(event.kind, event.kind)
        if event.kind == "bar_completed":
            selector = f"bar:{cast('Bar', data).bar_spec_id}"
        return any(
            _request_accepts_scope(request, scope_key, event)
            and selector
            in {observation.selector for observation in request.observations}
            for request in self._subscription_requests.values()
        )


def _market_id(value: Market | MarketId | MarketIdRead) -> MarketId | MarketIdRead:
    return value.id if isinstance(value, Market) else value


def _quote_midpoint(quote: Quote) -> Decimal:
    bid = _decimal_value(quote.bid_price)
    ask = _decimal_value(quote.ask_price)
    if bid is not None and ask is not None:
        return (bid + ask) / Decimal("2")
    if bid is not None:
        return bid
    if ask is not None:
        return ask
    raise RuntimeError("around_spot option subscription requires a priced quote")


def _decimal_value(value: DecimalValue | None) -> Decimal | None:
    if value is None:
        return None
    return value.value


def _request_accepts_scope(
    request: SubscriptionRequest, scope_key: str, event: MarketEventVariant
) -> bool:
    target = request.target
    if target.kind == "market":
        return target.market_id == scope_key
    if target.kind == "consolidated_instrument":
        return (
            f"consolidated:{target.instrument_id}:{target.network_id or '*'}"
            == scope_key
        )
    return str(event.data.instrument_id).startswith("instrument:option:")


def _scope_key(scope: ObservationScope) -> str:
    market_id = scope.market_id
    if market_id is not None:
        return str(market_id)
    return (
        f"consolidated:{scope.instrument_id}:"
        f"{scope.network_id or '*'}"
    )


def _subscription(
    value: MarketSubscriptionResultRead, *, request_id: RequestId
) -> Subscription:
    return Subscription(
        SubscriptionId(value.subscription_id),
        request_id,
        SubscriptionState(value.state),
        None,
    )
