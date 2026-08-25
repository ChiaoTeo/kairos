from __future__ import annotations

import os
import shutil
import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, Sequence, TextIO

import click
import typer
from typer.core import TyperGroup
from typer.main import get_command

from .commands.launch import launch_app
from .commands.project import project_app
from .commands.notifications import notifications_app
from .commands.config import config_app
from .commands.data import data_app
from .commands.research import research_app
from .commands.reference import reference_passthrough
from .commands.account import account_passthrough
from .commands.capital import capital_passthrough
from .commands.integration import integration_passthrough
from .commands.market import market_passthrough
from .commands.order import order_passthrough
from .commands.risk import risk_passthrough
from .commands.system import system_app
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.system.apps.observe.application import SystemObserveApplication
from kairospy.surface.workbench import (
    WorkbenchLaunchRequest,
    WorkbenchWorkspaceError,
    run_workbench,
)
from .options import OutputFormat, render, reset_command_output, set_command_output
from .observe_rendering import observe_payload


_HELP_PANEL_ORDER = {
    "Getting started": 0,
    "Strategy workflow": 1,
    "Research & data": 2,
    "System operations": 3,
    "Business tools": 4,
    "Advanced tools": 5,
}
_HELP_COMMAND_ORDER = {
    name: index
    for index, name in enumerate(
        (
            "quickstart",
            "interactive",
            "project",
            "launch",
            "observe",
            "data",
            "research",
            "system",
            "account",
            "market",
            "order",
            "reference",
            "risk",
            "capital",
            "notifications",
            "integration",
            "config",
            "browse",
            "version",
        )
    )
}


class KairosHelpGroup(TyperGroup):
    """Keep root help compact and ordered by the user's likely workflow."""

    def list_commands(self, ctx: Any) -> list[str]:
        commands = super().list_commands(ctx)
        original_order = {name: index for index, name in enumerate(commands)}

        def sort_key(name: str) -> tuple[int, int, int]:
            panel = getattr(self.commands[name], "rich_help_panel", None)
            panel_order = (
                _HELP_PANEL_ORDER.get(panel, len(_HELP_PANEL_ORDER))
                if isinstance(panel, str)
                else len(_HELP_PANEL_ORDER)
            )
            return (
                panel_order,
                _HELP_COMMAND_ORDER.get(name, len(_HELP_COMMAND_ORDER)),
                original_order[name],
            )

        return sorted(
            commands,
            key=sort_key,
        )

    def format_help(self, ctx: Any, formatter: Any) -> None:
        if self.rich_markup_mode is None:
            return super().format_help(ctx, formatter)

        # Typer otherwise stretches every help panel to the full terminal width,
        # which makes the command index difficult to scan on wide displays.
        from typer import rich_utils

        previous_width = rich_utils.MAX_WIDTH
        rich_utils.MAX_WIDTH = min(shutil.get_terminal_size().columns, 100)
        try:
            return super().format_help(ctx, formatter)
        finally:
            rich_utils.MAX_WIDTH = previous_width


app = typer.Typer(
    cls=KairosHelpGroup,
    no_args_is_help=True,
    help=(
        "[bold cyan]Kairos[/bold cyan] — build, validate, and operate trading "
        "strategies across backtest, paper, and live."
    ),
    epilog=(
        "[dim]New to Kairos? Run [bold]kairos quickstart[/bold]. "
        "Open the unified workbench with [bold]kairos interactive[/bold].[/dim]"
    ),
)
app.add_typer(
    launch_app,
    name="launch",
    help="Run strategies and inspect their status, logs, and reports.",
    rich_help_panel="Strategy workflow",
)
app.add_typer(
    project_app,
    name="project",
    help="Create, scaffold, and diagnose a Kairos project.",
    rich_help_panel="Getting started",
)
app.add_typer(
    data_app,
    name="data",
    help="Plan, acquire, validate, and inspect unified Datasets.",
    rich_help_panel="Research & data",
)
app.add_typer(
    research_app,
    name="research",
    help="Lock reproducible Research plans and inspect trust gates.",
    rich_help_panel="Research & data",
)
app.add_typer(
    config_app,
    name="config",
    help="Inspect advanced workspace configuration.",
    rich_help_panel="Advanced tools",
)
app.command(
    "account",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
    help="Configure account registry, credentials, and standalone account tools.",
    rich_help_panel="Business tools",
)(account_passthrough)
app.command(
    "integration",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
    help="Inspect provider capabilities and run provider operations.",
    rich_help_panel="Business tools",
)(integration_passthrough)
app.command(
    "market",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
    help="Run Market standalone commands such as validate, replay, and download.",
    rich_help_panel="Business tools",
)(market_passthrough)
app.command(
    "order",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
    help="Run account-scoped direct provider order operations.",
    rich_help_panel="Business tools",
)(order_passthrough)
app.command(
    "risk",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
    help="Run Risk standalone schema, doctor, and preview tools.",
    rich_help_panel="Business tools",
)(risk_passthrough)
app.command(
    "capital",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
    help="Run Capital standalone schema and doctor tools.",
    rich_help_panel="Business tools",
)(capital_passthrough)
app.add_typer(
    notifications_app,
    name="notifications",
    help="Configure, bind, validate, and test outbound notification destinations.",
    rich_help_panel="Business tools",
)
app.add_typer(
    system_app,
    name="system",
    help="Diagnose and control workspace runtime components.",
    rich_help_panel="System operations",
)
app.command(
    "reference",
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
    help="Query Reference standalone facts.",
    rich_help_panel="Business tools",
)(reference_passthrough)


