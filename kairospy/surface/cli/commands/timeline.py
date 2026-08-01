from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Mapping

import typer

from kairospy.application.system.facade.context import workspace as resolve_workspace
from kairospy.application.launch.control import RuntimeMode
from kairospy.surface.cli.options import OutputFormat, resolve_output
from kairospy.surface.rendering.writer import write_result
from kairospy.surface.timeline import TimelineDataLoader, find_latest_instance, list_instances, serve_timeline


timeline_app = typer.Typer(no_args_is_help=True, help="Timeline viewer commands")


@timeline_app.command("list")
def list_timeline_launches(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Launch id to filter, for example binance-btc-funding-arbitrage-backtest"),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    root: Path | None = typer.Option(None, "--root", help="Launch root. Defaults to the workspace launch root."),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    rows = list_instances(_launch_root(root), mode=mode.value if mode else None, launch_id=target)
    payload = {"instances": rows, "count": len(rows)}
    output = resolve_output(ctx, output_format, default=OutputFormat.text)
    if output in {OutputFormat.json, OutputFormat.jsonl}:
        write_result(payload, output=output)
        return
    typer.echo(_render_instances(payload))


@timeline_app.command("export")
def export_timeline(
    ctx: typer.Context,
    instance_path: Path | None = typer.Argument(None, help="Launch instance directory"),
    latest: str | None = typer.Option(None, "--latest", help="Load latest instance for the given launch id."),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    root: Path | None = typer.Option(None, "--root", help="Launch root. Defaults to the workspace launch root."),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    path = _resolve_instance_path(instance_path, latest=latest, mode=mode, root=root)
    data = TimelineDataLoader(path).load()
    write_result(data, output=resolve_output(ctx, output_format, default=OutputFormat.json))


@timeline_app.command("open")
def open_timeline(
    latest: str | None = typer.Option(None, "--latest", help="Select latest instance for the given launch id by default."),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    root: Path | None = typer.Option(None, "--root", help="Launch root. Defaults to the workspace launch root."),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    browser: bool = typer.Option(True, "--browser/--no-browser", help="Open the viewer in the default browser."),
) -> None:
    launch_root = _launch_root(root)
    path = _resolve_latest_instance_path(latest=latest, mode=mode, launch_root=launch_root)
    serve_timeline(
        launch_root,
        selected_instance_path=path,
        mode=mode.value if mode else None,
        launch_id=latest,
        host=host,
        port=port,
        open_browser=browser,
        static_root=_viewer_dist(),
    )


@timeline_app.command("api")
def timeline_api(
    latest: str | None = typer.Option(None, "--latest", help="Select latest instance for the given launch id by default."),
    mode: RuntimeMode | None = typer.Option(None, "--mode"),
    root: Path | None = typer.Option(None, "--root", help="Launch root. Defaults to the workspace launch root."),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
) -> None:
    launch_root = _launch_root(root)
    path = _resolve_latest_instance_path(latest=latest, mode=mode, launch_root=launch_root)
    serve_timeline(
        launch_root,
        selected_instance_path=path,
        mode=mode.value if mode else None,
        launch_id=latest,
        host=host,
        port=port,
        open_browser=False,
        static_root=None,
    )


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
        raise typer.BadParameter("timeline command requires INSTANCE_PATH or --latest LAUNCH_ID")
    return find_latest_instance(_launch_root(root), mode=mode.value if mode else None, launch_id=latest)


def _resolve_latest_instance_path(*, latest: str | None, mode: RuntimeMode | None, launch_root: Path) -> Path | None:
    if latest is None:
        return None
    return find_latest_instance(launch_root, mode=mode.value if mode else None, launch_id=latest)


def _launch_root(root: Path | None) -> Path:
    return root.expanduser().resolve() if root is not None else resolve_workspace().launch_root


def _viewer_dist() -> Path | None:
    path = Path(str(files("kairospy.surface.timeline").joinpath("static")))
    return path if (path / "index.html").exists() else None


def _render_instances(payload: Mapping[str, object]) -> str:
    rows = payload.get("instances")
    if not isinstance(rows, list) or not rows:
        return "Timeline Launches\n  none"
    columns = ("mode", "launch_id", "launch_instance_id", "trace", "risk", "equity", "directory")
    table_rows = [_instance_row(row) for row in rows if isinstance(row, Mapping)]
    widths = {column: max(len(column), *(len(str(row.get(column, "-"))) for row in table_rows)) for column in columns}
    lines = [
        "Timeline Launches",
        "  " + "  ".join(column.ljust(widths[column]) for column in columns),
        "  " + "  ".join("-" * widths[column] for column in columns),
    ]
    for row in table_rows:
        lines.append("  " + "  ".join(str(row.get(column, "-")).ljust(widths[column]) for column in columns))
    return "\n".join(lines)


def _instance_row(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "mode": row.get("mode") or "-",
        "launch_id": row.get("launch_id") or "-",
        "launch_instance_id": row.get("launch_instance_id") or "-",
        "trace": row.get("decision_trace_count") or 0,
        "risk": row.get("risk_snapshot_count") or 0,
        "equity": row.get("equity_count") or 0,
        "directory": row.get("directory") or "-",
    }


__all__ = ["timeline_app"]
