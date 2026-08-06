"""Actor/business coordination used by the system application.

The system lifecycle delegates actor composition and business event handling to
this coordinator. Market/account/projector wiring stays behind this boundary.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from dataclasses import dataclass, field
from types import SimpleNamespace

from kairospy.application.actor.monitor.application.output import MonitorOutput, MonitorOutputCoordinator
from kairospy.application.actor.account.application.assembly import (
    AccountActorDependencies,
    account_directory,
    build_account_application,
    compose_account_capabilities,
    execution_coordinator,
)
from kairospy.application.actor.market.application.assembly import build_market_application
from kairospy.application.support.messaging import Message, MessageBus, MessageInbox
from kairospy.application.support.launch.application.runtime import LaunchRuntimeSession
from kairospy.application.support.messaging.topology import MessageTopology
from kairospy.application.support.messaging.delivery import MessageDeliveryPolicy
from kairospy.application.support.runtime.services.orchestration.state import RuntimeCycle
from kairospy.application.support.runtime.application.interaction import RuntimeInstruction
from kairospy.application.support.runtime.application.interaction import SystemCallResult
from kairospy.application.support.runtime.domain.commands import RuntimeCommand
from kairospy.application.actor.support.base import BusinessActorSupervisor
from kairospy.application.actor.support.commands import ActorCommandRouter
from kairospy.application.actor.account.application import AccountActor
from kairospy.application.actor.account.application.commands import (
    ExecuteIntentCommand as AccountExecuteIntentCommand,
    RecordIntentsCommand,
)
from kairospy.application.actor.account.application.projectors import AccountActorProjectors
from kairospy.application.actor.market.application import MarketActor, ReferenceActor, dynamic_subscription_limit, reference_poll_interval
from kairospy.application.actor.market.application.projectors import MarketActorProjectors
from kairospy.application.actor.risk.application import RiskActor
from kairospy.application.actor.risk.application.projectors import RiskActorProjectors
from kairospy.application.actor.monitor.application import MonitorActor
from kairospy.application.actor.notification import NotificationActor
from kairospy.domain.intent import IntentJournal, TradeIntent


_LOGGER = logging.getLogger("kairospy.system")


@dataclass(slots=True)
class SystemBusinessRuntime:
    """Business runtime for one composed System instance.

    This object is the business task coordinator, not a service registry.
    Its public methods are the small set of tasks delegated by the system
    lifecycle:
    attach the business context, bind runtime resources, process one runtime
    event, execute one synchronous system call, and tear the context down.
    Concrete market/account/usecase services remain private to this object.
    """
    output: MonitorOutputCoordinator
    account_actor: AccountActor | None = None
    command_router: ActorCommandRouter = field(default_factory=ActorCommandRouter)
    actor_supervisor: BusinessActorSupervisor | None = None
    message_subscription: MessageInbox | None = None
    message_bus: MessageBus | None = None
    topology: MessageTopology | None = None
    message_policy: MessageDeliveryPolicy | None = None
    notification_actor: NotificationActor | None = None
    runtime: object | None = None
    _strategy_callbacks_logged: set[str] = field(default_factory=set)
    _pending_async_intents: list[object] = field(default_factory=list)
    _processing_async_event: bool = False

    @property
    def intents(self) -> IntentJournal:
        if self.account_actor is None:
            raise RuntimeError("account actor has not been composed")
        return self.account_actor.intents

    def attach(self, *, views: object) -> None:
        self.output.attach(views=views)
        if self.topology is not None:
            self.topology.register("*", self.output.publish_for_event)

    def message_inbox(self) -> MessageInbox | None:
        return self.message_subscription

    def bind_runtime(self, runtime: LaunchRuntimeSession) -> None:
        self.runtime = runtime
        self.output.publish_started(runtime.views)
        if self.notification_actor is not None:
            self.notification_actor.bind_views(runtime.views)

    async def start_actors(self) -> None:
        if self.actor_supervisor is not None:
            await self.actor_supervisor.start()

    async def stop_actors(self) -> None:
        if self.actor_supervisor is not None:
            await self.actor_supervisor.stop()

    @property
    def has_finite_actors(self) -> bool:
        return self.actor_supervisor is not None and self.actor_supervisor.has_finite_actor

    async def wait_for_finite_actors(self) -> None:
        if self.actor_supervisor is not None:
            await self.actor_supervisor.wait_for_finite_completion()

    async def close(self) -> None:
        if self.message_subscription is not None:
            await self.message_subscription.close()
        if self.message_bus is not None:
            await self.message_bus.close()

    def process(self, event: Message) -> tuple[RuntimeCycle, ...]:
        runtime = self.runtime
        if runtime is None:
            raise RuntimeError("business runtime is not bound")
        if self.topology is not None:
            self.topology.dispatch_sync(event)
        return self._process_runtime_event(runtime, event)

    async def process_async(self, event: Message) -> tuple[RuntimeCycle, ...]:
        if self.message_policy is not None:
            delivered, result = await self.message_policy.deliver(event, self._process_async_once)
            return result if delivered and isinstance(result, tuple) else ()
        return await self._process_async_once(event)

    async def _process_async_once(self, event: Message) -> tuple[RuntimeCycle, ...]:
        runtime = self.runtime
        if runtime is None:
            raise RuntimeError("business runtime is not bound")
        self._processing_async_event = True
        self._pending_async_intents.clear()
        # AccountActor is the owner of account snapshots, order state and
        # intent transitions.  Projectors below only observe the resulting
        # state and publish views.
        if self.actor_supervisor is not None and (
            event.domain == "market"
            or event.topic in {
                "account.snapshot",
                "account.market_profile.refresh",
                "execution.update",
                "market.refresh.requested",
                "reference.refresh.requested",
                "account.refresh.requested",
                "execution.refresh.requested",
                "risk.requested",
                "reference.catalog.changed",
            }
        ):
            await self.actor_supervisor.dispatch(event)
        if self.topology is not None:
            await self.topology.dispatch(event)
        if self.notification_actor is not None:
            # The actor only enqueues here; external HTTP delivery runs in its
            # own worker and cannot hold up business event processing.
            await self.notification_actor.handle(event)
        try:
            return await self._process_runtime_event_async(runtime, event)
        finally:
            self._processing_async_event = False
            self._pending_async_intents.clear()

    def on_message(self, event: Message) -> RuntimeInstruction:
        self.command_router.apply_event(event)
        return RuntimeInstruction(strategy_hook=_strategy_hook(event))

    def call(self, command: RuntimeCommand) -> SystemCallResult:
        return self.command_router.call(command)

    def emit_intent(self, intent: object, *, context: object | None = None) -> None:
        at = getattr(context, "now", None)
        if at is None:
            raise RuntimeError("intent emission requires a timestamp")
        if self.account_actor is None:
            raise RuntimeError("account actor has not been composed")
        if self._processing_async_event:
            # Strategy callbacks are synchronous, so the command is collected
            # here and submitted after the callback returns.  The actual
            # mutation still happens through AccountActor.ask().
            self._pending_async_intents.append(intent)
            return
        self.account_actor.apply_command(RecordIntentsCommand((intent,), at))
        event = getattr(context, "event", None)
        hook = _strategy_hook(event) if isinstance(event, Message) else "on_data"
        self.output.on_intents((intent,), context, hook)

    def on_strategy_result(
        self,
        event: Message,
        *,
        hook: str,
        intents: tuple[object, ...],
        context: object,
    ) -> RuntimeInstruction:
        at = getattr(context, "now", None) or event.time
        if self.account_actor is None:
            raise RuntimeError("account actor has not been composed")
        collected = tuple(intents)
        self.account_actor.apply_command(RecordIntentsCommand(collected, at))
        for intent in collected:
            if isinstance(intent, TradeIntent):
                self.account_actor.apply_command(AccountExecuteIntentCommand(intent, context))
        self.output.on_intents(collected, context, hook)
        return RuntimeInstruction()

    async def _on_strategy_result_async(
        self,
        event: Message,
        *,
        hook: str,
        intents: tuple[object, ...],
        context: object,
    ) -> RuntimeInstruction:
        if self.account_actor is None:
            raise RuntimeError("account actor has not been composed")
        at = getattr(context, "now", None) or event.time
        collected = tuple(dict.fromkeys((*self._pending_async_intents, *intents)))
        if collected:
            await self.account_actor.dispatch_command(RecordIntentsCommand(collected, at))
        for intent in collected:
            if isinstance(intent, TradeIntent):
                await self.account_actor.dispatch_command(AccountExecuteIntentCommand(intent, context))
        self.output.on_intents(collected, context, hook)
        return RuntimeInstruction()

    def detach(self) -> None:
        return None

    def _process_runtime_event(self, runtime: LaunchRuntimeSession, event: Message) -> tuple[RuntimeCycle, ...]:
        instruction = self.on_message(event)
        if not instruction.dispatch_strategy:
            if instruction.action == "stop":
                runtime.stop()
            cycles = (runtime.observe(event),)
        else:
            cycle = runtime.process(event, hook=instruction.strategy_hook)
            cycles = (cycle,)
            if cycle.dispatched:
                output = cycle.output
                result_instruction = self.on_strategy_result(
                    event,
                    hook=getattr(output, "hook", cycle.hook),
                    intents=tuple(getattr(output, "intents", ())),
                    context=getattr(output, "context", None),
                )
                if result_instruction.action == "stop":
                    runtime.stop()
        for cycle in cycles:
            if cycle.dispatched and cycle.hook not in self._strategy_callbacks_logged:
                self._strategy_callbacks_logged.add(cycle.hook)
                _LOGGER.info(
                    "system=%s phase=strategy_callback hook=%s event_sequence=%s intents=%d",
                    getattr(runtime, "launch_id", "-"),
                    cycle.hook,
                    event.sequence,
                    len(tuple(getattr(cycle.output, "intents", ()))),
                )
            self.output.publish_cycle(cycle, runtime.views)
        return cycles

    async def _process_runtime_event_async(self, runtime: LaunchRuntimeSession, event: Message) -> tuple[RuntimeCycle, ...]:
        instruction = self.on_message(event)
        if not instruction.dispatch_strategy:
            if instruction.action == "stop":
                runtime.stop()
            cycles = (runtime.observe(event),)
        else:
            cycle = runtime.process(event, hook=instruction.strategy_hook)
            cycles = (cycle,)
            if cycle.dispatched:
                output = cycle.output
                result_instruction = await self._on_strategy_result_async(
                    event,
                    hook=getattr(output, "hook", cycle.hook),
                    intents=tuple(getattr(output, "intents", ())),
                    context=getattr(output, "context", None),
                )
                if result_instruction.action == "stop":
                    runtime.stop()
        for cycle in cycles:
            if cycle.dispatched and cycle.hook not in self._strategy_callbacks_logged:
                self._strategy_callbacks_logged.add(cycle.hook)
                _LOGGER.info(
                    "system=%s phase=strategy_callback hook=%s event_sequence=%s intents=%d",
                    getattr(runtime, "launch_id", "-"),
                    cycle.hook,
                    event.sequence,
                    len(tuple(getattr(cycle.output, "intents", ()))) + len(self._pending_async_intents),
                )
            self.output.publish_cycle(cycle, runtime.views)
        return cycles


class SystemApplication:
    """Compose system actors for one running System instance."""

    def start(
        self,
        *,
        resources: object,
        strategy_id: str,
        artifact_output: object,
        timeline_sample_interval: object,
        reference: object | None = None,
        normalized_config: object | None = None,
        message_bus: object | None = None,
    ) -> SystemBusinessRuntime:
        components = SimpleNamespace(
            market=getattr(resources, "data", None),
            account=getattr(resources, "account", None),
            account_catalog=getattr(resources, "account", None),
            execution=getattr(resources, "trading_execution", None),
        )
        reference = getattr(resources, "reference", reference)
        ensure_ready = getattr(reference, "ensure_ready", None)
        if callable(ensure_ready):
            ensure_ready()
        market_source = getattr(components, "market", None)
        market_service = build_market_application(market_source, store=getattr(resources, "market_store", None))
        account_runtime = getattr(components, "account", None)
        account_service = build_account_application(account_runtime)
        execution_runtime = execution_coordinator(components)
        risk_application = getattr(execution_runtime, "risk", None)
        # In the Actor composition RiskActor is the sole mutable owner of the
        # risk ledger.  Standalone ExecutionApplication composition keeps its
        # synchronous risk option for CLI/tests; this runtime path does not.
        if execution_runtime is not None and hasattr(execution_runtime, "risk"):
            execution_runtime.risk = None
        execution_source = getattr(components, "execution", None)
        health_sequence = 0

        def publish_connection_health(health: object) -> None:
            nonlocal health_sequence
            output.publish_connection_health(health)
            if message_bus is None:
                return
            health_sequence += 1
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(
                message_bus.publish(
                    Message(
                        "connection.health",
                        health,
                        datetime.now(timezone.utc),
                        "system.connection",
                        health_sequence,
                    )
                )
            )
        monitor_actor = None if message_bus is None else MonitorActor(strategy_id=strategy_id, bus=message_bus)
        notification_application = getattr(resources, "notifications", None)
        notification_settings_value = getattr(resources, "notification_settings", None)
        notification_actor = (
            None
            if message_bus is None or notification_application is None
            else NotificationActor(
                notification_application,
                bus=message_bus,
                queue_size=int(getattr(notification_settings_value, "queue_size", 256)),
                summary_interval_seconds=getattr(notification_settings_value, "summary_interval_seconds", None),
            )
        )
        set_market_reference = getattr(execution_source, "set_market_reference", None)
        if callable(set_market_reference):
            set_market_reference(reference)
        account_owner = AccountActor(
            account_runtime,
            message_bus,
            execution_source=execution_source,
            connections=getattr(resources, "connection_scope", None),
            publish_connection_health=publish_connection_health,
        )
        intents = account_owner.intents
        capabilities = compose_account_capabilities(
            AccountActorDependencies(
                intents=intents,
                account_service=account_service,
                account_snapshot_store=account_runtime,
                account=account_runtime,
                account_catalog=getattr(components, "account_catalog", None),
                account_directory=account_directory(components),
                trading_execution=getattr(components, "execution", None),
                execution_coordinator=execution_runtime,
                fills_source=getattr(components, "execution", None),
                risk=None,
                audit_store=getattr(execution_runtime, "audit_store", None),
                instance_id=str(getattr(execution_runtime, "instance_id", "local")),
            )
        )
        command_router = ActorCommandRouter()
        artifact_projector = MonitorOutput(
            artifact_output,
            timeline_sample_interval=timeline_sample_interval,
            monitor_actor=monitor_actor,
        )
        poll_interval = reference_poll_interval(
            normalized_config if isinstance(normalized_config, dict) else None
        )
        reference_actor = (
            None
            if message_bus is None or reference is None
            else ReferenceActor(reference, message_bus, poll_interval_seconds=poll_interval)
        )
        market_actor = (
            None
            if message_bus is None or (
                not callable(getattr(market_source, "events", None))
                and market_service is None
                and reference is None
            )
            else MarketActor(
                market_source,  # type: ignore[arg-type]
                message_bus,  # type: ignore[arg-type]
                market_service=market_service,
                reference=reference,
                connections=getattr(resources, "connection_scope", None),
                publish_connection_health=publish_connection_health,
                projectors=MarketActorProjectors(market=market_service, reference=reference),
                max_dynamic_members=dynamic_subscription_limit(normalized_config if isinstance(normalized_config, Mapping) else None),
            )
        )
        risk_actor = None if message_bus is None or risk_application is None else RiskActor(risk_application, bus=message_bus, projectors=RiskActorProjectors(risk_application))
        account_owner.risk_actor = risk_actor
        runtime_account_actor = (
            account_owner
            if message_bus is not None and (callable(getattr(account_runtime, "events", None)) or callable(getattr(execution_source, "events", None)))
            else None
        )
        account_owner.account_application = capabilities.account_application
        account_owner.execution_application = capabilities.execution_application
        account_owner.account_view = capabilities.account
        account_owner.projectors = AccountActorProjectors(
            strategy_id=strategy_id,
            intents=intents,
            account=account_owner if capabilities.account is not None else None,
            execution=capabilities.execution,
        )
        if monitor_actor is not None:
            monitor_actor.bind_actor_sources(tuple(actor for actor in (reference_actor, market_actor, risk_actor, account_owner, notification_actor) if actor is not None))
        # AccountActor awaits RiskActor for reservation decisions, so the
        # dependency is started before the account event loops.
        business_actors = tuple(actor for actor in (reference_actor, market_actor, risk_actor, account_owner, monitor_actor, notification_actor) if actor is not None)
        output = MonitorOutputCoordinator(actors=business_actors, monitor_output=artifact_projector)
        actor_supervisor = BusinessActorSupervisor(business_actors, monitor=monitor_actor)
        topology = MessageTopology()
        message_policy = MessageDeliveryPolicy()
        if market_actor is not None:
            actor_supervisor.route_domain("market", market_actor)
            command_router.register(
                "market.subscribe",
                "market.subscribe.batch",
                "market.subscribe.dynamic",
                "market.unsubscribe",
                handler=market_actor,
            )
            actor_supervisor.route("market.refresh.requested", market_actor)
            actor_supervisor.route("reference.catalog.changed", market_actor)
        if reference_actor is not None:
            actor_supervisor.route("reference.refresh.requested", reference_actor)
        if runtime_account_actor is not None:
            actor_supervisor.route_domain("market", runtime_account_actor)
            actor_supervisor.route("account.refresh.requested", runtime_account_actor)
            actor_supervisor.route("execution.refresh.requested", runtime_account_actor)
            actor_supervisor.route("account.snapshot", runtime_account_actor)
            actor_supervisor.route("execution.update", runtime_account_actor)
        if risk_actor is not None:
            actor_supervisor.route("risk.requested", risk_actor)
        if monitor_actor is not None:
            actor_supervisor.route("*", monitor_actor)
        message_subscription = None if message_bus is None else message_bus.open_inbox(maxsize=1024)  # type: ignore[union-attr]
        return SystemBusinessRuntime(
            output=output,
            account_actor=account_owner,
            command_router=command_router,
            actor_supervisor=actor_supervisor,
            message_subscription=message_subscription,
            message_bus=message_bus,  # type: ignore[arg-type]
            topology=topology,
            message_policy=message_policy,
            notification_actor=notification_actor,
        )


def _strategy_hook(event: Message) -> str | None:
    if event.domain in {"market", "data"}:
        return "on_data"
    if event.domain == "clock":
        return "on_clock"
    if event.domain in {"system", "reference"}:
        return "on_system"
    if event.domain == "intent":
        return "on_intent"
    return None


__all__ = [
    "SystemApplication",
    "SystemBusinessRuntime",
]
