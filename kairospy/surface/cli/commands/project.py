from __future__ import annotations

from typing import Mapping

import typer

from kairospy.application.system.facade.project import ProjectFacade
from kairospy.surface.cli.options import OutputFormat
from kairospy.surface.cli.output import write_cli_result


project_app = typer.Typer(no_args_is_help=True, help="Project workspace commands")
_PROJECTS = ProjectFacade()


@project_app.command("init")
def init(
    project_name: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force"),
) -> None:
    try:
        typer.echo(_PROJECTS.init(project_name, force=force))
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@project_app.command("status")
def status(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _PROJECTS.status()
    write_cli_result(ctx, payload, output_format=output_format, text=_render_status)


@project_app.command("doctor")
def doctor(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _PROJECTS.doctor()
    issues = payload["issues"]
    write_cli_result(ctx, payload, output_format=output_format, text=_render_doctor)
    if issues:
        raise typer.Exit(2)


def _render_status(result: object) -> str:
    payload = _payload(result)
    return "\n".join([
            "Project Status",
            f"  root             {payload['root']}",
            f"  manifest         {payload['manifest'] or '<defaults>'}",
            f"  timezone         {payload['timezone']}",
            f"  language         {payload['language']}",
            f"  workspace        {payload['workspace_root']}",
            f"  accounts         {payload['accounts']}",
            f"  launches             {payload['launches']}",
            f"  market_datasets  {payload['market_datasets']}",
            f"  reference        {payload['reference_root']}",
    ])


def _render_doctor(result: object) -> str:
    payload = _payload(result)
    lines = ["Project Doctor", f"  valid  {str(payload['valid']).lower()}"]
    lines.extend(f"  issue  {issue}" for issue in payload["issues"])
    return "\n".join(lines)


def _payload(result: object) -> Mapping[str, object]:
    if not isinstance(result, Mapping):
        raise TypeError("project renderer expected mapping payload")
    return result


__all__ = ["project_app"]
