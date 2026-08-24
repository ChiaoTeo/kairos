"""CLI adapters over the same Project-scoped Data Application as Python."""

from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
import json
from pathlib import Path
from typing import Mapping, Sequence, TextIO

import click
import typer
from typer.main import get_command

from kairospy.research.apps.data.application import DataApplication, DataRequirement
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.surface.cli.options import OutputFormat, render


data_app = typer.Typer(no_args_is_help=True, help="Plan, acquire and inspect Datasets.")
set_app = typer.Typer(no_args_is_help=True, help="Inspect named Dataset Set aliases.")
gate_app = typer.Typer(no_args_is_help=True, help="Inspect persisted data trust gates.")
data_app.add_typer(set_app, name="set")
data_app.add_typer(gate_app, name="gate")


def _application(workspace: Path | None) -> DataApplication:
    owner = WorkspaceApplication().open(workspace or Path.cwd())
    return DataApplication(owner)


def _emit(value: object, output: OutputFormat) -> None:
    typer.echo(render(value, output))


def _requirements(path: Path) -> tuple[DataRequirement, ...]:
    value = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    rows = value.get("requirements") if isinstance(value, Mapping) else value
    if not isinstance(rows, list) or not rows:
        raise typer.BadParameter(
            "requirements file must contain a non-empty JSON array or object"
        )
    try:
        return tuple(DataRequirement(**dict(row)) for row in rows)
    except (TypeError, ValueError) as error:
        raise typer.BadParameter(f"invalid data requirement: {error}") from error


@data_app.command("list")
def list_datasets(
    owner: str | None = typer.Option(None, "--owner"),
    kind: str | None = typer.Option(None, "--kind"),
    subject: str | None = typer.Option(None, "--subject"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    refs = _application(workspace).list(owner=owner, kind=kind, subject=subject)
    _emit({"datasets": [ref.as_dict() for ref in refs]}, output)


@data_app.command("inspect")
def inspect_dataset(
    dataset_id: str = typer.Argument(...),
    version: str | None = typer.Option(None, "--version"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    description = _application(workspace).describe(dataset_id, version=version)
    _emit(
        {
            "ref": description.ref.as_dict(),
            "lineage": dict(description.lineage),
            "quality_report": dict(description.quality_report),
            "partitions": [
                {
                    "key": item.key,
                    "event_count": item.event_count,
                    "content_hash": item.content_hash,
                    "format": item.format,
                }
                for item in description.partitions
            ],
        },
        output,
    )


@data_app.command("plan")
def plan_data(
    requirements: Path = typer.Argument(..., exists=True, dir_okay=False),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    plan = asyncio.run(_application(workspace).plan(_requirements(requirements)))
    _emit(plan.as_dict(), output)


@data_app.command("execute")
def execute_data(
    requirements: Path = typer.Argument(..., exists=True, dir_okay=False),
    expected_plan_hash: str | None = typer.Option(None, "--expected-plan-hash"),
    max_concurrency: int = typer.Option(1, "--max-concurrency", min=1),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    application = _application(workspace)
    plan = asyncio.run(application.plan(_requirements(requirements)))
    if expected_plan_hash is not None and plan.plan_hash != expected_plan_hash:
        raise typer.BadParameter(
            "reviewed data plan changed: "
            f"expected={expected_plan_hash}, actual={plan.plan_hash}"
        )
    result = asyncio.run(application.execute(plan, max_concurrency=max_concurrency))
    _emit(
        {
            "plan_hash": plan.plan_hash,
            "dataset_set": result.as_dict(),
            "execution": application.execution(plan.plan_hash),
        },
        output,
    )


@data_app.command("execution")
def show_execution(
    plan_hash: str = typer.Argument(...),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(_application(workspace).execution(plan_hash), output)


@set_app.command("list")
def list_sets(
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit({"aliases": dict(_application(workspace).set_aliases())}, output)


@set_app.command("show")
def show_set(
    name: str = typer.Argument(...),
    composition_hash: str | None = typer.Option(None, "--composition-hash"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    value = _application(workspace).load_set(name, composition_hash=composition_hash)
    _emit(value.as_dict(), output)


@gate_app.command("show")
def show_gate(
    composition_hash: str = typer.Argument(...),
    workspace: Path | None = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(_application(workspace).trust_report(composition_hash), output)


def execute_data_argv(argv: Sequence[str], stdout: TextIO) -> int:
    """Run only the Data capability without importing Launch/Strategy runtime."""

    command_result: object = None
    try:
        with redirect_stdout(stdout), redirect_stderr(stdout):
            command_result = get_command(data_app).main(
                args=list(argv), prog_name="kairos data", standalone_mode=False
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


__all__ = ["data_app", "execute_data_argv"]
