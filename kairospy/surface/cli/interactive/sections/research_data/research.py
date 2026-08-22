"""Interactive Research plan and trust-gate section."""

from __future__ import annotations

import typer

from ...models import GuidedCommand, InteractiveContext


def print_menu(context: InteractiveContext) -> None:
    del context
    typer.echo(
        "\n".join(
            (
                "研究：",
                "  1. 锁定 research plan",
                "  2. 查看 research plan",
                "  3. 发布 research gate",
                "  4. 查看 research gate",
            )
        )
    )


def print_help(context: InteractiveContext) -> None:
    del context
    typer.echo("可用命令：lock/show/publish/gate")


def handle(
    context: InteractiveContext, parts: tuple[str, ...]
) -> GuidedCommand | None:
    del context
    if len(parts) != 1:
        return None
    key = parts[0]
    if key in {"1", "lock"}:
        path = typer.prompt("research-plan.json 路径", default="research-plan.json").strip()
        return GuidedCommand(("research", "plan", "lock", path), "锁定研究计划")
    if key in {"2", "show"}:
        plan_id = typer.prompt("research plan id").strip()
        return GuidedCommand(("research", "plan", "show", plan_id), "查看研究计划")
    if key in {"3", "publish"}:
        plan = typer.prompt("research-plan.json 路径", default="research-plan.json").strip()
        evidence = typer.prompt(
            "research-evidence.json 路径", default="research-evidence.json"
        ).strip()
        return GuidedCommand(
            ("research", "gate", "publish", plan, evidence),
            "发布研究 gate 证据",
            dangerous=True,
        )
    if key in {"4", "gate"}:
        gate_id = typer.prompt("research gate id").strip()
        return GuidedCommand(("research", "gate", "show", gate_id), "查看研究 gate")
    return None


def choose() -> GuidedCommand:
    choice = typer.prompt("Research 动作（lock/show/publish/gate）", default="lock").strip()
    aliases = {"lock": "1", "show": "2", "publish": "3", "gate": "4"}
    command = handle(
        InteractiveContext(None, None, None, shell_path=("research",)),
        (aliases.get(choice) or choice,),
    )
    if command is None:
        raise typer.BadParameter("未知 Research 动作")
    return command
