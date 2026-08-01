from __future__ import annotations

from pathlib import Path
from typing import Mapping

import typer

from kairospy.application.support.system.facade.config import ConfigFacade
from kairospy.surface.cli.options import OutputFormat
from kairospy.surface.cli.output import write_cli_result


config_app = typer.Typer(no_args_is_help=True, help="Workspace configuration commands")
profile_app = typer.Typer(no_args_is_help=True, help="Local CLI profile commands")
config_app.add_typer(profile_app, name="profile")
_CONFIG = ConfigFacade()


@config_app.command("paths")
def paths(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _CONFIG.paths()
    write_cli_result(ctx, payload, output_format=output_format, text=_render_paths)


@config_app.command("show")
def show(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    _show_manifest(ctx, output_format)


@config_app.command("manifest")
def manifest(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    _show_manifest(ctx, output_format)


def _show_manifest(ctx: typer.Context, output_format: OutputFormat | None) -> None:
    payload = _CONFIG.manifest()
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@config_app.command("doctor")
def doctor(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _CONFIG.doctor()
    issues = payload["issues"]
    write_cli_result(ctx, payload, output_format=output_format, text=_render_doctor)
    if issues:
        raise typer.Exit(2)


@config_app.command("explain")
def explain(
    ctx: typer.Context,
    launch: str | None = typer.Option(None, "--launch", help="Registered launch name"),
    config_path: Path | None = typer.Option(None, "--config", help="Launch config path"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    if launch is None and config_path is None:
        raise typer.BadParameter("config explain requires --launch or --config")
    if launch is not None and config_path is not None:
        raise typer.BadParameter("use either --launch or --config, not both")
    try:
        payload = _CONFIG.explain(launch=launch, config_path=config_path)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_explain)


@config_app.command("operations")
def operations(
    ctx: typer.Context,
    limit: int | None = typer.Option(50, "--limit"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _CONFIG.operations(limit=limit)
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_operations)


@profile_app.command("list")
def profile_list(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _CONFIG.list_profiles()
    write_cli_result(ctx, payload, output_format=output_format, text=_render_profiles)


@profile_app.command("use")
def profile_use(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _CONFIG.use_profile(name)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, text=_render_profile_use)


@profile_app.command("create")
def profile_create(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    source: Path | None = typer.Option(None, "--from", help="Template TOML to copy"),
    force: bool = typer.Option(False, "--force"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _CONFIG.create_profile(name=name, source=source, force=force)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, text=_render_profile_create)


def _render_paths(result: object) -> str:
    payload = _payload(result)
    return "\n".join([
        "Workspace Paths",
        f"  root             {payload['root']}",
        f"  manifest         {payload['manifest_path']}",
        f"  workspace_root   {payload['workspace_root']}",
        f"  state_root       {payload['state_root']}",
        f"  launch_root         {payload['launch_root']}",
        f"  data_root        {payload['data_root']}",
        f"  reference_root   {payload['reference_root']}",
        f"  accounts_root    {payload['accounts_root']}",
        f"  launch_index        {payload['launch_index_path']}",
    ])


def _render_doctor(result: object) -> str:
    payload = _payload(result)
    lines = ["Config Doctor", f"  valid    {str(payload['valid']).lower()}"]
    lines.extend(f"  issue    {issue}" for issue in payload["issues"] if isinstance(issue, str))
    accounts = payload["accounts"]
    launches = payload["launches"]
    if isinstance(accounts, Mapping):
        lines.append(f"  accounts {accounts['count']} from {accounts['root']}")
    if isinstance(launches, Mapping):
        lines.append(f"  launches     {launches['count']} from {launches['path']}")
    return "\n".join(lines)


def _render_explain(result: object) -> str:
    payload = _payload(result)
    sources = payload["sources"]
    if not isinstance(sources, Mapping):
        raise TypeError("config explain renderer expected sources mapping")
    account_ref = payload.get("account_ref")
    return "\n".join([
        "Effective Config",
        f"  target             {payload['target']}",
        f"  launch_config         {sources['launch_config']}",
        f"  workspace_manifest {sources['workspace_manifest']}",
        f"  account.ref        {account_ref or ''}",
        f"  account.source     {sources['account'] or ''}",
    ])


def _render_operations(result: object) -> str:
    payload = _payload(result)
    rows = payload["operations"]
    if not rows:
        return f"Operations\n  none\n  path {payload['path']}"
    if not isinstance(rows, list):
        raise TypeError("config operations renderer expected row list")
    lines = ["Operations"]
    for row in rows:
        if isinstance(row, Mapping):
            lines.append(f"  {row.get('event_time')}  {row.get('action')}  {row.get('target')}")
    return "\n".join(lines)


def _render_profiles(result: object) -> str:
    payload = _payload(result)
    profiles = payload["profiles"]
    if not profiles:
        return f"Profiles\n  none\n  root {payload['root']}"
    if not isinstance(profiles, list):
        raise TypeError("profile list renderer expected profile list")
    lines = ["Profiles"]
    for item in profiles:
        if isinstance(item, Mapping):
            marker = "*" if item["selected"] else " "
            lines.append(f" {marker} {item['name']}  {item['path']}")
    return "\n".join(lines)


def _render_profile_use(result: object) -> str:
    payload = _payload(result)
    return "\n".join([
        "Profile Selected",
        f"  name       {payload['profile']}",
        f"  path       {payload['path']}",
        f"  selection  {payload['selection']}",
    ])


def _render_profile_create(result: object) -> str:
    payload = _payload(result)
    return "\n".join([
        "Profile Created",
        f"  name    {payload['profile']}",
        f"  path    {payload['path']}",
        f"  source  {payload['source'] or ''}",
    ])


def _payload(result: object) -> Mapping[str, object]:
    if not isinstance(result, Mapping):
        raise TypeError("config renderer expected mapping payload")
    return result


__all__ = ["config_app"]
