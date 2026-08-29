"""Install versioned starters while leaving generated files user-owned."""

from __future__ import annotations

from pathlib import Path

import typer

from kairospy.surface.cli.options import OutputFormat, render
from kairospy.system.apps.workspace.application import WorkspaceApplication


template_app = typer.Typer(no_args_is_help=True, help="Discover and install templates")


def _emit(value: object, output: OutputFormat) -> None:
    typer.echo(render(value, output))


@template_app.command("list", help="List built-in templates and their versions.")
def template_list(
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(WorkspaceApplication().list_templates(), output)


@template_app.command("show", help="Show parameters and requirements.")
def template_show(
    template_id: str = typer.Argument(...),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    try:
        value = WorkspaceApplication().show_template(template_id)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="template_id") from error
    _emit(value, output)


@template_app.command("install", help="Generate user-owned project resources.")
def template_install(
    template_id: str = typer.Argument(...),
    name: str | None = typer.Option(None, "--name", help="Installation id."),
    parameter: list[str] | None = typer.Option(
        None,
        "--param",
        help="Template value as NAME=VALUE; repeat for multiple values.",
    ),
    destination: str | None = typer.Option(
        None,
        "--destination",
        help="Project-relative directory for a generated strategy package.",
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    try:
        values = _parameters(parameter or [])
        definition = WorkspaceApplication().show_template(template_id)
        raw_parameters = definition.get("parameters")
        parameter_items = raw_parameters if isinstance(raw_parameters, list) else []
        parameter_names = {
            str(item.get("name")) for item in parameter_items if isinstance(item, dict)
        }
        if destination is not None:
            if "strategy_package" not in parameter_names:
                raise ValueError("this template does not generate a strategy package")
            if "strategy_package" in values:
                raise ValueError(
                    "use either --destination or --param strategy_package=..., not both"
                )
            values["strategy_package"] = _destination_package(destination)
        defaults = {
            str(item["name"]): str(item["default"])
            for item in parameter_items
            if isinstance(item, dict) and item.get("default") is not None
        }
        resolved_name = (
            name
            or values.get("launch_id")
            or defaults.get("launch_id")
            or str(definition["template_id"])
        )
        created = WorkspaceApplication().install_template(
            owner,
            template=template_id,
            installation_id=name,
            parameters=values,
        )
        status = WorkspaceApplication().template_status(
            owner,
            installation_id=resolved_name,
        )[0]
    except (FileExistsError, KeyError, OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    _emit(
        {
            "status": "installed",
            "installation": status,
            "created": [str(path) for path in created],
            "ownership": "generated files belong to the user and are never overwritten",
        },
        output,
    )


@template_app.command("status", help="Report customized or missing generated files.")
def template_status(
    installation_id: str | None = typer.Argument(None),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    try:
        value = WorkspaceApplication().template_status(
            owner, installation_id=installation_id
        )
    except (KeyError, OSError, ValueError) as error:
        raise typer.BadParameter(str(error), param_hint="installation_id") from error
    _emit(value, output)


def _parameters(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        name, separator, selected = value.partition("=")
        name = name.strip()
        if not separator or not name or not selected.strip():
            raise ValueError("template parameters must use NAME=VALUE")
        if name in result:
            raise ValueError(f"template parameter was specified twice: {name}")
        result[name] = selected.strip()
    return result


def _destination_package(value: str) -> str:
    if "\\" in value:
        raise ValueError("template destination must use forward slashes")
    path = Path(value.strip())
    if not value.strip() or not path.parts or path.is_absolute() or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ValueError("template destination must be a project-relative directory")
    if any(not part.isidentifier() for part in path.parts):
        raise ValueError(
            "every template destination component must be a Python identifier"
        )
    return ".".join(path.parts)


__all__ = ["template_app"]
