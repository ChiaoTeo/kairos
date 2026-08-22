"""Interactive Capital planning section."""

from __future__ import annotations

import typer

from ...models import GuidedCommand, InteractiveContext


def print_menu(context: InteractiveContext) -> None:
    del context
    typer.echo("Capital：\n  1. Schema\n  2. Doctor\n  3. Preview\n  4. Plan\n  5. Health\n  6. Current")


def print_help(context: InteractiveContext) -> None:
    del context
    typer.echo("可用命令：schema/doctor/preview/plan/health/current")


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> GuidedCommand | None:
    del context
    if len(parts) != 1:
        return None
    key = parts[0]
    standalone = {"1": "schema", "schema": "schema", "2": "doctor", "doctor": "doctor", "3": "preview", "preview": "preview", "4": "plan", "plan": "plan"}.get(key)
    if standalone is not None:
        argv = ("capital", standalone)
        if standalone == "preview":
            kind = typer.prompt("Capital request kind", default="funding-objective").strip()
            path = typer.prompt("Capital request 文件", default="capital-request.json").strip()
            argv = (*argv, "--kind", kind, "--file", path)
        elif standalone == "plan":
            objective = typer.prompt(
                "funding objective 文件", default="funding-objective.json"
            ).strip()
            demand = typer.prompt("capital demand 文件", default="capital-demand.json").strip()
            availability = typer.prompt(
                "availability 文件", default="availability.json"
            ).strip()
            argv = (
                *argv,
                "--objective-file", objective,
                "--demand-file", demand,
                "--availability-file", availability,
            )
        return GuidedCommand(argv, f"运行 Capital {standalone}")
    connected = {"5": "health", "health": "health", "6": "current", "current": "current"}.get(key)
    if connected is None:
        return None
    return GuidedCommand(("system", "component", "capital", connected, "--format", "table"), f"查看 Capital {connected}")


def choose() -> GuidedCommand:
    command = handle(InteractiveContext(None, None, None, shell_path=("capital",)), ("doctor",))
    assert command is not None
    return command
