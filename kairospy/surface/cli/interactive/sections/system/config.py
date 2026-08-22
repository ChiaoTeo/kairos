"""Interactive advanced workspace configuration section."""

from __future__ import annotations

import typer

from ...models import GuidedCommand, InteractiveContext


def print_menu(context: InteractiveContext) -> None:
    del context
    typer.echo(
        "\n".join(
            (
                "高级配置：",
                "  1. 查看路径",
                "  2. 查看 manifest",
                "  3. 查看配置",
                "  4. 运行配置诊断",
                "  5. 解释配置",
                "  6. 查看可用操作",
                "  7. 列出 profiles",
                "  8. 创建 profile",
                "  9. 切换 profile",
            )
        )
    )


def print_help(context: InteractiveContext) -> None:
    del context
    typer.echo("可用命令：paths/manifest/show/doctor/explain/operations/profiles/create/use")


def handle(
    context: InteractiveContext, parts: tuple[str, ...]
) -> GuidedCommand | None:
    del context
    if len(parts) != 1:
        return None
    key = parts[0]
    direct = {
        "1": ("paths", "查看 workspace 路径"),
        "paths": ("paths", "查看 workspace 路径"),
        "2": ("manifest", "查看 workspace manifest"),
        "manifest": ("manifest", "查看 workspace manifest"),
        "3": ("show", "查看 workspace 配置"),
        "show": ("show", "查看 workspace 配置"),
        "4": ("doctor", "运行配置诊断"),
        "doctor": ("doctor", "运行配置诊断"),
        "5": ("explain", "解释 workspace 配置"),
        "explain": ("explain", "解释 workspace 配置"),
        "6": ("operations", "查看配置操作"),
        "operations": ("operations", "查看配置操作"),
    }.get(key)
    if direct is not None:
        command, summary = direct
        return GuidedCommand(("config", command, "--format", "text"), summary)
    if key in {"7", "profiles"}:
        return GuidedCommand(("config", "profile", "list", "--format", "text"), "列出配置 profiles")
    if key in {"8", "create"}:
        name = typer.prompt("profile name").strip()
        return GuidedCommand(("config", "profile", "create", name), "创建配置 profile", dangerous=True)
    if key in {"9", "use"}:
        name = typer.prompt("profile name").strip()
        return GuidedCommand(("config", "profile", "use", name), "切换配置 profile", dangerous=True)
    return None


def choose() -> GuidedCommand:
    command = handle(
        InteractiveContext(None, None, None, shell_path=("config",)), ("show",)
    )
    assert command is not None
    return command
