"""Single product-routing boundary used by the Workbench Textual adapter."""

from __future__ import annotations

from typing import Any

from ...widgets import ActionToken, Feature
from ..effects import ScreenEffect
from ..session import GuidedSession
from ..operation import OperationSpec
from ..results import ResultKind
from .market import runtime as market
from .operations import runtime as operations
from .reference import runtime as reference
from .research import runtime as research
from .launch import execution, market as launch_market, runtime as launch
from .resources import account, configuration as resources_flow


def handle_input(
    state: Any, session: GuidedSession, token: ActionToken, value: str
) -> tuple[ScreenEffect, ...] | None:
    """Route one typed continuation to its product owner."""

    if token.feature is Feature.MARKET:
        return market.handle_input(state, session, token, value)
    if token.feature is Feature.REFERENCE:
        return reference.handle_input(state, session, token, value)
    if token.feature is Feature.OPERATIONS:
        return operations.handle_input(state, session, token, value)
    if token.feature is Feature.RESEARCH:
        return research.handle_input(state, session, token, value)
    if token.feature is Feature.RESOURCES:
        effects = account.handle_input(state, session, token, value)
        if effects is not None:
            return effects
        return resources_flow.handle_input(state, session, token, value)
    if token.feature is Feature.STRATEGY:
        effects = execution.handle_input(state, session, token, value)
        if effects is not None:
            return effects
        effects = launch_market.handle_input(state, session, token, value)
        if effects is not None:
            return effects
        return launch.handle_input(state, session, token, value)
    return None


def handle_command(
    state: Any,
    session: GuidedSession,
    command: str,
    arguments: tuple[str, ...],
) -> tuple[ScreenEffect, ...] | None:
    """Offer one command to product owners in the stable command order."""

    effects = market.handle_command(state, session, command, arguments)
    if effects is not None:
        return effects
    effects = reference.handle_command(state, session, command, arguments)
    if effects is not None:
        return effects
    effects = operations.handle_command(state, session, command, arguments)
    if effects is not None:
        return effects
    effects = research.handle_command(state, session, command, arguments)
    if effects is not None:
        return effects
    effects = account.handle_command(state, session, command, arguments)
    if effects is not None:
        return effects
    effects = resources_flow.handle_command(state, session, command, arguments)
    if effects is not None:
        return effects
    effects = execution.handle_command(state, session, command, arguments)
    if effects is not None:
        return effects
    effects = launch_market.handle_command(state, session, command, arguments)
    if effects is not None:
        return effects
    return launch.handle_command(state, session, command, arguments)


def handle_context(
    state: Any, session: GuidedSession, command: str
) -> tuple[ScreenEffect, ...] | None:
    """Route a context-relative selection to the current product owner."""

    section = session.context[:1]
    if section == ("market",):
        return market.handle_context(state, session, command)
    if section == ("reference",):
        return reference.handle_context(state, session, command)
    if section == ("operations",):
        return operations.handle_context(state, session, command)
    if section == ("research",):
        return research.handle_context(state, session, command)
    if section == ("strategy",):
        effects = execution.handle_context(state, session, command)
        if effects is not None:
            return effects
        effects = launch_market.handle_context(state, session, command)
        if effects is not None:
            return effects
        return launch.handle_context(state, session, command)
    if section == ("resources",):
        effects = account.handle_context(state, session, command)
        if effects is not None:
            return effects
        return resources_flow.handle_context(state, session, command)
    return None


def handle_success(
    state: Any, session: GuidedSession, spec: OperationSpec, result: Any
) -> tuple[ScreenEffect, ...] | None:
    """Route a successful operation to the flow which owns its result."""

    effects = market.handle_success(state, session, spec, result)
    if effects is not None:
        return effects
    effects = reference.handle_success(state, session, spec, result)
    if effects is not None:
        return effects
    effects = operations.handle_success(state, session, spec, result)
    if effects is not None:
        return effects
    effects = research.handle_success(state, session, spec, result)
    if effects is not None:
        return effects
    effects = account.handle_success(state, session, spec, result)
    if effects is not None:
        return effects
    effects = resources_flow.handle_success(state, session, spec, result)
    if effects is not None:
        return effects
    effects = execution.handle_success(state, session, spec, result)
    if effects is not None:
        return effects
    effects = launch_market.handle_success(state, session, spec, result)
    if effects is not None:
        return effects
    return launch.handle_success(state, session, spec, result)


