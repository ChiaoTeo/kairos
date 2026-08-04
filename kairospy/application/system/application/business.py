"""Actor/business coordination used by the system application.

The system lifecycle delegates actor composition and business event handling to
this coordinator. Market/account/projector wiring stays behind this boundary.
"""

from __future__ import annotations

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
from kairospy.application.actor.account.application.projectors import AccountActorProjectors
from kairospy.application.actor.market.application import MarketActor, reference_poll_interval
from kairospy.application.actor.market.application.projectors import MarketActorProjectors
from kairospy.application.actor.risk.application import RiskActor
from kairospy.application.actor.risk.application.projectors import RiskActorProjectors
from kairospy.application.actor.monitor.application import MonitorActor
from kairospy.domain.intent import IntentJournal, TradeIntent


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
    runtime: object | None = None

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
        # AccountActor is the owner of account snapshots, order state and
        # intent transitions.  Projectors below only observe the resulting
        # state and publish views.
        if self.actor_supervisor is not None and event.topic in {
            "account.snapshot",
            "execution.update",
            "market.refresh.requested",
            "reference.refresh.requested",
            "account.refresh.requested",
            "execution.refresh.requested",
            "risk.requested",
        }:
            await self.actor_supervisor.dispatch(event)
        if self.topology is not None:
            await self.topology.dispatch(event)
        result = self._process_runtime_event(runtime, event)
        return result

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
        self.account_actor.record_intent(intent, at=at)
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
        self.account_actor.record_intents(intents, at=at)
        for intent in intents:
            if isinstance(intent, TradeIntent):
                self.account_actor.execute_intent(intent, context)
        self.output.on_intents(intents, context, hook)
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
        market_service = build_market_application(market_source)
        account_runtime = getattr(components, "account", None)
        account_service = build_account_application(account_runtime)
        execution_runtime = execution_coordinator(components)
        execution_source = getattr(components, "execution", None)
        account_owner = AccountActor(
            account_runtime,
            message_bus,
            execution_source=execution_source,
            connections=getattr(resources, "connection_scope", None),
            publish_connection_health=lambda health: output.publish_connection_health(health),
        )
        intents = account_owner.intents
        capabilities = compose_account_capabilities(
            AccountActorDependencies(
                intents=intents,
                market_service=market_service,
                account_service=account_service,
                account_snapshot_store=account_runtime,
                account=account_runtime,
                account_catalog=getattr(components, "account_catalog", None),
                account_directory=account_directory(components),
                trading_execution=getattr(components, "execution", None),
                execution_coordinator=execution_runtime,
                fills_source=getattr(components, "execution", None),
                risk=getattr(execution_runtime, "risk", None),
                reference=reference,
            )
        )
        command_router = ActorCommandRouter()
        artifact_projector = MonitorOutput(
            artifact_output,
            timeline_sample_interval=timeline_sample_interval,
        )
        poll_interval = reference_poll_interval(
            normalized_config if isinstance(normalized_config, dict) else None
        )
        market_actor = (
            None
            if message_bus is None or (not callable(getattr(market_source, "events", None)) and reference is None)
            else MarketActor(
                market_source,  # type: ignore[arg-type]
                message_bus,  # type: ignore[arg-type]
                market_service=market_service,
                reference=reference,
                reference_poll_interval_seconds=poll_interval,
                connections=getattr(resources, "connection_scope", None),
                publish_connection_health=lambda health: output.publish_connection_health(health),
                projectors=MarketActorProjectors(market=market_service, reference=reference),
            )
        )
        risk_actor = None if message_bus is None or getattr(capabilities, "risk", None) is None else RiskActor(capabilities.risk, projectors=RiskActorProjectors(capabilities.risk))
        runtime_account_actor = (
            account_owner
            if message_bus is not None and (callable(getattr(account_runtime, "events", None)) or callable(getattr(execution_source, "events", None)))
            else None
        )
        account_owner.account_application = capabilities.account_application
        account_owner.execution_application = capabilities.execution_application
        account_owner.projectors = AccountActorProjectors(
            strategy_id=strategy_id,
            intents=intents,
            account=capabilities.account,
            execution=capabilities.execution,
        )
        monitor_actor = None if message_bus is None else MonitorActor(strategy_id=strategy_id)
        business_actors = tuple(actor for actor in (market_actor, account_owner, risk_actor, monitor_actor) if actor is not None)
        output = MonitorOutputCoordinator(actors=business_actors, monitor_output=artifact_projector)
        actor_supervisor = BusinessActorSupervisor(business_actors, monitor=monitor_actor)
        topology = MessageTopology()
        message_policy = MessageDeliveryPolicy()
        if market_actor is not None:
            actor_supervisor.route("*", market_actor)
            command_router.register(
                "market.subscribe",
                "market.subscribe.batch",
                "market.unsubscribe",
                handler=market_actor,
            )
            actor_supervisor.route("market.refresh.requested", market_actor)
            actor_supervisor.route("reference.refresh.requested", market_actor)
        if runtime_account_actor is not None:
            actor_supervisor.route("*", runtime_account_actor)
            actor_supervisor.route("account.refresh.requested", runtime_account_actor)
            actor_supervisor.route("execution.refresh.requested", runtime_account_actor)
            actor_supervisor.route("account.snapshot", runtime_account_actor)
            actor_supervisor.route("execution.update", runtime_account_actor)
        if risk_actor is not None:
            actor_supervisor.route("*", risk_actor)
        if monitor_actor is not None:
            actor_supervisor.route("*", monitor_actor)
            actor_supervisor.route("risk.requested", risk_actor)
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
