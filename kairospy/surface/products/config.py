from __future__ import annotations

from enum import StrEnum
import json
from pathlib import Path
from typing import Mapping

import typer

from kairospy.application.system.workspace import KairosWorkspace
from kairospy.config import load_run_config


config_app = typer.Typer(no_args_is_help=True, help="Workspace configuration commands")
profile_app = typer.Typer(no_args_is_help=True, help="Local CLI profile commands")
config_app.add_typer(profile_app, name="profile")


class OutputFormat(StrEnum):
    json = "json"
    text = "text"


@config_app.command("paths")
def paths(output_format: OutputFormat = typer.Option(OutputFormat.text, "--format")) -> None:
    workspace = KairosWorkspace.resolve()
    payload = workspace.to_dict()
    if output_format is OutputFormat.json:
        _echo(payload)
        return
    typer.echo(
        "\n".join(
            [
                "Workspace Paths",
                f"  root             {payload['root']}",
                f"  manifest         {payload['manifest_path']}",
                f"  workspace_root   {payload['workspace_root']}",
                f"  state_root       {payload['state_root']}",
                f"  run_root         {payload['run_root']}",
                f"  data_root        {payload['data_root']}",
                f"  reference_root   {payload['reference_root']}",
                f"  accounts_root    {payload['accounts_root']}",
                f"  run_index        {payload['run_index_path']}",
            ]
        )
    )


@config_app.command("show")
def show(output_format: OutputFormat = typer.Option(OutputFormat.json, "--format")) -> None:
    _show_manifest(output_format)


@config_app.command("manifest")
def manifest(output_format: OutputFormat = typer.Option(OutputFormat.json, "--format")) -> None:
    _show_manifest(output_format)


def _show_manifest(output_format: OutputFormat) -> None:
    workspace = KairosWorkspace.resolve()
    payload = {
        "workspace": workspace.to_dict(),
        "manifest": {
            "path": str(workspace.manifest.source_path) if workspace.manifest.source_path is not None else None,
            "values": dict(workspace.manifest.values),
        },
    }
    if output_format is OutputFormat.json:
        _echo(payload)
        return
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


@config_app.command("doctor")
def doctor(output_format: OutputFormat = typer.Option(OutputFormat.text, "--format")) -> None:
    workspace = KairosWorkspace.resolve()
    issues: list[str] = []
    if workspace.manifest_path is None:
        issues.append(".kairos/kairos.toml was not found; using built-in defaults")
    for account in workspace.accounts.list():
        if account.environment == "live" and not account.credential_values and not account.credential:
            issues.append(f"live account {account.account_id} has no credential metadata")
    payload = {
        "valid": not issues,
        "issues": issues,
        "workspace": workspace.to_dict(),
        "accounts": {"count": len(workspace.accounts.list()), "root": str(workspace.accounts.root)},
        "runs": {"count": len(workspace.run_index.list()), "path": str(workspace.run_index.path)},
    }
    if output_format is OutputFormat.json:
        _echo(payload)
    else:
        lines = ["Config Doctor", f"  valid    {str(payload['valid']).lower()}"]
        lines.extend(f"  issue    {issue}" for issue in issues)
        lines.append(f"  accounts {payload['accounts']['count']} from {payload['accounts']['root']}")
        lines.append(f"  runs     {payload['runs']['count']} from {payload['runs']['path']}")
        typer.echo("\n".join(lines))
    if issues:
        raise typer.Exit(2)