def _cli_format(argv: Sequence[str]) -> str:
    """Resolve explicit output first, then the selected workspace manifest."""
    for index, item in enumerate(argv):
        if item in {"--output", "--format"} and index + 1 < len(argv):
            return argv[index + 1]
        for option in ("--output=", "--format="):
            if item.startswith(option):
                return item[len(option) :]
    # Project creation targets a workspace that does not exist yet. Its output
    # must not inherit the format of an unrelated workspace discovered from
    # the caller's current directory.
    if list(argv[:2]) == ["project", "init"]:
        return "text"
    if argv and argv[0] in {"interactive", "i"}:
        return "text"
    workspace: str | None = None
    for index, item in enumerate(argv):
        if item == "--workspace" and index + 1 < len(argv):
            workspace = argv[index + 1]
        elif item.startswith("--workspace="):
            workspace = item.split("=", 1)[1]
    try:
        return WorkspaceApplication().resolve(workspace).cli_format
    except (FileNotFoundError, ValueError):
        return "text"


@app.command("observe", rich_help_panel="Strategy workflow")
def observe(
    workspace: str | None = typer.Option(None, "--workspace"),
    refresh: float = typer.Option(
        2.0, "--refresh", min=0.2, help="Refresh interval in seconds"
    ),
    once: bool = typer.Option(
        False, "--once", help="Print one JSON observation and exit"
    ),
) -> None:
    """在统一工作台中观测项目；--once 输出一次 JSON。"""
    if once:
        import json

        value = WorkspaceApplication().resolve(workspace)
        snapshot = SystemObserveApplication(value).read()
        typer.echo(json.dumps(observe_payload(snapshot), default=str))
        return
    try:
        run_workbench(
            WorkbenchLaunchRequest(
                workspace=Path(workspace) if workspace is not None else None,
                initial_section="observe",
                observe_refresh_seconds=refresh,
                require_workspace=True,
            )
        )
    except WorkbenchWorkspaceError as error:
        raise typer.BadParameter(str(error)) from error


def _interactive_command(
    workspace: str | None,
    dry_run: bool,
    no_exec: bool,
    yes: bool,
    no_alt_screen: bool = False,
    transcript: str | None = None,
) -> None:
    result = run_workbench(
        WorkbenchLaunchRequest(
            workspace=Path(workspace) if workspace is not None else None,
            dry_run=dry_run,
            no_exec=no_exec,
            yes=yes,
            inline=no_alt_screen,
            transcript_path=Path(transcript) if transcript is not None else None,
        )
    )
    if result.transcript_path is not None:
        typer.echo(f"Workbench transcript: {result.transcript_path}")


