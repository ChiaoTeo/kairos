"""Built-in strategy for trusted, instance-local Python interaction."""

from __future__ import annotations

import ast
import asyncio
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from collections.abc import Mapping
from typing import Any

from kairospy.strategy import CommandResult, Strategy, StrategyCommand, StrategyContext


class InteractiveStrategy(Strategy):
    """Execute user-provided Python source through the strategy command hook.

    This is intentionally a trusted-workspace feature.  Source is evaluated in
    the strategy process so it can see the live Strategy and Context objects;
    the enclosing launch must protect the instance Unix socket accordingly.
    """

    strategy_id = "builtin-interactive"

    def __init__(self) -> None:
        self._namespace: dict[str, object] | None = None

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
        namespace = self._namespace
        if namespace is None:
            namespace = {
                "__name__": "__kairos_interactive__",
                "strategy": self,
                "context": ctx,
                "account": ctx.account,
                "execution": ctx.execution,
                "market": ctx.market,
            }
            self._namespace = namespace
        value, stdout, stderr = await _execute_source(command.source, namespace)
        return CommandResult(
            command.request_id,
            "completed",
            result={"value": _display_value(value)},
            stdout=stdout,
            stderr=stderr,
        )


async def _execute_source(
    source: str, namespace: dict[str, object]
) -> tuple[object, str, str]:
    if not source.strip():
        raise ValueError("interactive Python source must not be empty")
    tree = ast.parse(source, filename="<kairos-interactive>", mode="exec")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        tree.body[-1] = ast.Assign(
            targets=[ast.Name(id="_kairos_result", ctx=ast.Store())],
            value=tree.body[-1].value,
        )
    ast.fix_missing_locations(tree)
    code = compile(
        tree,
        "<kairos-interactive>",
        "exec",
        flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
    )
    namespace.pop("_kairos_result", None)
    # ``eval`` is required for a code object compiled with
    # PyCF_ALLOW_TOP_LEVEL_AWAIT; ``exec`` discards the coroutine it creates.
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = eval(code, namespace, namespace)
        if asyncio.iscoroutine(result):
            await result
    return namespace.pop("_kairos_result", None), stdout.getvalue(), stderr.getvalue()


def _display_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _display_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_display_value(item) for item in value]
    return repr(value)


__all__ = ["InteractiveStrategy"]
