"""Built-in strategy for trusted, instance-local Python interaction."""

from __future__ import annotations

from kairospy.strategy import CommandResult, Strategy, StrategyCommand, StrategyContext
from kairospy.strategy.apps.runtime.services.python_control import StrategyPythonControl


class InteractiveStrategy(Strategy):
    """Execute user-provided Python source through the strategy command hook.

    This is intentionally a trusted-workspace feature.  Source is evaluated in
    the strategy process so it can see the live Strategy and Context objects;
    the enclosing launch must protect the instance Unix socket accordingly.
    """

    strategy_id = "builtin-interactive"

    def __init__(self) -> None:
        self._control: StrategyPythonControl | None = None

    async def on_command(
        self, ctx: StrategyContext, command: StrategyCommand
    ) -> CommandResult:
        if command.kind != "interactive.python":
            return CommandResult(
                command.request_id,
                "rejected",
                error=f"unsupported strategy command: {command.kind}",
                error_code="unsupported_command",
            )
        if self._control is None:
            self._control = StrategyPythonControl(self, ctx)
        return await self._control.command(command)


__all__ = ["InteractiveStrategy"]
