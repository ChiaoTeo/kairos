"""Trusted, instance-local Python control owned by Strategy Runtime."""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

from kairospy.strategy.api.commands import StrategyCommand
from kairospy.strategy.api.results import CommandResult


class StrategyPythonControl:
    """Execute trusted workspace Python without involving strategy state/hooks."""

    def __init__(self, strategy: object, context: object) -> None:
        namespace: dict[str, object] = {
            "__name__": "__kairos_runtime_control__",
            "strategy": strategy,
            "context": context,
        }
        for name in (
            "reference",
            "market",
            "account",
            "portfolio",
            "capital",
            "risk",
            "execution",
            "clock",
        ):
            capability = getattr(context, name, None)
            if capability is not None:
                namespace[name] = capability
        self._namespace = namespace

    async def command(self, command: StrategyCommand) -> CommandResult:
        if command.kind != "interactive.python":
            return CommandResult(
                command.request_id,
                "rejected",
                error=f"unsupported runtime command: {command.kind}",
                error_code="unsupported_command",
            )
        value, stdout, stderr = await execute_python(command.source, self._namespace)
        return CommandResult(
            command.request_id,
            "completed",
            result={"value": display_value(value)},
            stdout=stdout,
            stderr=stderr,
        )


async def execute_python(
    source: str, namespace: dict[str, object]
) -> tuple[object, str, str]:
    if not source.strip():
        raise ValueError("interactive Python source must not be empty")
    tree = ast.parse(source, filename="<kairos-runtime-control>", mode="exec")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        tree.body[-1] = ast.Assign(
            targets=[ast.Name(id="_kairos_result", ctx=ast.Store())],
            value=tree.body[-1].value,
        )
    ast.fix_missing_locations(tree)
    code = compile(
        tree,
        "<kairos-runtime-control>",
        "exec",
        flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
    )
    namespace.pop("_kairos_result", None)
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = eval(code, namespace, namespace)
        if asyncio.iscoroutine(result):
            await result
    return namespace.pop("_kairos_result", None), stdout.getvalue(), stderr.getvalue()


def display_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): display_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [display_value(item) for item in value]
    return repr(value)


__all__ = ["StrategyPythonControl", "display_value", "execute_python"]
