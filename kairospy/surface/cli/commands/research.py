"""CLI adapters over the Project-scoped Research Application."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import json
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

import click
import typer
from typer.main import get_command

from kairospy.application.research import ResearchApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.research import ResearchSpec
from kairospy.surface.cli.options import OutputFormat, render


research_app = typer.Typer(
    no_args_is_help=True, help="Lock reproducible Research plans and inspect gates."
)
plan_app = typer.Typer(no_args_is_help=True, help="Lock and inspect Research plans.")
gate_app = typer.Typer(no_args_is_help=True, help="Publish and inspect Research gates.")
research_app.add_typer(plan_app, name="plan")
research_app.add_typer(gate_app, name="gate")


def _application(workspace: Path | None) -> ResearchApplication:
    owner = WorkspaceApplication().open(workspace or Path.cwd())
    return ResearchApplication(owner)


def _emit(value: object, output: OutputFormat) -> None:
    typer.echo(render(value, output))


def _object(path: Path, *, description: str) -> Mapping[str, Any]:
    value = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise typer.BadParameter(f"{description} must contain a JSON object")
    return value


def _spec(path: Path) -> ResearchSpec:
    try:
        return ResearchSpec.from_dict(_object(path, description="Research plan"))
    except (TypeError, ValueError) as error:
        raise typer.BadParameter(f"invalid Research plan: {error}") from error


@plan_app.command("lock")
def lock_plan(
    spec: Path = typer.Argument(..., exists=True, dir_okay=False),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Immutably lock a Research plan before Holdout inspection."""

    _emit(_application(workspace).pin_plan(_spec(spec)), output)


@plan_app.command("show")
def show_plan(
    research_plan_hash: str = typer.Argument(...),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(_application(workspace).plan(research_plan_hash), output)


@gate_app.command("publish")
def publish_gate(
    spec: Path = typer.Argument(..., exists=True, dir_okay=False),
    evidence: Path = typer.Argument(..., exists=True, dir_okay=False),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Validate evidence against the locked plan and persist Gate 2."""

    value = _object(evidence, description="Research evidence")
    results = value.get("results")
    limitations = value.get("limitations")
    if not isinstance(results, Mapping):
        raise typer.BadParameter("Research evidence requires an object named results")
    if not isinstance(limitations, (list, tuple)):
        raise typer.BadParameter("Research evidence requires a limitations sequence")
    try:
        report = _application(workspace).publish_gate(
            _spec(spec),
            results={str(name): dict(item) for name, item in results.items()},
            conclusion=str(value.get("conclusion", "")),
            limitations=tuple(str(item) for item in limitations),
        )
    except (TypeError, ValueError) as error:
        raise typer.BadParameter(f"invalid Research evidence: {error}") from error
    _emit(report, output)


@gate_app.command("show")
def show_gate(
    research_plan_hash: str = typer.Argument(...),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(_application(workspace).gate_report(research_plan_hash), output)


def execute_research_argv(argv: Sequence[str], stdout: TextIO) -> int:
    """Run Research without importing Launch or Strategy runtime."""

    command_result: object = None
    try:
        with redirect_stdout(stdout), redirect_stderr(stdout):
            command_result = get_command(research_app).main(
                args=list(argv), prog_name="kairos research", standalone_mode=False
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
    return command_result if isinstance(command_result, int) else 0


__all__ = ["execute_research_argv", "research_app"]
