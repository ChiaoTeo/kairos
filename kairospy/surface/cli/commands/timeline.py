from __future__ import annotations

from pathlib import Path
from typing import Mapping

import typer

from kairospy.application.system.facade.context import workspace as resolve_workspace
from kairospy.application.system.facade.run_control import RuntimeMode
from kairospy.surface.cli.options import OutputFormat, resolve_output
from kairospy.surface.rendering.writer import write_result
from kairospy.surface.timeline import TimelineDataLoader, find_latest_instance, serve_timeline
from kairospy.surface.timeline.loader import list_instances


timeline_app = typer.Typer(no_args_is_help=True, help="Timeline viewer commands")


@timeline_app.command("list")
def list_timeline_runs(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Run id to filter, for example binance-btc-funding-arbitrage-backtest"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    root: Path | None = typer.Option(None, "--root", help="Run root. Defaults to the workspace run root."),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    rows = list_instances(_run_root(root), mode=mode.value if mode else None, run_id=target)
    payload = {"instances": rows, "count": len(rows)}
    output = resolve_output(ctx, output_format, default=OutputFormat.text)
    if output is OutputFormat.json:
        write_result(payload, output=OutputFormat.json)
        return
    typer.echo(_render_instances(payload))


@timeline_app.command("export")
def export_timeline(
    ctx: typer.Context,
    instance_path: Path | None = typer.Argument(None, help="Run instance directory"),
    latest: str | None = typer.Option(None, "--latest", help="Load latest instance for the given run id."),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    root: Path | None = typer.Option(None, "--root", help="Run root. Defaults to the workspace run root."),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    path = _resolve_instance_path(instance_path, latest=latest, mode=mode, root=root)
    data = TimelineDataLoader(path).load()
    write_result(data, output=resolve_output(ctx, output_format, default=OutputFormat.json))


@timeline_app.command("open")
def open_timeline(
    instance_path: Path | None = typer.Argument(None, help="Run instance directory"),
    latest: str | None = typer.Option(None, "--latest", help="Open latest instance for the given run id."),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    root: Path | None = typer.Option(None, "--root", help="Run root. Defaults to the workspace run root."),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    browser: bool = typer.Option(True, "--browser/--no-browser", help="Open the viewer in the default browser."),
) -> None:
    path = _resolve_instance_path(instance_path, latest=latest, mode=mode, root=root)
    serve_timeline(path, host=host, port=port, open_browser=browser)


def _resolve_instance_path(
    instance_path: Path | None,
    *,
    latest: str | None,
    mode: RuntimeMode | None,
    root: Path | None,
) -> Path:
    if instance_path is not None and latest is not None:
        raise typer.BadParameter("pass INSTANCE_PATH or --latest, not both")
    if instance_path is not None:
        return instance_path.expanduser().resolve()
    if latest is None:
        raise typer.BadParameter("timeline command requires INSTANCE_PATH or --latest RUN_ID")
    return find_latest_instance(_run_root(root), mode=mode.value if mode else None, run_id=latest)


def _run_root(root: Path | None) -> Path:
    return root.expanduser().resolve() if root is not None else resolve_workspace().run_root


def _render_instances(payload: Mapping[str, object]) -> str:
    rows = payload.get("instances")
    if not isinstance(rows, list) or not rows:
        return "Timeline Runs\n  none"
    columns = ("mode", "run_id", "run_instance_id", "trace", "risk", "equity", "directory")
    table_rows = [_instance_row(row) for row in rows if isinstance(row, Mapping)]
    widths = {column: max(len(column), *(len(str(row.get(column, "-"))) for row in table_rows)) for column in columns}
    lines = [
        "Timeline Runs",
        "  " + "  ".join(column.ljust(widths[column]) for column in columns),
        "  " + "  ".join("-" * widths[column] for column in columns),
    ]
    for row in table_rows:
        lines.append("  " + "  ".join(str(row.get(column, "-")).ljust(widths[column]) for column in columns))
    return "\n".join(lines)


def _instance_row(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "mode": row.get("mode") or "-",
        "run_id": row.get("run_id") or "-",
        "run_instance_id": row.get("run_instance_id") or "-",
        "trace": row.get("decision_trace_count") or 0,
        "risk": row.get("risk_snapshot_count") or 0,
        "equity": row.get("equity_count") or 0,
        "directory": row.get("directory") or "-",
    }


__all__ = ["timeline_app"]
