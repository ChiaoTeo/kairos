"""Interactive Dataset workflow section."""

from __future__ import annotations

import typer

from ...models import GuidedCommand, InteractiveContext


def print_menu(context: InteractiveContext) -> None:
    del context
    typer.echo(
        "\n".join(
            (
                "数据：",
                "  1. 列出 datasets",
                "  2. 查看 dataset",
                "  3. 审阅 data requirements",
                "  4. 执行 data requirements",
                "  5. 查看 data execution",
                "  6. 列出 dataset set aliases",
                "  7. 查看 dataset set",
                "  8. 查看 data gate",
            )
        )
    )


def print_help(context: InteractiveContext) -> None:
    del context
    typer.echo("可用命令：list/inspect/plan/execute/execution/sets/set/gate")


def handle(
    context: InteractiveContext, parts: tuple[str, ...]
) -> GuidedCommand | None:
    del context
    if len(parts) != 1:
        return None
    key = parts[0]
    if key in {"1", "list"}:
        return GuidedCommand(("data", "list"), "列出项目 datasets")
    if key in {"2", "inspect"}:
        dataset_id = typer.prompt("dataset id").strip()
        return GuidedCommand(("data", "inspect", dataset_id), "查看 dataset")
    if key in {"3", "plan"}:
        path = typer.prompt("requirements.json 路径", default="requirements.json").strip()
        return GuidedCommand(("data", "plan", path), "审阅数据需求，不下载")
    if key in {"4", "execute"}:
        path = typer.prompt("requirements.json 路径", default="requirements.json").strip()
        plan_hash = typer.prompt("expected plan hash", default="").strip()
        argv = ("data", "execute", path)
        if plan_hash:
            argv = (*argv, "--expected-plan-hash", plan_hash)
        return GuidedCommand(argv, "执行已经审阅的数据计划", dangerous=True)
    if key in {"5", "execution"}:
        execution_id = typer.prompt("execution id").strip()
        return GuidedCommand(("data", "execution", execution_id), "查看数据执行记录")
    if key in {"6", "sets"}:
        return GuidedCommand(("data", "set", "list"), "列出 Dataset Set aliases")
    if key in {"7", "set"}:
        alias = typer.prompt("dataset set alias").strip()
        return GuidedCommand(("data", "set", "show", alias), "查看 Dataset Set")
    if key in {"8", "gate"}:
        gate_id = typer.prompt("data gate id").strip()
        return GuidedCommand(("data", "gate", "show", gate_id), "查看 data gate")
    return None


def choose() -> GuidedCommand:
    choice = typer.prompt("Data 动作（list/plan/execute/sets）", default="list").strip()
    aliases = {"list": "1", "plan": "3", "execute": "4", "sets": "6"}
    command = handle(
        InteractiveContext(None, None, None, shell_path=("data",)),
        (aliases.get(choice) or choice,),
    )
    if command is None:
        raise typer.BadParameter("未知 Data 动作")
    return command
