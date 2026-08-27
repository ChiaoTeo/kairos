from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from time import time_ns
from typing import TYPE_CHECKING, Any, cast

from kairospy.investment.apps.reference.application import (
    Instrument,
    InstrumentRef,
    Market,
)
from kairospy.primitives.reference import InstrumentId, MarketId

from kairospy.infrastructure.contracts.market.events import (
    MarketEvent as NativeMarketEvent,
)
from kairospy.infrastructure.contracts.market.types import (
    MarketSubscriptionRequest as SubscriptionRequest,
    MarketTarget,
    ObservationRequirement,
    Options,
    Provider,
    ProviderPreference,
)
from kairospy.infrastructure.contracts.market.view import (
    MarketBarCurrent,
    MarketGreeksCurrent,
    MarketQuoteCurrent,
    MarketViewKey,
)
from .requests import MarketData

if TYPE_CHECKING:
    from kairospy.strategy.api.market import (
        Bar,
        MarketEvent,
        OptionGreeks,
        Quote,
        Trade,
    )


@dataclass(frozen=True, slots=True)
class Subscription:
    subscription_id: str
    request_id: str
    status: str
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
    response: object
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
    indexed current-view reader. Keeping those concrete dependencies here avoids a
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
        self._event_sequence: int | None = None
        self._event_occurred_at_unix_nanos: int | None = None
        self._latest_trades: dict[str, object] = {}
        self._event_source_ready = event_source is None
        self._request_counter = 0
        self._handles: dict[str, Any] = {}
        self._subscription_requests: dict[str, SubscriptionRequest] = {}
        self._subscription_request_ids: dict[str, str] = {}

    def check_event_source_ready(self) -> None:
        """Validate the configured Market event source without reading current state."""

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
        from .events import EventStreamGap

        cursor = self._event_cursor or 0
        subscribe_live = getattr(self._event_source, "subscribe_live", None)
        if callable(subscribe_live):
            records = cast(AsyncIterator[Any], subscribe_live())
            live = True
        else:
            records = cast(AsyncIterator[Any], self._event_source.replay_from(cursor))
            live = False
        async for record in records:
            if not isinstance(record, NativeMarketEvent):
                raise TypeError(
                    "Market event source must yield owner-native MarketEvent values"
                )
            stream_id = record.metadata.stream_id
            sequence = record.metadata.sequence
            if stream_id != "market.events":
                raise RuntimeError(
                    f"Market event stream identity is invalid: {stream_id}"
                )
            if self._launch_id is not None and record.launch_id != self._launch_id:
                raise RuntimeError("Market event belongs to another launch")
            if self._launch_id is not None and record.instance_id != self._instance_id:
                raise RuntimeError("Market event belongs to another launch instance")
            if cursor == 0 and live:
                cursor = sequence - 1
            if sequence <= cursor:
                continue
            expected = cursor + 1
            if sequence != expected:
                raise EventStreamGap(stream_id, expected, sequence)
            cursor = sequence
            self._event_cursor = cursor
            if record.kind not in {"bar", "quote", "trade", "greeks"}:
                continue
            event = cast("MarketEvent", record)
            if event.kind == "trade":
                trade = cast("Trade", event.data)
                self._latest_trades[_scope_key(trade.scope)] = trade
            if self._matches_subscription(event):
                yield event

    @property
    def events_replayable(self) -> bool:
        return bool(getattr(self._event_source, "replayable", False))

    @property
    def events_enabled(self) -> bool:
        """Whether this Strategy currently owns Market event demand."""

        return self.events_replayable or bool(self._subscription_requests)

    def bind_event(
        self, sequence: int | None, occurred_at_unix_nanos: int | None = None
    ) -> None:
        """Bind command causation to the currently dispatched strategy event."""

        self._event_sequence = sequence
        self._event_occurred_at_unix_nanos = occurred_at_unix_nanos

    def subscribe_bars(
        self,
        market: Market | MarketId,
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
        market: Market | MarketId,
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
        market: Market | MarketId | Options,
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
        market: Market | MarketId,
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
        market: Market | MarketId,
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
        handle = self._commands.unsubscribe(
            subscription.subscription_id,
            strategy_id=self._strategy_id,
            instance_id=self._instance_id,
            request_id=request_id,
            launch_id=self._launch_id,
        )
        self._handles[request_id] = handle
        owner_request_id = self._subscription_request_ids.pop(
            subscription.subscription_id, None
        )
        if owner_request_id is not None:
            self._subscription_requests.pop(owner_request_id, None)

    def latest_bar(
        self,
        market: Market | MarketId,
        *,
        timeframe: str,
        provider: Provider | str | None = None,
    ) -> Bar | None:
        value = self._snapshots.bar(
            str(_market_id(market)), self._provider_for(market, provider), timeframe
        )
        if value is None:
            return None
        if not isinstance(value, MarketBarCurrent):
            raise TypeError("Market bar view returned a non-native value")
        return cast("Bar", value)

    def latest_quote(
        self,
        market: Market | MarketId,
        *,
        provider: Provider | str | None = None,
    ) -> Quote | None:
        value = self._snapshots.quote(
            str(_market_id(market)), self._provider_for(market, provider)
        )
        if value is None:
            return None
        if not isinstance(value, MarketQuoteCurrent):
            raise TypeError("Market quote view returned a non-native value")
        return cast("Quote", value)

    def latest_trade(self, market: Market | MarketId) -> Trade | None:
        """Return the latest consumed trade event.

        Trade is event-only in Market v2 and has no indexed current-view
        resource. The result is therefore available after the event stream has
        delivered a trade, rather than through an aggregate snapshot read.
        """

        return cast("Trade | None", self._latest_trades.get(str(_market_id(market))))

    def latest_greeks(
        self,
        market: Market | MarketId,
        *,
        provider: Provider | str | None = None,
    ) -> OptionGreeks | None:
        value = self._snapshots.greeks(
            str(_market_id(market)), self._provider_for(market, provider)
        )
        if value is None:
            return None
        if not isinstance(value, MarketGreeksCurrent):
            raise TypeError("Market Greeks view returned a non-native value")
        return cast("OptionGreeks", value)

    def current_view(
        self,
        market: Market | MarketId,
        *,
        provider: Provider | str | None = None,
        kind: Any,
        qualifier: str | None = None,
    ):
        """Read one independent Market v2 current-view resource.

        Current views are partitioned by market, provider, data kind, and an
        optional qualifier. This API intentionally does not reconstruct an
        aggregate snapshot when one field changes.
        """

        return self._snapshots.get(MarketViewKey(
            str(_market_id(market)), self._provider_for(market, provider), kind, qualifier
        ))

    def _provider_for(
        self,
        market: Market | MarketId,
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
        handle = self._commands.subscribe(
            request,
            strategy_id=self._strategy_id,
            instance_id=self._instance_id,
            request_id=request_id,
            launch_id=self._launch_id,
        )
        self._handles[request_id] = handle
        self._subscription_requests[request_id] = request
        subscription = _subscription(handle, request_id=request_id)
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
        subscription_id = handle.subscription_id
        return SubscriptionStatus(
            request_id=request_id,
            subscription_id=str(subscription_id),
            status=handle.state,
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
            removed_subscription_ids=removed_ids,
            error=None,
        )

    def _release_owner(self) -> Any:
        request_id = self._request_id("market.release_owner")
        response = self._commands.release_owner(
            strategy_id=self._strategy_id,
            instance_id=self._instance_id,
            request_id=request_id,
            launch_id=self._launch_id,
        )
        self._handles[request_id] = response
        return request_id, response

    def _command_status(self, request_id: str) -> Any:
        """Allow deterministic application-protocol fakes to advance state."""

        status = getattr(self._commands, "status", None)
        if callable(status):
            return status(request_id)
        return self._handles[request_id]

    def _matches_subscription(self, event: MarketEvent) -> bool:
        """Apply this Strategy instance's requested Market demand."""

        if self.events_replayable or not self._subscription_requests:
            return True
        data = event.data
        scope_key = _scope_key(getattr(data, "scope"))
        selector = (
            f"bar:{getattr(data, 'bar_spec_id')}" if event.kind == "bar" else event.kind
        )
        return any(
            _request_accepts_scope(request, scope_key, event)
            and selector
            in {observation.selector for observation in request.observations}
            for request in self._subscription_requests.values()
        )


def _market_id(value: Market | MarketId) -> MarketId:
    return value.id if isinstance(value, Market) else value


def _quote_midpoint(quote: Quote) -> Decimal:
    bid = _native_decimal(quote.bid_price)
    ask = _native_decimal(quote.ask_price)
    if bid is not None and ask is not None:
        return (bid + ask) / Decimal("2")
    if bid is not None:
        return bid
    if ask is not None:
        return ask
    raise RuntimeError("around_spot option subscription requires a priced quote")


def _native_decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(int(getattr(value, "mantissa"))).scaleb(
        -int(getattr(value, "scale"))
    )


def _request_accepts_scope(
    request: SubscriptionRequest, scope_key: str, event: MarketEvent
) -> bool:
    target = request.target
    if target.kind == "market":
        return target.market_id == scope_key
    if target.kind == "consolidated_instrument":
        return (
            f"consolidated:{target.instrument_id}:{target.network_id or '*'}"
            == scope_key
        )
    return str(getattr(event.data, "instrument_id")).startswith("instrument:option:")


def _scope_key(scope: object) -> str:
    market_id = getattr(scope, "market_id", None)
    if market_id is not None:
        return str(market_id)
    return (
        f"consolidated:{getattr(scope, 'instrument_id')}:"
        f"{getattr(scope, 'network_id', None) or '*'}"
    )


def _subscription(value: Any, *, request_id: str) -> Subscription:
    return Subscription(
        str(value.subscription_id),
        request_id,
        value.state,
        None,
    )
