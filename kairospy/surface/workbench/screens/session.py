"""UI-only session state for Workbench product slices."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from rich.console import RenderableType

from ..widgets import (
    ActionToken,
    ActionItem,
    ChoiceInteraction,
    ConfirmInteraction,
    ControlInteraction,
    InputInteraction,
    InteractionState,
    RunningInteraction,
)
from .live import LiveBuffer
from .operation import OperationSpec
from .results import ResultKind, ResultRoute
from .selection import LaunchRecordView, ResourceRecordView, SelectionRecord

if TYPE_CHECKING:
    from .flows.launch.execution_actions import ExecutionPromptState
    from .flows.launch.market_actions import LaunchMarketPromptState
    from .flows.launch.orders import OrderPromptState
    from .flows.launch.wizard import LaunchWizardState
    from .flows.market.actions import MarketFilePromptState, MarketRouteView
    from .flows.market.workspace import WorkspaceMarketPromptState
    from .flows.operations.actions import ProjectPromptState
    from .flows.operations.business import BusinessPromptState
    from .flows.operations.views import ServiceStatusView
    from .flows.resources.wizard import ResourceWizardState


@dataclass(slots=True)
class MarketSession:
    """Market-owned transient selections, prompts, and live presentation state."""

    purpose: str = "search"
    selected: object | None = None
    observation: str | None = None
    provider: str | None = None
    snapshot: RenderableType | None = None
    refresh_enabled: bool = False
    records: tuple[SelectionRecord, ...] = ()
    routes: tuple[MarketRouteView, ...] = ()
    file_prompt: MarketFilePromptState | None = None
    workspace_prompt: WorkspaceMarketPromptState | None = None

    def reset_control(self) -> None:
        self.provider = None
        self.snapshot = None
        self.refresh_enabled = False

    def reset(self) -> None:
        self.purpose = "search"
        self.selected = None
        self.observation = None
        self.records = ()
        self.routes = ()
        self.file_prompt = None
        self.workspace_prompt = None
        self.reset_control()


@dataclass(slots=True)
class ReferenceSession:
    """Reference-owned search vocabulary and current result kind."""

    kind: str | None = None
    instrument_type: str | None = None
    selected: object | None = None

    def reset(self) -> None:
        self.kind = None
        self.instrument_type = None
        self.selected = None


@dataclass(slots=True)
class OperationsSession:
    """Operations-owned selections and multi-step prompts."""

    selected_service: str | None = None
    selected_service_status: ServiceStatusView | None = None
    service_records: tuple[SelectionRecord, ...] = ()
    live_buffer: LiveBuffer | None = None
    source_tail: tuple[str, ...] = ()
    log_generation: str | None = None
    log_size: int = 0
    log_started_at: float | None = None
    received_lines: int = 0
    warning_lines: int = 0
    project_prompt: ProjectPromptState | None = None
    profile_action: str | None = None
    business_prompt: BusinessPromptState | None = None

    def reset(self) -> None:
        self.selected_service = None
        self.selected_service_status = None
        self.service_records = ()
        self.reset_logs()
        self.project_prompt = None
        self.profile_action = None
        self.business_prompt = None

    def start_logs(self, component: str, *, started_at: float) -> None:
        self.live_buffer = LiveBuffer(f"service/{component}")
        self.source_tail = ()
        self.log_generation = None
        self.log_size = 0
        self.log_started_at = started_at
        self.received_lines = 0
        self.warning_lines = 0

    def reset_logs(self) -> None:
        self.live_buffer = None
        self.source_tail = ()
        self.log_generation = None
        self.log_size = 0
        self.log_started_at = None
        self.received_lines = 0
        self.warning_lines = 0

    def finish_result(self, kind: ResultKind) -> None:
        if kind is ResultKind.BUSINESS:
            self.business_prompt = None
        elif kind is ResultKind.OPERATIONS_PROJECT:
            self.project_prompt = None
        elif kind is ResultKind.OPERATIONS_PROFILE:
            self.profile_action = None


@dataclass(slots=True)
class ResearchSession:
    """Research-owned current workflow input."""

    action: str | None = None
    primary: str | None = None

    def reset(self) -> None:
        self.action = None
        self.primary = None


@dataclass(slots=True)
class ResourcesSession:
    """Runtime resource discovery and configuration presentation state."""

    kind: str | None = None
    selected: ResourceRecordView | None = None
    action: str | None = None
    wizard: ResourceWizardState | None = None

    def reset(self) -> None:
        if self.wizard is not None:
            self.wizard.clear_secrets()
        self.kind = None
        self.selected = None
        self.action = None
        self.wizard = None


@dataclass(slots=True)
class AccountSession:
    """Account runtime and order prompt state."""

    order_prompt: OrderPromptState | None = None

    def reset(self) -> None:
        self.order_prompt = None

    def finish_result(self, kind: ResultKind) -> None:
        if kind is ResultKind.ORDER:
            self.reset()


@dataclass(slots=True)
class StrategySession:
    """Strategy Launch and instance transient presentation state."""

    launch_records: tuple[LaunchRecordView, ...] = ()
    instance_records: tuple[LaunchRecordView, ...] = ()
    component_records: tuple[LaunchRecordView, ...] = ()
    selected_record: LaunchRecordView | None = None
    wizard: LaunchWizardState | None = None
    attach_snapshot: RenderableType | None = None
    live_buffer: LiveBuffer | None = None
    source_tail: tuple[str, ...] = ()

    @property
    def attach_paused(self) -> bool:
        return self.live_buffer is not None and not self.live_buffer.following

    @attach_paused.setter
    def attach_paused(self, paused: bool) -> None:
        if self.live_buffer is None:
            self.live_buffer = LiveBuffer("launch-attach")
        if paused:
            self.live_buffer.pause()
        else:
            self.live_buffer.resume()

    def reset_live_buffer(self, source: str) -> None:
        self.live_buffer = LiveBuffer(source)
        self.source_tail = ()

    def reset(self) -> None:
        self.launch_records = ()
        self.instance_records = ()
        self.component_records = ()
        self.selected_record = None
        self.wizard = None
        self.attach_snapshot = None
        self.live_buffer = None
        self.source_tail = ()


@dataclass(slots=True)
class ExecutionSession:
    """Launch-scoped Execution interaction state."""

    prompt: ExecutionPromptState | None = None

    def reset(self) -> None:
        self.prompt = None


@dataclass(slots=True)
class LaunchMarketSession:
    """Launch-scoped Market interaction state."""

    prompt: LaunchMarketPromptState | None = None

    def reset(self) -> None:
        self.prompt = None


@dataclass(slots=True)
class GuidedSession:
    """Transient presentation state; Applications continue to own business facts."""

    context: tuple[str, ...] = ()
    interaction: InteractionState = ChoiceInteraction()
    suspended_interaction: (
        ChoiceInteraction | InputInteraction | ControlInteraction | None
    ) = None
    visible_records: tuple[SelectionRecord, ...] = ()
    market: MarketSession = field(default_factory=MarketSession)
    reference: ReferenceSession = field(default_factory=ReferenceSession)
    operations: OperationsSession = field(default_factory=OperationsSession)
    research: ResearchSession = field(default_factory=ResearchSession)
    resources: ResourcesSession = field(default_factory=ResourcesSession)
    account: AccountSession = field(default_factory=AccountSession)
    strategy: StrategySession = field(default_factory=StrategySession)
    execution: ExecutionSession = field(default_factory=ExecutionSession)
    launch_market: LaunchMarketSession = field(default_factory=LaunchMarketSession)

    def home(self) -> None:
        self.context = ()
        self.market.reset()
        self.reference.reset()
        self.operations.reset()
        self.research.reset()
        self.resources.reset()
        self.account.reset()
        self.strategy.reset()
        self.execution.reset()
        self.launch_market.reset()
        self.reset_prompt()

    def enter(self, *parts: str) -> None:
        self.context = tuple(parts)
        self.reset_prompt()

    def back(self) -> None:
        self.context = self.context[:-1]
        self.reset_prompt()

    def ask(
        self,
        action: ActionToken,
        *,
        title: str | None = None,
        prompt: str = "",
        detail: str = "",
        value_summary: RenderableType | None = None,
        secret: bool = False,
    ) -> None:
        self.suspended_interaction = None
        self.interaction = InputInteraction(
            action=action,
            title=title or action.action,
            prompt=prompt,
            detail=detail,
            value_summary=value_summary,
            secret=secret,
        )

    def confirm(
        self,
        operation: OperationSpec,
        *,
        title: str = "需要确认",
        display_summary: RenderableType | None = None,
        force_hint: str | None = None,
    ) -> None:
        if isinstance(
            self.interaction, (ChoiceInteraction, InputInteraction, ControlInteraction)
        ):
            self.suspended_interaction = self.interaction
        self.interaction = ConfirmInteraction(
            title=title,
            summary=display_summary or operation.audit_summary,
            operation=operation,
            force_hint=force_hint,
        )

    def reject_input(self, message: str) -> bool:
        """Attach validation feedback to the active input without creating history."""

        if not isinstance(self.interaction, InputInteraction):
            return False
        self.interaction = replace(self.interaction, error=message)
        return True

    def busy(self, route: ResultRoute, *, message: str | None = None) -> None:
        self.suspended_interaction = None
        self.interaction = RunningInteraction(
            route=route,
            title="正在执行",
            message=message or route.kind.value,
        )

    def choose(
        self,
        actions: tuple[ActionItem, ...],
        *,
        title: str = "",
        summary: RenderableType | None = None,
    ) -> None:
        """Present navigation or recovery actions without changing business state."""

        self.suspended_interaction = None
        self.interaction = ChoiceInteraction(
            title=title,
            summary=summary,
            actions=actions,
        )

    def control(
        self,
        title: str,
        snapshot: RenderableType,
        actions: tuple[ActionItem, ...],
        *,
        refreshing: bool,
    ) -> None:
        """Present a live snapshot while keeping keyboard controls available."""

        self.suspended_interaction = None
        self.interaction = ControlInteraction(
            title=title,
            snapshot=snapshot,
            actions=actions,
            refreshing=refreshing,
        )

    def finish_prompt(self) -> None:
        """Return the input to navigation without discarding visible records."""

        if self.suspended_interaction is not None:
            self.interaction = self.suspended_interaction
            self.suspended_interaction = None
        elif not isinstance(self.interaction, (ChoiceInteraction, ControlInteraction)):
            self.interaction = ChoiceInteraction()

    def clear_result_flow(self, result_kind: ResultKind) -> None:
        """Clear feature prompt state after a worker terminal state."""

        self.operations.finish_result(result_kind)
        self.account.finish_result(result_kind)
        if result_kind is ResultKind.EXECUTION:
            self.execution.reset()
        elif result_kind is ResultKind.LAUNCH_MARKET:
            self.launch_market.reset()
        if result_kind is ResultKind.MARKET_FILE:
            self.market.file_prompt = None
        elif result_kind is ResultKind.WORKSPACE_MARKET:
            self.market.workspace_prompt = None

    def reset_prompt(self) -> None:
        self.finish_prompt()
        self.suspended_interaction = None
        self.interaction = ChoiceInteraction()
        self.visible_records = ()


__all__ = [
    "AccountSession",
    "ExecutionSession",
    "GuidedSession",
    "MarketSession",
    "LaunchMarketSession",
    "OperationsSession",
    "ReferenceSession",
    "ResearchSession",
    "ResourcesSession",
    "StrategySession",
]
