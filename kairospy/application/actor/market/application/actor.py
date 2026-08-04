"""Market Actor application facade."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from kairospy.application.actor.market.application.reference import ReferenceActor, reference_poll_interval
from kairospy.application.actor.support.base import BusinessActor
from kairospy.application.support.messaging import Message, MessageBus
from kairospy.application.support.runtime.application.interaction import SystemCallDecision, SystemCallResult
from kairospy.application.support.runtime.domain.commands import CommandHandle, RuntimeCommand, RuntimeCommandStatus
from kairospy.application.usecases.market.application.data import DataSubscription, DataSubscriptionGroup, MarketDataSubscriptionGroupSpec, MarketDataSubscriptionSpec
from kairospy.application.usecases.market.domain.datasets import parse_market_dataset_id
from kairospy.application.usecases.strategy.protocol import StrategySubscriptionGroupRequest, StrategySubscriptionRequest
from kairospy.domain.reference import MarketResolver
from kairospy.application.usecases.market.application.component import MarketApplication


class MarketActor(BusinessActor):
    """Own market data and reference-facing market resources."""

    def __init__(self, source: object | None, bus: MessageBus, *, market_service: MarketApplication | None = None, reference: object | None = None, reference_poll_interval_seconds: float = 300.0, connections: object | None = None, publish_connection_health: object | None = None, projectors: object | None = None) -> None:
        super().__init__("market", bus=bus)
        self.runtime = source
        self.is_finite = bool(getattr(source, "is_finite", False))
        self.market_service = market_service
        self.projectors = projectors
        self._connections = connections
        self._publish_connection_health = publish_connection_health if callable(publish_connection_health) else None
        self._connection_roles = ("market_stream", "market_data", "market_feed")
        self.reference_actor = None if reference is None else ReferenceActor(reference, bus, poll_interval_seconds=reference_poll_interval_seconds)
        self.policy: Callable[[RuntimeCommand], RuntimeCommandStatus | None] | None = None
        self._handles: dict[str, CommandHandle] = {}
        self._results: dict[str, SystemCallResult] = {}

    def call(self, command: RuntimeCommand) -> SystemCallResult:
        """Handle a market command as part of the market Actor boundary."""
        existing = self._handles.get(command.command_id)
        if existing is not None:
            return self._results[command.command_id]
        handle = CommandHandle(command.command_id, command.kind)
        self._handles[command.command_id] = handle
        try:
            decision = None if self.policy is None else self.policy(command)
            if decision is RuntimeCommandStatus.DEFERRED:
                handle._defer()
            elif decision is RuntimeCommandStatus.IGNORED:
                handle._ignore()
            elif decision is RuntimeCommandStatus.REJECTED:
                handle._reject("command rejected by system policy")
            elif command.kind == "market.subscribe":
                self._subscribe(handle, command.payload)
            elif command.kind == "market.subscribe.batch":
                self._subscribe_batch(handle, command.payload)
            elif command.kind == "market.unsubscribe":
                self._unsubscribe(handle, command.payload)
            else:
                handle._reject(f"unsupported runtime command: {command.kind}")
        except (TypeError, ValueError, KeyError, RuntimeError) as error:
            handle._reject(str(error))
        result = _call_result(handle)
        self._results[command.command_id] = result
        return result

    def apply_event(self, event: Message) -> None:
        if event.command_id is None:
            return
        handle = self._handles.get(event.command_id)
        if handle is None or handle.done:
            return
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        kind = event.kind.lower()
        if kind.endswith((".rejected", ".failed")):
            handle._reject(str(payload.get("error") or "system rejected command"), at=event.time)
        elif kind.endswith(".cancelled"):
            handle._cancel(at=event.time)
        elif kind.endswith((".completed", ".activated")):
            handle._complete(payload, at=event.time)

    def _subscribe(self, handle: CommandHandle, payload: object | None) -> None:
        if self.market_service is None:
            handle._reject("system has no market service")
            return
        try:
            subscription = self.market_service.subscriptions.subscribe(_market_subscription_spec(payload))
        except (TypeError, ValueError, KeyError) as error:
            handle._reject(str(error))
            return
        if not isinstance(subscription, DataSubscription):
            handle._reject("system market service returned an invalid subscription")
            return
        handle._accept(result={"subscription_id": subscription.key})

    def _unsubscribe(self, handle: CommandHandle, payload: object | None) -> None:
        if self.market_service is None:
            handle._reject("system has no market service")
            return
        subscription = payload if isinstance(payload, (DataSubscription, str)) else None
        if subscription is None:
            handle._reject("market.unsubscribe requires a subscription or id")
            return
        self.market_service.subscriptions.unsubscribe(subscription)
        handle._accept()

    def _subscribe_batch(self, handle: CommandHandle, payload: object | None) -> None:
        if self.market_service is None:
            handle._reject("system has no market service")
            return
        try:
            group = self.market_service.subscriptions.subscribe_many(_market_subscription_group(payload))
        except (TypeError, ValueError, KeyError) as error:
            handle._reject(str(error))
            return
        if not isinstance(group, DataSubscriptionGroup):
            handle._reject("system market service returned an invalid subscription group")
            return
        handle._accept(result={"subscription_ids": tuple(item.key for item in group.subscriptions)})

    def _start_connections(self) -> None:
        manager = self._connections
        if manager is None:
            return
        start_roles = getattr(manager, "start_roles", None)
        if callable(start_roles):
            start_roles(self._connection_roles)
        health = getattr(manager, "health", None)
        if callable(health) and self._publish_connection_health is not None:
            self._publish_connection_health(health())

    def _stop_connections(self) -> None:
        manager = self._connections
        if manager is None:
            return
        stop_roles = getattr(manager, "stop_roles", None)
        if callable(stop_roles):
            stop_roles(self._connection_roles)

    async def on_start(self) -> None:
        self._start_connections()
        if self.reference_actor is not None:
            await self.reference_actor.start()
        if self.market_service is not None:
            setter = getattr(self.runtime, "set_market_service", None)
            if callable(setter):
                setter(self.market_service)
        events = getattr(self.runtime, "events", None)
        if callable(events):
            self.start_event_loop(events(), is_finite=self.is_finite, name="market")

    async def on_stop(self) -> None:
        if self.reference_actor is not None:
            await self.reference_actor.stop()
        if self.market_service is not None:
            clearer = getattr(self.runtime, "clear_market_service", None)
            if callable(clearer):
                clearer()
        self._stop_connections()

    async def process(self, message: object) -> None:
        projector_event = getattr(self.projectors, "on_event", None)
        if callable(projector_event):
            projector_event(message)
        if self.reference_actor is not None:
            await self.reference_actor.process(message)  # type: ignore[arg-type]

def _market_subscription_spec(payload: object) -> MarketDataSubscriptionSpec:
    if isinstance(payload, MarketDataSubscriptionSpec):
        return payload
    if not isinstance(payload, StrategySubscriptionRequest):
        raise TypeError("market.subscribe requires a strategy subscription request")
    if isinstance(payload.subject, str) and payload.subject.startswith("market.") and not payload.selectors:
        dataset = parse_market_dataset_id(payload.subject)
        return MarketDataSubscriptionSpec(dataset.market_ref, (dataset.selector,), identity=payload.identity, params=payload.params, dataset_id=dataset.dataset_id)
    if not payload.selectors:
        raise ValueError("data subscription selectors are required")
    market = MarketResolver(default_venue=payload.exchange, default_market=payload.market_type).resolve(payload.subject, venue=payload.exchange, market=payload.market_type)
    return MarketDataSubscriptionSpec(market, payload.selectors, identity=payload.identity, params=payload.params)


def _market_subscription_group(payload: object) -> MarketDataSubscriptionGroupSpec:
    if isinstance(payload, MarketDataSubscriptionGroupSpec):
        return payload
    if not isinstance(payload, StrategySubscriptionGroupRequest):
        raise TypeError("market.subscribe.batch requires a strategy subscription group")
    return MarketDataSubscriptionGroupSpec(tuple(_market_subscription_spec(request) for request in payload.requests))


def _call_result(handle: CommandHandle) -> SystemCallResult:
    decision = {
        RuntimeCommandStatus.DEFERRED: SystemCallDecision.DEFERRED,
        RuntimeCommandStatus.IGNORED: SystemCallDecision.IGNORED,
        RuntimeCommandStatus.REJECTED: SystemCallDecision.REJECTED,
    }.get(handle.status, SystemCallDecision.ACCEPTED)
    return SystemCallResult(request_id=handle.command_id, decision=decision, handle=handle, result=handle.result, error=handle.error)


__all__ = ["MarketActor", "ReferenceActor", "reference_poll_interval"]