@config_app.command("explain")
def explain(
    run: str | None = typer.Option(None, "--run", help="Registered run name"),
    config_path: Path | None = typer.Option(None, "--config", help="Run config path"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    if run is None and config_path is None:
        raise typer.BadParameter("config explain requires --run or --config")
    if run is not None and config_path is not None:
        raise typer.BadParameter("use either --run or --config, not both")
    workspace = KairosWorkspace.resolve()
    path = workspace.run_index.resolve_config_path(run) if run is not None else config_path
    run_config = load_run_config(path)
    account_ref = run_config.account_ref
    account_source = None
    if account_ref:
        try:
            account_source = str(workspace.accounts.get(account_ref).source_path)
        except Exception:
            account_source = None
    payload = {
        "target": run or str(config_path),
        "workspace": {
            "root": str(workspace.root),
            "manifest": str(workspace.manifest_path) if workspace.manifest_path is not None else None,
        },
        "run_config": run_config.explain(),
        "sources": {
            "run_config": str(run_config.path) if run_config.path is not None else None,
            "workspace_manifest": str(workspace.manifest_path) if workspace.manifest_path is not None else None,
            "account": account_source,
        },
    }
    if output_format is OutputFormat.json:
        _echo(payload)
        return
    lines = [
        "Effective Config",
        f"  target             {payload['target']}",
        f"  run_config         {payload['sources']['run_config']}",
        f"  workspace_manifest {payload['sources']['workspace_manifest']}",
        f"  account.ref        {account_ref or ''}",
        f"  account.source     {account_source or ''}",
    ]
    typer.echo("\n".join(lines))


@config_app.command("operations")
def operations(
    limit: int | None = typer.Option(50, "--limit"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    workspace = KairosWorkspace.resolve()
    rows = _read_jsonl(workspace.operations_path)
    if limit is not None:
        rows = rows[-limit:]
    payload = {"operations": rows, "count": len(rows), "path": str(workspace.operations_path)}
    if output_format is OutputFormat.json:
        _echo(payload)
        return
    if not rows:
        typer.echo(f"Operations\n  none\n  path {workspace.operations_path}")
        return
    lines = ["Operations"]
    for row in rows:
        lines.append(f"  {row.get('event_time')}  {row.get('action')}  {row.get('target')}")
    typer.echo("\n".join(lines))


def _echo(payload: Mapping[str, object]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


@profile_app.command("list")
def profile_list(output_format: OutputFormat = typer.Option(OutputFormat.text, "--format")) -> None:
    workspace = KairosWorkspace.resolve()
    root = workspace.workspace_root / "profiles"
    selected = _selected_profile(workspace.state_root / "selection.json")
    profiles = [
        {"name": path.stem, "path": str(path), "selected": path.stem == selected}
        for path in sorted(root.glob("*.toml"))
    ]
    payload = {"profiles": profiles, "count": len(profiles), "root": str(root), "selected": selected}
    if output_format is OutputFormat.json:
        _echo(payload)
        return
    if not profiles:
        typer.echo(f"Profiles\n  none\n  root {root}")
        return
    lines = ["Profiles"]
    for item in profiles:
        marker = "*" if item["selected"] else " "
        lines.append(f" {marker} {item['name']}  {item['path']}")
    typer.echo("\n".join(lines))


@profile_app.command("use")
def profile_use(name: str = typer.Argument(...)) -> None:
    workspace = KairosWorkspace.resolve()
    path = workspace.workspace_root / "profiles" / f"{name}.toml"
    if not path.exists():
        raise typer.BadParameter(f"profile does not exist: {path}")
    selection_path = workspace.state_root / "selection.json"
    selection_path.parent.mkdir(parents=True, exist_ok=True)
    selection_path.write_text(json.dumps({"profile": name}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    workspace.operations.append("config.profile.use", target={"profile": name}, payload={"path": path, "selection": selection_path})
    _echo({"profile": name, "path": str(path), "selection": str(selection_path)})


@profile_app.command("create")
def profile_create(
    name: str = typer.Argument(...),
    source: Path | None = typer.Option(None, "--from", help="Template TOML to copy"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    workspace = KairosWorkspace.resolve()
    path = workspace.workspace_root / "profiles" / f"{name}.toml"
    if path.exists() and not force:
        raise typer.BadParameter(f"profile already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if source is None:
        content = "[cli]\nformat = \"text\"\n"
    else:
        if not source.exists():
            raise typer.BadParameter(f"profile template does not exist: {source}")
        content = source.read_text(encoding="utf-8")
    path.write_text(content, encoding="utf-8")
    workspace.operations.append("config.profile.create", target={"profile": name}, payload={"path": path, "source": source})
    _echo({"profile": name, "path": str(path), "source": None if source is None else str(source)})


def _selected_profile(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(value, Mapping):
        return None
    selected = value.get("profile")
    return selected if isinstance(selected, str) and selected.strip() else None


__all__ = ["config_app"]
