"""Single-screen guided command line for the Kairos Workbench."""

from __future__ import annotations

import shlex
from typing import Any, Callable

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Input, RichLog, Static
from textual.worker import Worker

from kairospy.investment.apps.reference.application import ReferenceApplication
from kairospy.surface.console.models import (
    ObserveSnapshot,
    component_rows,
    recommended_action,
)

from ..widgets import WorkbenchCommandInput, WorkspaceHeader


class CommandLineScreen(Screen[None]):
    """One output region and one stateful, guided command input."""

    TITLE = "Kairos"
    SUB_TITLE = "命令"
    BINDINGS = [
        Binding("ctrl+l", "clear", "清屏", show=False),
        Binding("escape", "cancel_pending", "取消", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._pending_argument: str | None = None
        self._pending_confirmation: tuple[str, Callable[[], Any]] | None = None
        self._active_worker: Worker[Any] | None = None

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield RichLog(
            id="command-output",
            wrap=True,
            highlight=False,
            markup=False,
        )
        yield Static("就绪", id="command-status")
        yield WorkbenchCommandInput(id="command-input")
        yield Static(
            "Enter 执行  ·  ↑↓ 历史  ·  Esc 取消引导  ·  Ctrl+C 取消任务",
            id="command-hints",
        )

    def on_mount(self) -> None:
        self._write_welcome()
        self._input().focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "command-input":
            return
        value = event.value.strip()
        if not value:
            return
        command_input = self._input()
        command_input.remember(value)
        command_input.value = ""
        self.submit(value)

    def submit(self, value: str) -> None:
        """Execute input through the same path used by the visible prompt."""

        value = value.strip()
        if not value:
            return
        self._write_prompt(value)
        if self._pending_argument is not None:
            pending = self._pending_argument
            if value.lower() in {"cancel", "取消"}:
                self.action_cancel_pending()
            else:
                self._pending_argument = None
                self._input().placeholder = "输入命令；Enter 提交"
                self._dispatch(pending, (value,))
            self._input().focus()
            return
        command, arguments = _parse_command(value)
        self._dispatch(command, arguments)
        self._input().focus()

    def _dispatch(self, command: str, arguments: tuple[str, ...]) -> None:
        if command == "help":
            self._write(_help_renderable())
        elif command == "clear":
            self.action_clear()
        elif command == "observe":
            self._run("observe", self._read_observe)
        elif command == "market":
            query = " ".join(arguments).strip()
            if query:
                self._run("market", lambda: self._find_markets(query))
            else:
                self._request_argument(
                    "market",
                    "请输入市场代码或名称",
                    "例如 AAPL、BTCUSDT；输入 cancel 取消。",
                )
        elif command == "confirm":
            self._confirm_pending()
        elif command == "cancel":
            self.action_cancel_pending()
        else:
            self._write_error(f"未知命令：{command or value_or_unknown(arguments)}")
            self._write(Text("输入 help 查看当前可用命令。", style="dim"))

    def _request_argument(self, command: str, prompt: str, detail: str) -> None:
        self._pending_argument = command
        self._write(
            Panel(Group(Text(prompt, style="bold"), Text(detail)), title=command)
        )
        self._set_status(f"等待输入 · {command}")
        self._input().placeholder = prompt

    def action_clear(self) -> None:
        self._output().clear()
        self._write(Text("输出已清空；业务状态没有改变。", style="dim"))

    def action_cancel_pending(self) -> None:
        if self._pending_argument is not None:
            command = self._pending_argument
            self._pending_argument = None
            self._input().placeholder = "输入命令；Enter 提交"
            self._write(Text(f"已取消 {command} 输入。", style="dim"))
            self._set_status("就绪")
            return
        if self._pending_confirmation is not None:
            summary, _ = self._pending_confirmation
            self._pending_confirmation = None
            self._write(Text(f"已取消：{summary}", style="dim"))
            self._set_status("就绪")
            return
        cancelled = self.workers.cancel_node(self)
        if cancelled:
            self._write(Text(f"已取消 {len(cancelled)} 个当前任务。", style="dim"))
            self._set_status("就绪")

    def request_confirmation(
        self,
        summary: str,
        action: Callable[[], Any],
    ) -> None:
        """Stage a dangerous action in the command stream, without a modal."""

        self._pending_confirmation = (summary, action)
        self._write(
            Panel(
                Text.from_markup(
                    f"{summary}\n\n输入 [bold]confirm[/bold] 继续，输入 "
                    "[bold]cancel[/bold] 或按 Esc 取消。"
                ),
                title="需要确认",
                border_style="yellow",
            )
        )
        self._set_status("等待确认")

    def _confirm_pending(self) -> None:
        if self._pending_confirmation is None:
            self._write(Text("当前没有等待确认的操作。", style="dim"))
            return
        summary, action = self._pending_confirmation
        self._pending_confirmation = None
        self._run("confirmed", action, status=f"正在执行：{summary}")

    def _run(
        self,
        kind: str,
        operation: Callable[[], Any],
        *,
        status: str | None = None,
    ) -> None:
        self._set_status(status or _running_status(kind))
        self._active_worker = self.run_worker(
            operation,
            name=f"command-{kind}",
            group="guided-command",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _read_observe(self) -> ObserveSnapshot | None:
        return self.app.state.refresh_snapshot()  # type: ignore[attr-defined,no-any-return]

    def _find_markets(self, query: str) -> tuple[Any, ...]:
        state = self.app.state  # type: ignore[attr-defined]
        if state.owner is None:
            raise RuntimeError(state.load_error or "当前没有可用的 workspace")
        application = ReferenceApplication.from_database(
            state.owner.paths.reference_database()
        )
        return application.find_markets(query=query, active_only=True, limit=25)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "guided-command":
            return
        if event.worker is not self._active_worker:
            return
        kind = event.worker.name.removeprefix("command-")
        if event.state.name == "SUCCESS":
            self._render_result(kind, event.worker.result)
            self._set_status("就绪")
            self._active_worker = None
        elif event.state.name == "ERROR":
            self._write_error(str(event.worker.error))
            self._set_status("失败 · 可继续输入")
            self._active_worker = None
        elif event.state.name == "CANCELLED":
            self._set_status("已取消 · 可继续输入")
            self._active_worker = None

    def _render_result(self, kind: str | None, result: Any) -> None:
        if kind == "observe":
            self._write(
                Text("当前没有可用的系统观察结果。", style="dim")
                if result is None
                else _observe_renderable(result)
            )
        elif kind == "market":
            self._write(_markets_renderable(tuple(result or ())))
        else:
            self._write(Panel(str(result), title="完成", border_style="green"))

    def _write_welcome(self) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        self._write(
            Group(
                Text("Kairos Workbench", style="bold cyan"),
                Text(f"Workspace: {state.workspace_id}", style="dim"),
                Text("输入 help 查看命令；输入命令后会逐步提示缺少的参数。"),
            )
        )

    def _write_prompt(self, value: str) -> None:
        prompt = Text("kairos › ", style="bold bright_blue")
        prompt.append(value)
        self._write(prompt)

    def _write_error(self, value: str) -> None:
        self._write(Panel(value, title="命令失败", border_style="red"))

    def _write(self, renderable: RenderableType) -> None:
        self._output().write(renderable)

    def _output(self) -> RichLog:
        return self.query_one("#command-output", RichLog)

    def _input(self) -> WorkbenchCommandInput:
        return self.query_one("#command-input", WorkbenchCommandInput)

    def _set_status(self, value: str) -> None:
        self.query_one("#command-status", Static).update(value)


def _parse_command(value: str) -> tuple[str, tuple[str, ...]]:
    stripped = value.strip().removeprefix("/")
    try:
        parts = shlex.split(stripped)
    except ValueError:
        return "", ()
    if not parts:
        return "help", ()
    return parts[0].lower(), tuple(parts[1:])


def value_or_unknown(arguments: tuple[str, ...]) -> str:
    return " ".join(arguments) or "<无法解析>"


def _help_renderable() -> RenderableType:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    table.add_row("observe", "查看 Workspace 组件和 Launch 状态")
    table.add_row("market [代码]", "搜索有效市场标的；省略代码时进入引导")
    table.add_row("clear", "清空当前输出显示")
    table.add_row("confirm / cancel", "继续或取消等待中的步骤")
    table.add_row("help", "显示这份帮助")
    return Panel(table, title="命令", border_style="cyan")


def _observe_renderable(snapshot: ObserveSnapshot) -> RenderableType:
    table = Table(show_header=True, header_style="bold")
    table.add_column("组件")
    table.add_column("状态")
    table.add_column("新鲜度")
    table.add_column("详情")
    for row in component_rows(snapshot):
        table.add_row(*(str(value) for value in row))
    summary = Text(
        f"{snapshot.workspace_id} · {snapshot.overall_status} · "
        f"{len(snapshot.launches)} 个 Launch\n",
        style="bold",
    )
    summary.append(f"下一步：{recommended_action(snapshot)}", style="dim")
    return Panel(Group(summary, table), title="系统状态", border_style="cyan")


def _markets_renderable(markets: tuple[Any, ...]) -> RenderableType:
    if not markets:
        return Panel("没有找到匹配的有效标的。", title="市场搜索")
    table = Table(show_header=True, header_style="bold")
    table.add_column("代码")
    table.add_column("交易所")
    table.add_column("类型")
    table.add_column("计价")
    table.add_column("状态")
    for market in markets:
        table.add_row(
            str(market.venue_symbol or market.instrument.display_symbol),
            str(market.exchange_id).rsplit(":", 1)[-1],
            str(market.instrument_kind),
            str(market.quote_asset or "—"),
            str(market.status),
        )
    return Panel(table, title=f"找到 {len(markets)} 个标的", border_style="cyan")


def _running_status(kind: str) -> str:
    return {
        "observe": "正在读取系统状态…",
        "market": "正在搜索市场标的…",
    }.get(kind, "正在执行…")