def handle_failure(
    state: Any, session: GuidedSession, spec: OperationSpec, error: str
) -> tuple[ScreenEffect, ...] | None:
    """Route a failed operation to the flow which owns its recovery."""

    effects = market.handle_failure(state, session, spec, error)
    if effects is not None:
        return effects
    effects = reference.handle_failure(state, session, spec, error)
    if effects is not None:
        return effects
    effects = operations.handle_failure(state, session, spec, error)
    if effects is not None:
        return effects
    effects = research.handle_failure(state, session, spec, error)
    if effects is not None:
        return effects
    effects = account.handle_failure(state, session, spec, error)
    if effects is not None:
        return effects
    effects = resources_flow.handle_failure(state, session, spec, error)
    if effects is not None:
        return effects
    effects = execution.handle_failure(state, session, spec, error)
    if effects is not None:
        return effects
    effects = launch_market.handle_failure(state, session, spec, error)
    if effects is not None:
        return effects
    return launch.handle_failure(state, session, spec, error)


def handle_cancel(
    state: Any, session: GuidedSession, spec: OperationSpec
) -> tuple[ScreenEffect, ...] | None:
    """Route cancellation recovery to the flow which owns the operation."""

    effects = market.handle_cancel(state, session, spec)
    if effects is not None:
        return effects
    effects = reference.handle_cancel(state, session, spec)
    if effects is not None:
        return effects
    effects = operations.handle_cancel(state, session, spec)
    if effects is not None:
        return effects
    effects = research.handle_cancel(state, session, spec)
    if effects is not None:
        return effects
    effects = account.handle_cancel(state, session, spec)
    if effects is not None:
        return effects
    effects = resources_flow.handle_cancel(state, session, spec)
    if effects is not None:
        return effects
    effects = execution.handle_cancel(state, session, spec)
    if effects is not None:
        return effects
    effects = launch_market.handle_cancel(state, session, spec)
    if effects is not None:
        return effects
    return launch.handle_cancel(state, session, spec)


def cancel_input(session: GuidedSession, token: ActionToken) -> bool:
    """Discard a typed prompt through the product which owns its staged state."""

    if token.feature is Feature.MARKET:
        return market.cancel_input(session, token)
    if token.feature is Feature.REFERENCE:
        return reference.cancel_input(session, token)
    if token.feature is Feature.OPERATIONS:
        return operations.cancel_input(session, token)
    if token.feature is Feature.RESEARCH:
        return research.cancel_input(session, token)
    if token.feature is Feature.RESOURCES:
        if account.cancel_input(session, token):
            return True
        return resources_flow.cancel_input(session, token)
    if token.feature is Feature.STRATEGY:
        if execution.cancel_input(session, token):
            return True
        if launch_market.cancel_input(session, token):
            return True
        return launch.cancel_input(session, token)
    return False


def cancel_confirmation(session: GuidedSession, spec: OperationSpec) -> None:
    """Discard confirmation-only state without making Screen infer its owner."""

    if spec.route.kind is ResultKind.RESOURCE_WIZARD:
        resources_flow.cancel_input(
            session, ActionToken(Feature.RESOURCES, "resource:setup")
        )
    elif spec.route.kind is ResultKind.STRATEGY_WIZARD:
        launch.cancel_input(session, ActionToken(Feature.STRATEGY, "strategy:launch"))
    else:
        session.clear_result_flow(spec.route.kind)


__all__ = [
    "cancel_confirmation",
    "cancel_input",
    "handle_cancel",
    "handle_command",
    "handle_context",
    "handle_failure",
    "handle_input",
    "handle_success",
]
