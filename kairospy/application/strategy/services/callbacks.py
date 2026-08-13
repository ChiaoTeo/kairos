from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable

from kairospy.strategy import CommandResult, StrategyCommand, StrategyLogger

from ..protocol import Strategy
from .context import StrategyContext


COMMAND_TIMEOUT_SECONDS = 30.0


class StrategyCallbackHost:
    """Invoke user code against an already-routed typed Strategy event."""

    def __init__(
        self,
        strategy: Strategy,
        context: StrategyContext,
        logger: StrategyLogger,
    ) -> None:
        self.strategy = strategy
        self.context = context
        self.logger = logger

    def lifecycle(self, name: str) -> None:
        self._invoke(name, self.context._bind(None))

    def dispatch(
        self,
        hook: str,
        domain: str,
        event: object,
        *,
        on_bound: Callable[[], None] | None = None,
    ) -> None:
        metadata = getattr(event, "metadata")
        event_time_source = (
            "none"
            if metadata.occurred_at is None
            else "market_event"
            if domain == "market"
            else f"{domain}_event"
        )
        self.context._bind(event)
        with self.logger.bind_event(
            event_time=metadata.occurred_at,
            event_time_source=event_time_source,
            event_sequence=metadata.sequence,
        ):
            if on_bound is not None:
                on_bound()
            self._invoke(hook, self.context, event)

    async def command(self, command: StrategyCommand) -> CommandResult:
        callback = getattr(self.strategy, "on_command", None)
        if callback is None:
            return CommandResult(
                command.request_id,
                "rejected",
                error=f"unsupported strategy command: {command.kind}",
                error_code="unsupported_command",
            )
        result = callback(self.context._bind(None), command)
        if inspect.isawaitable(result):
            result = await asyncio.wait_for(result, timeout=COMMAND_TIMEOUT_SECONDS)
        if result is None:
            return CommandResult(command.request_id, "completed")
        if not isinstance(result, CommandResult):
            raise TypeError("strategy on_command must return CommandResult")
        return result

    def _invoke(self, name: str, *args: object) -> None:
        callback = getattr(self.strategy, name, None)
        if callback is None:
            return
        result = callback(*args)
        if result is not None:
            raise TypeError(
                f"{name} must return None; use context bus to interact with the system"
            )
