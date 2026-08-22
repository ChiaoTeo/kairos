"""Interactive provider Integration section."""

from __future__ import annotations

import typer

from ...models import GuidedCommand, InteractiveContext


def print_menu(context: InteractiveContext) -> None:
    del context
    typer.echo("Provider 集成：\n  1. 查看能力和帮助\n  2. 查看 transfer 帮助\n  3. 查看 earn 帮助")


def print_help(context: InteractiveContext) -> None:
    print_menu(context)


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> GuidedCommand | None:
    del context
    if len(parts) != 1:
        return None
    action = {"1": None, "help": None, "capabilities": None, "2": "transfer", "transfer": "transfer", "3": "earn", "earn": "earn"}.get(parts[0], "missing")
    if action == "missing":
        return None
    argv = ("integration", "--help") if action is None else ("integration", action, "--help")
    return GuidedCommand(argv, "查看 Provider 集成能力", needs_workspace=False)


def choose() -> GuidedCommand:
    command = handle(InteractiveContext(None, None, None, shell_path=("integration",)), ("help",))
    assert command is not None
    return command
