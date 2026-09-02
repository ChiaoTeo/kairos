"""UI-only session state for Workbench product slices."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING
from uuid import uuid4

from rich.console import RenderableType

from ..widgets import (
    ActionToken,
    ActionItem,
    ChoiceInteraction,
    ConfirmInteraction,
    ControlInteraction,
    InputInteraction,
    InteractionHeading,
    InteractionState,
    RunningInteraction,
)
from .live import LiveBuffer
from .navigation.identity import NavigationContext
from .operation import OperationSpec
from .results import ResultKind, ResultRoute
from .selection import LaunchRecordView, ResourceRecordView, SelectionRecord

if TYPE_CHECKING:
    from .flows.launch.execution_actions import ExecutionPromptState
    from .flows.launch.market_actions import LaunchMarketPromptState
    from .flows.launch.orders import OrderPromptState
    from .flows.launch.wizard import LaunchWizardState
    from .flows.launch.actions import LaunchReadinessView
    from .flows.market.actions import MarketFilePromptState, MarketRouteView
    from .flows.market.workspace import WorkspaceMarketPromptState
    from .flows.reference.actions import (
        CatalogSetupGoal,
        CatalogSetupPlanView,
        ReferenceSourceView,
    )
    from .flows.operations.actions import ProjectPromptState
    from .flows.operations.business import BusinessPromptState
    from .flows.operations.views import ServiceStatusView, SupportStatusView
    from .flows.resources.wizard import ResourceWizardState
    from .flows.account.transfers import TransferPromptState


@dataclass(frozen=True, slots=True)
class NavigationFrame:
    """One visited Workbench page without copying owner-owned business facts."""

    context: NavigationContext


@dataclass(slots=True)
class MarketSession:
    """Market-owned transient selections, prompts, and live presentation state."""

    purpose: str = "search"
    query: str | None = None
    catalog_setup_goal: CatalogSetupGoal | None = None
    catalog_setup_plan: CatalogSetupPlanView | None = None
    catalog_setup_reference_recovery: str | None = None
    catalog_setup_reference_issue: str | None = None
    selected: object | None = None
    observation: str | None = None
    provider: str | None = None
    snapshot: RenderableType | None = None
    refresh_enabled: bool = False
    records: tuple[SelectionRecord, ...] = ()
    routes: tuple[MarketRouteView, ...] = ()
    file_prompt: MarketFilePromptState | None = None
    workspace_prompt: WorkspaceMarketPromptState | None = None
    operator_owner_id: str = field(
        default_factory=lambda: f"operator:kairos-i:{uuid4().hex[:12]}"
    )

    def reset_control(self) -> None:
        self.provider = None
        self.snapshot = None
        self.refresh_enabled = False

    def reset(self) -> None:
        self.purpose = "search"
        self.query = None
        self.catalog_setup_goal = None
        self.catalog_setup_plan = None
        self.catalog_setup_reference_recovery = None
        self.catalog_setup_reference_issue = None
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
    query: str | None = None
    instrument_type: str | None = None
    selected: object | None = None
    sources: tuple[ReferenceSourceView, ...] = ()
    selected_source: ReferenceSourceView | None = None

    def reset(self) -> None:
        self.kind = None
        self.query = None
        self.instrument_type = None
        self.selected = None
        self.sources = ()
        self.selected_source = None


@dataclass(slots=True)
class OperationsSession:
    """Operations-owned selections and multi-step prompts."""

    selected_service: str | None = None
    selected_service_status: ServiceStatusView | None = None
    selected_support: str | None = None
    selected_support_status: SupportStatusView | None = None
    service_records: tuple[SelectionRecord, ...] = ()
    inventory_records: tuple[SelectionRecord, ...] = ()
    group_records: tuple[SelectionRecord, ...] = ()
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
        self.selected_support = None
        self.selected_support_status = None
        self.service_records = ()
        self.inventory_records = ()
        self.group_records = ()
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
    parent_wizard: ResourceWizardState | None = None
    return_context: tuple[str, ...] | None = None
    model_chat: LiveBuffer | None = None
    model_chat_turns: int = 0
    model_chat_failures: int = 0

    def start_model_chat(self, model_id: str) -> None:
        self.model_chat = LiveBuffer(f"model/{model_id}")
        self.model_chat_turns = 0
        self.model_chat_failures = 0

    def reset_model_chat(self) -> None:
        self.model_chat = None
        self.model_chat_turns = 0
        self.model_chat_failures = 0

    def reset(self) -> None:
        if self.wizard is not None:
            self.wizard.clear_secrets()
        if self.parent_wizard is not None:
            self.parent_wizard.clear_secrets()
        self.kind = None
        self.selected = None
        self.action = None
        self.wizard = None
        self.parent_wizard = None
        self.return_context = None
        self.reset_model_chat()


@dataclass(slots=True)
class AccountSession:
    """Account runtime and order prompt state."""

    runtime_entry: bool = False
    records: tuple[ResourceRecordView, ...] = ()
    selected: ResourceRecordView | None = None
    selected_segment: str | None = None
    order_prompt: OrderPromptState | None = None
    transfer_prompt: TransferPromptState | None = None

    def reset(self) -> None:
        self.runtime_entry = False
        self.records = ()
        self.selected = None
        self.selected_segment = None
        self.order_prompt = None
        self.transfer_prompt = None

    def finish_result(self, kind: ResultKind) -> None:
        if kind is ResultKind.ORDER:
            self.order_prompt = None


@dataclass(slots=True)
class StrategySession:
    """Strategy Launch and instance transient presentation state."""

    launch_records: tuple[LaunchRecordView, ...] = ()
    instance_records: tuple[LaunchRecordView, ...] = ()
    component_records: tuple[LaunchRecordView, ...] = ()
    selected_record: LaunchRecordView | None = None
    readiness: LaunchReadinessView | None = None
    instance_entered_from_operations: bool = False
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
        self.readiness = None
        self.instance_entered_from_operations = False
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

    root_label: str = "首页"
    context: NavigationContext = ()
    navigation_stack: list[NavigationFrame] = field(default_factory=list)
    navigation_generation: int = 0
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

    def __post_init__(self) -> None:
        if not self.navigation_stack:
            self.navigation_stack.append(NavigationFrame(self.context))
        elif self.navigation_stack[-1].context != self.context:
            self.navigation_stack.append(NavigationFrame(self.context))

    def home(self) -> None:
        self.navigation_generation += 1
        self.context = ()
        self.navigation_stack[:] = [NavigationFrame(())]
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
        target = tuple(parts)
        self.navigation_generation += 1
        if self.navigation_stack[-1].context != self.context:
            self.navigation_stack.append(NavigationFrame(self.context))
        if self.navigation_stack[-1].context != target:
            self.navigation_stack.append(NavigationFrame(target))
        self.context = target
        self.reset_prompt()

    def enter_context(self, target: NavigationContext) -> None:
        """Enter one canonical page identity without repeating its spelling."""

        self.enter(*target)

    def back(self) -> None:
        self.navigation_generation += 1
        if (
            len(self.navigation_stack) > 1
            and self.navigation_stack[-1].context == self.context
        ):
            self.navigation_stack.pop()
            self.context = self.navigation_stack[-1].context
        else:
            self.context = self.context[:-1]
        self.reset_prompt()

    def stack_parent(self) -> NavigationContext | None:
        """Return the actually visited parent when the current frame is tracked."""

        if (
            len(self.navigation_stack) > 1
            and self.navigation_stack[-1].context == self.context
        ):
            return self.navigation_stack[-2].context
        return None

    def pop_frame(self) -> NavigationContext | None:
        """Pop one actually visited page without interpreting business state."""

        parent = self.stack_parent()
        if parent is None:
            return None
        self.navigation_stack.pop()
        return parent

    def replace_context(self, context: NavigationContext) -> None:
        """Replace an untracked compatibility page without growing history."""

        self.context = context
        self.reset_prompt()

    def return_to(self, *parts: str) -> bool:
        """Return to the most recent matching frame and discard its descendants."""

        target = tuple(parts)
        for index in range(len(self.navigation_stack) - 1, -1, -1):
            if self.navigation_stack[index].context != target:
                continue
            del self.navigation_stack[index + 1 :]
            self.context = target
            self.navigation_generation += 1
            self.reset_prompt()
            return True
        return False

    def return_to_context(self, target: NavigationContext) -> bool:
        """Return to a canonical page already present in visit history."""

        return self.return_to(*target)

    def restore(self, *parts: str) -> None:
        """Return to a visited frame or replace an unmigrated compatibility page."""

        if not self.return_to(*parts):
            self.replace_context(tuple(parts))

    def restore_context(self, target: NavigationContext) -> None:
        """Restore a canonical page without rebuilding its string path."""

        self.restore(*target)

    def ask(
        self,
        action: ActionToken,
        *,
        title: str | None = None,
        prompt: str = "",
        detail: str = "",
        value_summary: RenderableType | None = None,
        secret: bool = False,
        heading: InteractionHeading | None = None,
        state: str | None = None,
    ) -> None:
        self.suspended_interaction = None
        self.interaction = InputInteraction(
            action=action,
            title=title or action.action,
            prompt=prompt,
            detail=detail,
            value_summary=value_summary,
            secret=secret,
            heading=heading,
            state=state,
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
        heading: InteractionHeading | None = None,
        state: str | None = None,
    ) -> None:
        """Present navigation or recovery actions without changing business state."""

        self.suspended_interaction = None
        self.interaction = ChoiceInteraction(
            title=title,
            summary=summary,
            actions=actions,
            heading=heading,
            state=state,
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