@app.command("interactive", rich_help_panel="Getting started")
def interactive(
    workspace: str | None = typer.Option(None, "--workspace"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="预览变更和外部动作，不执行。"
    ),
    no_exec: bool = typer.Option(False, "--no-exec", help="不执行变更和外部动作。"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认提示。"),
    no_alt_screen: bool = typer.Option(
        False,
        "--no-alt-screen",
        help="在当前终端内运行并保留退出时的最后画面。",
    ),
    transcript: str | None = typer.Option(
        None,
        "--transcript",
        help="将 Agent 可读的 Workbench JSONL 记录写入指定路径。",
    ),
) -> None:
    """打开统一的 Kairos Textual 工作台。"""
    _interactive_command(
        workspace,
        dry_run,
        no_exec,
        yes,
        no_alt_screen=no_alt_screen,
        transcript=transcript,
    )


@app.command("i", hidden=True)
def interactive_short(
    workspace: str | None = typer.Option(None, "--workspace"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    no_exec: bool = typer.Option(False, "--no-exec"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    no_alt_screen: bool = typer.Option(False, "--no-alt-screen"),
    transcript: str | None = typer.Option(None, "--transcript"),
) -> None:
    """Short alias for ``interactive``."""
    _interactive_command(
        workspace,
        dry_run,
        no_exec,
        yes,
        no_alt_screen=no_alt_screen,
        transcript=transcript,
    )


@app.command("browse", rich_help_panel="Advanced tools")
def browse(workspace: str | None = typer.Option(None, "--workspace")) -> None:
    """List workspace-owned files for low-level inspection."""
    value = WorkspaceApplication().resolve(workspace)
    for path in sorted(value.paths.root.rglob("*")):
        typer.echo(str(path.relative_to(value.paths.root)))


def _quickstart_payload() -> dict[str, object]:
    return {
        "purpose": "Get from an empty directory to a runnable backtest.",
        "first_run": [
            {
                "step": "Create a project with the starter backtest",
                "command": "kairos project init my-project --id my-project --template backtest",
            },
            {
                "step": "Enter the project",
                "command": "cd my-project",
            },
            {
                "step": "Check what is ready and what to do next",
                "command": "kairos project doctor",
            },
            {
                "step": "Run the starter strategy",
                "command": "kairos launch start demo-backtest",
            },
            {
                "step": "Wait for the backtest report",
                "command": "kairos launch wait demo-backtest",
            },
        ],
        "command_map": {
            "project": "Create or inspect a workspace.",
            "launch": "Start, stop, watch, and diagnose strategy runs.",
            "observe": "Open the runtime overview console.",
            "data": "Plan, acquire, and inspect datasets.",
            "research": "Lock research plans and publish trust gates.",
            "account": "Configure account registry and credentials.",
            "market": "Run standalone Market validation and data tools.",
            "system": "Control and diagnose runtime components.",
            "reference": "Query instruments, listings, and markets.",
        },
        "next_help": [
            "kairos project --help",
            "kairos launch --help",
            "kairos launch diagnose --help",
        ],
    }


def _render_quickstart_text(payload: dict[str, object]) -> str:
    lines = [
        "KairosPy quickstart",
        "",
        str(payload["purpose"]),
        "",
        "First run:",
    ]
    for index, item in enumerate(payload["first_run"], start=1):  # type: ignore[index]
        step = item["step"]  # type: ignore[index]
        command = item["command"]  # type: ignore[index]
        lines.append(f"{index}. {step}")
        lines.append(f"   {command}")
    lines.extend(["", "Command map:"])
    command_map = payload["command_map"]  # type: ignore[assignment]
    for name, description in command_map.items():  # type: ignore[union-attr]
        lines.append(f"- {name}: {description}")
    lines.extend(["", "Useful help:"])
    for command in payload["next_help"]:  # type: ignore[index]
        lines.append(f"- {command}")
    return "\n".join(lines)


@app.command("quickstart", rich_help_panel="Getting started")
def quickstart(
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Show the shortest path from a new project to a runnable strategy."""
    payload = _quickstart_payload()
    if output is OutputFormat.TEXT:
        typer.echo(_render_quickstart_text(payload))
        return
    typer.echo(render(payload, output))


@app.command("version", rich_help_panel="Advanced tools")
def version() -> None:
    """Print the installed KairosPy version."""
    typer.echo("kairospy 0.1.0")


def execute_argv(
    argv: Sequence[str], stdout: TextIO, *, prog_name: str = "kairospy"
) -> int:
    command = get_command(app)
    previous_format = os.environ.get("KAIROS_CLI_FORMAT")
    effective_format = OutputFormat(_cli_format(argv))
    render_token = set_command_output(effective_format)
    os.environ["KAIROS_CLI_FORMAT"] = effective_format.value
    command_result: object = None
    try:
        with redirect_stdout(stdout), redirect_stderr(stdout):
            command_result = command.main(
                args=list(argv), prog_name=prog_name, standalone_mode=False
            )
    except click.ClickException as error:
        error.show(file=stdout)
        return error.exit_code
    except click.Abort:
        return 130
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 1
    except Exception as error:
        stdout.write(f"Error: {error}\n")
        return 1
    finally:
        reset_command_output(render_token)
        if previous_format is None:
            os.environ.pop("KAIROS_CLI_FORMAT", None)
        else:
            os.environ["KAIROS_CLI_FORMAT"] = previous_format
    return command_result if isinstance(command_result, int) else 0


def main(argv: Sequence[str] | None = None) -> int:
    invoked_as = Path(sys.argv[0]).name
    prog_name = invoked_as if invoked_as in {"kairos", "kairospy"} else "kairos"
    return execute_argv(
        sys.argv[1:] if argv is None else argv,
        sys.stdout,
        prog_name=prog_name,
    )
