from __future__ import annotations

import json
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from .data import ObserveReader
from .models import ObserveSnapshot, component_rows, launch_rows, recommended_action


CONSOLE_CSS = """
Screen { layout: vertical; }
#summary { height: 3; padding: 0 1; background: $surface; }
#metrics { height: 5; padding: 0 1; }
.metric { width: 1fr; height: 3; margin-right: 1; padding: 0 1; border: round $surface-lighten-2; }
.metric-title { color: $text-muted; }
.metric-value { text-style: bold; }
#body { height: 1fr; padding: 0 1; }
#components-panel { width: 2fr; height: 1fr; border: round $surface-lighten-2; }
#side { width: 1fr; height: 1fr; margin-left: 1; }
#launches-panel { height: 1fr; border: round $surface-lighten-2; }
#market-panel { height: 1fr; margin-top: 1; border: round $surface-lighten-2; }
.panel-title { height: 1; padding: 0 1; background: $surface; color: $primary; text-style: bold; }
DataTable, RichLog { height: 1fr; padding: 0 1; }
#status { height: 1; padding: 0 1; color: $text-muted; }
"""


class ObserveApp(App[None]):
    """Read-only project and launch operator console with actionable guidance."""

    CSS = CONSOLE_CSS
    TITLE = "Kairos Observe"
    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, reader: ObserveReader, *, refresh_seconds: float = 2.0) -> None:
        super().__init__()
        if refresh_seconds <= 0:
            raise ValueError("refresh_seconds must be positive")
        self.reader = reader
        self.refresh_seconds = refresh_seconds
        self._last: ObserveSnapshot | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Loading…", id="summary")
        with Horizontal(id="metrics"):
            yield _metric("Overall", "-", "overall")
            yield _metric("Healthy", "-", "healthy")
            yield _metric("Degraded", "-", "degraded")
            yield _metric("Launches", "-", "launches")
        with Horizontal(id="body"):
            with Vertical(id="components-panel"):
                yield Static("Components", classes="panel-title")
                yield DataTable(
                    id="components",
                    cursor_type="row",
                    zebra_stripes=True,
                    show_row_labels=False,
                )
            with Vertical(id="side"):
                with Vertical(id="launches-panel"):
                    yield Static("Launches", classes="panel-title")
                    yield DataTable(
                        id="launch-table",
                        cursor_type="row",
                        zebra_stripes=True,
                        show_row_labels=False,
                    )
                with Vertical(id="market-panel"):
                    yield Static("Market snapshot", classes="panel-title")
                    yield RichLog(id="market", wrap=True, highlight=False)
        yield Static("r refresh · q quit", id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#components", DataTable)
        table.add_columns("component", "status", "freshness", "detail")
        launches = self.query_one("#launch-table", DataTable)
        launches.add_columns("launch", "mode", "state", "instance")
        self.set_interval(self.refresh_seconds, self.action_refresh)
        self.action_refresh()

    def action_refresh(self) -> None:
        self.run_worker(self._read, thread=True, exclusive=True)

    def _read(self) -> ObserveSnapshot:
        try:
            return self.reader.read()
        except Exception as error:
            if self._last is not None:
                return ObserveSnapshot(
                    workspace_id=self._last.workspace_id,
                    components=self._last.components,
                    launches=self._last.launches,
                    market_snapshot=self._last.market_snapshot,
                    error=str(error),
                )
            return ObserveSnapshot(workspace_id="-", components={}, error=str(error))

    def on_worker_state_changed(self, event: Any) -> None:
        if event.state.name != "SUCCESS":
            return
        self._render(event.worker.result)

    def _render(self, snapshot: ObserveSnapshot) -> None:
        self._last = snapshot
        rows = component_rows(snapshot)
        healthy = sum(row[1] in {"ok", "ready", "running"} for row in rows)
        degraded = len(rows) - healthy
        self.query_one("#summary", Static).update(
            f"{snapshot.workspace_id}  overall={snapshot.overall_status}  "
            f"updated={snapshot.observed_at.astimezone().strftime('%H:%M:%S')}"
            + (f"  error={snapshot.error}" if snapshot.error else "")
        )
        self._metric_value("overall", snapshot.overall_status)
        self._metric_value("healthy", str(healthy))
        self._metric_value("degraded", str(degraded))
        self._metric_value("launches", str(len(snapshot.launches)))
        table = self.query_one("#components", DataTable)
        table.clear()
        for component, status, freshness, detail in rows:
            table.add_row(component, status, freshness, detail)
        launches = self.query_one("#launch-table", DataTable)
        launches.clear()
        for launch_id, mode, state, instance in launch_rows(snapshot):
            launches.add_row(launch_id, mode, state, instance)
        market = self.query_one("#market", RichLog)
        market.clear()
        if snapshot.market_snapshot is None:
            market.write(Text("market process is not running", style="yellow"))
        elif snapshot.market_snapshot.get("error"):
            market.write(Text(str(snapshot.market_snapshot["error"]), style="red"))
        else:
            market.write(
                json.dumps(
                    _market_summary(dict(snapshot.market_snapshot)),
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
        self.query_one("#status", Static).update(
            f"last refresh: {snapshot.observed_at.astimezone().strftime('%H:%M:%S')}"
            f" · next: {recommended_action(snapshot)}"
        )

    def _metric_value(self, metric_id: str, value: str) -> None:
        self.query_one(f"#{metric_id} .metric-value", Static).update(value)


def _metric(title: str, value: str, metric_id: str) -> Vertical:
    return Vertical(
        Static(title, classes="metric-title"),
        Static(value, classes="metric-value"),
        id=metric_id,
        classes="metric",
    )


def _market_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        key: snapshot[key]
        for key in (
            "snapshot_id",
            "generation",
            "event_sequence",
            "views",
            "order_books",
        )
        if key in snapshot
    } or {"status": snapshot.get("status", "available")}
