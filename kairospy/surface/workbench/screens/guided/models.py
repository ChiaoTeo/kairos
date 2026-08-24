"""UI-only state for the guided command screen."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class PromptMode(Enum):
    """What the one visible input is collecting right now."""

    NAVIGATION = "navigation"
    ARGUMENT = "argument"
    SECRET = "secret"
    CONFIRMATION = "confirmation"
    BUSY = "busy"


@dataclass(frozen=True, slots=True)
class IdlePrompt:
    """The shared input is ready for navigation."""

    mode: PromptMode = PromptMode.NAVIGATION


@dataclass(frozen=True, slots=True)
class ArgumentPrompt:
    """The shared input is collecting one ordinary or secret value."""

    action: str
    secret: bool = False

    @property
    def mode(self) -> PromptMode:
        return PromptMode.SECRET if self.secret else PromptMode.ARGUMENT


@dataclass(frozen=True, slots=True)
class ConfirmationPrompt:
    """A dangerous action is waiting for an explicit confirmation."""

    summary: str
    operation: Callable[[], Any]
    result_kind: str
    mode: PromptMode = PromptMode.CONFIRMATION


@dataclass(frozen=True, slots=True)
class BusyPrompt:
    """A worker owns the shared input until it reaches a terminal state."""

    result_kind: str
    mode: PromptMode = PromptMode.BUSY


PromptState = IdlePrompt | ArgumentPrompt | ConfirmationPrompt | BusyPrompt


@dataclass(slots=True)
class GuidedSession:
    """Transient presentation state; Applications continue to own business facts."""

    context: tuple[str, ...] = ()
    prompt: PromptState = IdlePrompt()
    visible_records: tuple[Any, ...] = ()
    reference_kind: str | None = None
    reference_instrument_type: str | None = None
    market_purpose: str = "search"
    market_observation: str | None = None
    market_records: tuple[Any, ...] = ()
    market_routes: tuple[dict[str, Any], ...] = ()
    market_file_prompt: Any | None = None
    workspace_market_prompt: Any | None = None
    selected_service: str | None = None
    project_prompt: Any | None = None
    profile_action: str | None = None
    resource_kind: str | None = None
    selected_resource: dict[str, Any] | None = None
    resource_action: str | None = None
    resource_launch_id: str | None = None
    resource_wizard: Any | None = None
    research_action: str | None = None
    research_primary: str | None = None
    launch_records: tuple[dict[str, Any], ...] = ()
    launch_instance_records: tuple[dict[str, Any], ...] = ()
    launch_component_records: tuple[dict[str, Any], ...] = ()
    selected_launch_record: dict[str, Any] | None = None
    launch_wizard: Any | None = None
    launch_attach_paused: bool = False
    launch_attach_seen: tuple[str, ...] = ()
    business_prompt: Any | None = None
    order_prompt: Any | None = None
    execution_prompt: Any | None = None
    launch_market_prompt: Any | None = None

    def home(self) -> None:
        self.context = ()
        self.research_action = None
        self.research_primary = None
        self.reset_prompt()

    def enter(self, *parts: str) -> None:
        self.context = tuple(parts)
        self.reset_prompt()

    def back(self) -> None:
        self.context = self.context[:-1]
        self.reset_prompt()

    @property
    def prompt_mode(self) -> PromptMode:
        return self.prompt.mode

    @property
    def pending_action(self) -> str | None:
        if isinstance(self.prompt, ArgumentPrompt):
            return self.prompt.action
        if isinstance(self.prompt, ConfirmationPrompt):
            return self.prompt.summary
        if isinstance(self.prompt, BusyPrompt):
            return self.prompt.result_kind
        return None

    @property
    def argument_prompt(self) -> ArgumentPrompt | None:
        return self.prompt if isinstance(self.prompt, ArgumentPrompt) else None

    @property
    def confirmation_prompt(self) -> ConfirmationPrompt | None:
        return self.prompt if isinstance(self.prompt, ConfirmationPrompt) else None

    def ask(self, action: str, *, secret: bool = False) -> None:
        self.prompt = ArgumentPrompt(action, secret=secret)

    def confirm(
        self,
        summary: str,
        operation: Callable[[], Any],
        result_kind: str,
    ) -> None:
        self.prompt = ConfirmationPrompt(summary, operation, result_kind)

    def busy(self, result_kind: str) -> None:
        self.prompt = BusyPrompt(result_kind)

    def finish_prompt(self) -> None:
        """Return the input to navigation without discarding visible records."""

        self.prompt = IdlePrompt()

    def clear_result_flow(self, result_kind: str) -> None:
        """Clear feature prompt state after a worker terminal state."""

        if result_kind == "business-result":
            self.business_prompt = None
        elif result_kind == "order-result":
            self.order_prompt = None
        elif result_kind == "execution-result":
            self.execution_prompt = None
        elif result_kind == "launch-market-result":
            self.launch_market_prompt = None
        elif result_kind == "market-file-result":
            self.market_file_prompt = None
        elif result_kind == "operations-project-result":
            self.project_prompt = None
        elif result_kind == "operations-profile-result":
            self.profile_action = None
        elif result_kind == "workspace-market-result":
            self.workspace_market_prompt = None

    def reset_prompt(self) -> None:
        self.finish_prompt()
        self.visible_records = ()


__all__ = [
    "ArgumentPrompt",
    "BusyPrompt",
    "ConfirmationPrompt",
    "GuidedSession",
    "IdlePrompt",
    "PromptMode",
    "PromptState",
]
