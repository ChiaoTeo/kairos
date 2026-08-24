"""Embedded workspace observation screen."""

from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, RichLog, Static
from textual.worker import Worker

from ..widgets import WorkspaceHeader

from kairospy.surface.console.models import (
    ObserveSnapshot,
    component_rows,
    launch_rows,
    recommended_action,
)


class ObserveScreen(Screen[None]):
    """Live project facts inside the shared workbench shell."""

    TITLE = "Kairos"
    SUB_TITLE = "首页 › 系统维护 › 实时观测"
    BINDINGS = [
        Binding("escape", "back", "返回"),
        Binding("r", "refresh", "刷新"),
    ]

    def __init__(self, *, refresh_seconds: float = 2.0) -> None:
        super().__init__()
        self.refresh_seconds = refresh_seconds
        self._last: ObserveSnapshot | None = None

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Static("正在读取运行状态…", id="observe-summary")
        with Horizontal(id="observe-body"):
            with Vertical(id="observe-components-panel"):
                yield Static("系统组件", classes="panel-title")
                yield DataTable(
                    id="observe-components", cursor_type="row", zebra_stripes=True
                )
            with Vertical(id="observe-side"):
                yield Static("Launch", classes="panel-title")
                yield DataTable(
                    id="observe-launches", cursor_type="row", zebra_stripes=True
                )
                yield Static("Market snapshot", classes="panel-title")
                yield RichLog(id="observe-market", wrap=True, highlight=False)
        yield Static("", id="observe-status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#observe-components", DataTable).add_columns(
            "组件", "状态", "新鲜度", "详情"
        )
        self.query_one("#observe-launches", DataTable).add_columns(
            "Launch", "模式", "状态", "实例"
        )
        self.set_interval(self.refresh_seconds, self.action_refresh)
        self.action_refresh()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.run_worker(
            self._read,
            name="observe-refresh",
            group="observe-refresh",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _read(self) -> ObserveSnapshot | None:
        return self.app.state.refresh_snapshot()  # type: ignore[attr-defined,no-any-return]

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "observe-refresh":
            return
        if event.state.name == "ERROR":
            self.query_one("#observe-status", Static).update(
                f"刷新失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS" and event.worker.result is not None:
            self._render(event.worker.result)

    def _render(self, snapshot: ObserveSnapshot) -> None:
        self._last = snapshot
        rows = component_rows(snapshot)
        healthy = sum(row[1] in {"ok", "ready", "running"} for row in rows)
        self.query_one("#observe-summary", Static).update(
            f"{snapshot.workspace_id}  ·  {snapshot.overall_status}  ·  "
            f"{healthy}/{len(rows)} 个组件正常  ·  {len(snapshot.launches)} 个 Launch"
        )
        components = self.query_one("#observe-components", DataTable)
        components.clear()
        for row in rows:
            components.add_row(*row)
        launches = self.query_one("#observe-launches", DataTable)
        launches.clear()
        for row in launch_rows(snapshot):
            launches.add_row(*row)
        market = self.query_one("#observe-market", RichLog)
        market.clear()
        market.write(
            "Market 服务未运行"
            if snapshot.market_snapshot is None
            else json.dumps(
                _market_summary(dict(snapshot.market_snapshot)),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        self.query_one("#observe-status", Static).update(
            f"更新时间 {snapshot.observed_at.astimezone().strftime('%H:%M:%S')}  ·  下一步：{recommended_action(snapshot)}"
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
