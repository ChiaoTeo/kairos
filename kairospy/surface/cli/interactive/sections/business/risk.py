"""Interactive Risk budgets and reservations section."""

from __future__ import annotations

import typer

from ...models import GuidedCommand, InteractiveContext


def print_menu(context: InteractiveContext) -> None:
    del context
    typer.echo("Risk：\n  1. Schema\n  2. Doctor\n  3. Preview\n  4. Health\n  5. Limits\n  6. Reservations")


def print_help(context: InteractiveContext) -> None:
    del context
    typer.echo("可用命令：schema/doctor/preview/health/limits/reservations")


def handle(context: InteractiveContext, parts: tuple[str, ...]) -> GuidedCommand | None:
    del context
    if len(parts) != 1:
        return None
    key = parts[0]
    standalone = {"1": "schema", "schema": "schema", "2": "doctor", "doctor": "doctor", "3": "preview", "preview": "preview"}.get(key)
    if standalone is not None:
        argv = ("risk", standalone)
        if standalone == "preview":
            policy = typer.prompt("Risk policy 文件", default="risk-policy.json").strip()
            request = typer.prompt("Risk request 文件", default="risk-request.json").strip()
            argv = (*argv, "--policy-file", policy, "--request-file", request)
        return GuidedCommand(argv, f"运行 Risk {standalone}")
    connected = {"4": "health", "health": "health", "5": "limits", "limits": "limits", "6": "reservations", "reservations": "reservations"}.get(key)
    if connected is None:
        return None
    return GuidedCommand(("system", "component", "risk", connected, "--format", "table"), f"查看 Risk {connected}")


def choose() -> GuidedCommand:
    command = handle(InteractiveContext(None, None, None, shell_path=("risk",)), ("doctor",))
    assert command is not None
    return command
