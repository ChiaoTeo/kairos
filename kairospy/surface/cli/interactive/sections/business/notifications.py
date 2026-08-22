"""Interactive notification destination section."""

from __future__ import annotations

import typer

from ...models import GuidedCommand, InteractiveContext


def print_menu(context: InteractiveContext) -> None:
    del context
    typer.echo("通知：\n  1. 校验通知配置\n  2. 测试通知目的地")


def print_help(context: InteractiveContext) -> None:
    del context
    typer.echo("可用命令：validate/test")


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> GuidedCommand | None:
    del context
    if len(parts) != 1:
        return None
    if parts[0] in {"1", "validate"}:
        mode = typer.prompt("mode", default="paper").strip()
        return GuidedCommand(("notifications", "validate", "--mode", mode, "--format", "text"), "校验通知目的地")
    if parts[0] in {"2", "test"}:
        destination_id = typer.prompt("destination id").strip()
        return GuidedCommand(("notifications", "test", destination_id, "--format", "text"), "测试通知目的地", dangerous=True)
    return None


def choose() -> GuidedCommand:
    command = handle(InteractiveContext(None, None, None, shell_path=("notifications",)), ("validate",))
    assert command is not None
    return command
