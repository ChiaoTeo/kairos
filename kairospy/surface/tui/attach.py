from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
import shlex
from typing import Any

from rich.pretty import Pretty
from rich.table import Table
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, RichLog, Static, Switch, TabPane, TabbedContent, Tree

from kairospy.application.launch.attach import LaunchAttachSession
from kairospy.application.modes import RuntimeMode


ATTACH_APP_CSS = """
Screen {
    layout: vertical;
    background: $background;
    color: $text;
}

Header, Footer {
    background: $surface;
}

#attach-shell {
    height: 1fr;
    layout: vertical;
}

#attach-status {
    height: 3;
    padding: 0 1;
    background: $surface;
    border-bottom: solid $primary;
}

#attach-body {
    height: 1fr;
    layout: horizontal;
}

#attach-sidebar {
    width: 31;
    min-width: 27;
    height: 1fr;
    padding: 1;
    background: $panel;
    border-right: solid $surface-lighten-2;
}

.sidebar-section {
    height: auto;
    margin-bottom: 1;
}

.section-title {
    height: 1;
    margin-bottom: 1;
    color: $primary;
    text-style: bold;
}

.kv-table {
    height: auto;
    margin-bottom: 1;
}

#quick-actions {
    height: auto;
    grid-size: 2;
    grid-gutter: 1 1;
}

#quick-actions Button {
    width: 1fr;
    height: 3;
}

#follow-row {
    height: 3;
    layout: horizontal;
    content-align: left middle;
}

#follow-row Label {
    width: 1fr;
    height: 3;
    content-align: left middle;
}

#main-tabs {
    width: 1fr;
    height: 1fr;
}

TabbedContent {
    height: 1fr;
}

TabPane {
    height: 1fr;
    padding: 1;
}

.pane-body {
    height: 1fr;
    border: round $surface-lighten-2;
    background: $panel;
}

#overview-grid {
    height: auto;
    grid-size: 4;
    grid-gutter: 1 1;
    margin-bottom: 1;
}

.metric {
    height: 5;
    padding: 1;
    border: round $surface-lighten-2;
    background: $boost;
}

.metric-title {
    height: 1;
    color: $text-muted;
}

.metric-value {
    height: 2;
    margin-top: 1;
    color: $text;
    text-style: bold;
}

#overview-details, #command-output, #raw-output {
    height: 1fr;
    padding: 1;
    overflow: auto;
}

#logs-toolbar, #commands-toolbar, #trade-toolbar {
    height: 3;
    layout: horizontal;
    margin-bottom: 1;
}

#logs-toolbar Input, #commands-toolbar Input, #trade-toolbar Input {
    width: 1fr;
    height: 3;
    border: tall $surface-lighten-2;
    background: $boost;
}

#logs-toolbar Button, #commands-toolbar Button, #trade-toolbar Button {
    width: auto;
    min-width: 10;
    height: 3;
    margin-left: 1;
}

#logs {
    height: 1fr;
    padding: 0 1;
    background: $panel;
}

DataTable {
    height: 1fr;
    background: $panel;
    scrollbar-background: $panel;
    scrollbar-color: $primary;
}

DataTable > .datatable--header {
    background: $surface;
    color: $text;
    text-style: bold;
}

DataTable > .datatable--cursor {
    background: $primary;
    color: $text;
    text-style: bold;
}

Tree {
    height: 1fr;
    padding: 0 1;
    background: $panel;
}

#command-input {
    height: 3;
    border: tall $surface-lighten-2;
    background: $boost;
}

#command-input:focus, #log-filter:focus, #command-kind:focus, #command-payload:focus, #trade-account:focus {
    border: tall $primary;
}
"""


@dataclass(frozen=True, slots=True)
class AttachViewModel:
    mode: str
    launch_id: str
    phase: str
    heartbeat_at: str
    heartbeat_age: str
    directory: str
    state_file: str
    log_file: str
    record_count: str
    records: tuple[Mapping[str, object], ...]

    @classmethod
    def from_status(cls, status: Mapping[str, object]) -> "AttachViewModel":
        records_value = status.get("records")
        records = tuple(item for item in records_value if isinstance(item, Mapping)) if isinstance(records_value, list) else ()
        heartbeat_at = _text(status.get("heartbeat_at"))
        heartbeat_age = _duration(status.get("heartbeat_age_seconds"))
        return cls(
            mode=_text(status.get("mode")),
            launch_id=_text(status.get("launch_id")),
            phase=_text(status.get("phase") or "unknown"),
            heartbeat_at=heartbeat_at,
            heartbeat_age=heartbeat_age,
            directory=_text(status.get("directory")),
            state_file=_text(status.get("state_file")),
            log_file=_text(status.get("log_file")),
            record_count=str(status.get("record_count") or len(records)),
            records=records,
        )

    @property
    def target_label(self) -> str:
        return f"{self.mode}:{self.launch_id}"

    @property
    def heartbeat_label(self) -> str:
        if self.heartbeat_at == "-":
            return "-"
        return f"{self.heartbeat_at} ({self.heartbeat_age})"


class RuntimeAttachApp(App[None]):
    CSS = ATTACH_APP_CSS
    BINDINGS = [
        Binding("r", "refresh_status", "Refresh"),
        Binding("i", "inspect", "Inspect"),
        Binding("s", "stop_runtime", "Stop"),
        Binding("f", "toggle_follow", "Follow"),
        Binding("c", "clear_logs", "Clear Logs"),
        Binding("ctrl+c", "quit", "Detach"),
        Binding("q", "quit", "Detach"),
    ]

    def __init__(self, session: LaunchAttachSession) -> None:
        super().__init__()
        self.session = session
        self.title = f"Kairos Attach | {session.target.mode.value}:{session.target.launch_id}"
        self._log_position = 0
        self._log_filter = ""
        self._follow_logs = True

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="attach-shell"):
            yield Static("", id="attach-status")
            with Horizontal(id="attach-body"):
                with Vertical(id="attach-sidebar"):
                    yield Static("Target", classes="section-title")
                    yield Static("", id="target-details", classes="kv-table")
                    yield Static("Runtime", classes="section-title")
                    yield Static("", id="runtime-details", classes="kv-table")
                    yield Static("Quick Actions", classes="section-title")
                    with Horizontal(id="quick-actions"):
                        yield Button("Refresh", id="action-refresh", variant="primary")
                        yield Button("Inspect", id="action-inspect")
                        yield Button("Stop", id="action-stop", variant="warning")
                    with Horizontal(id="follow-row"):
                        yield Label("Follow logs")
                        yield Switch(value=True, id="follow-switch")
                with TabbedContent(id="main-tabs"):
                    with TabPane("Overview", id="overview-tab"):
                        with VerticalScroll(id="overview-pane", classes="pane-body"):
                            with Horizontal(id="overview-grid"):
                                yield _metric("Phase", "-", "metric-phase")
                                yield _metric("Heartbeat", "-", "metric-heartbeat")
                                yield _metric("Records", "-", "metric-records")
                                yield _metric("Log", "-", "metric-log")
                            yield RichLog(id="overview-details", wrap=True, highlight=False)
                    with TabPane("Logs", id="logs-tab"):
                        with Horizontal(id="logs-toolbar"):
                            yield Input(placeholder="Filter logs", id="log-filter")
                            yield Button("Clear", id="logs-clear")
                        yield RichLog(id="logs", wrap=True, highlight=False, classes="pane-body")
                    with TabPane("State", id="state-tab"):
                        yield Tree("state", id="state-tree", classes="pane-body")
                    with TabPane("Records", id="records-tab"):
                        yield DataTable(id="records-table", cursor_type="row", zebra_stripes=True, show_row_labels=False)
                    with TabPane("Commands", id="commands-tab"):
                        with Horizontal(id="commands-toolbar"):
                            yield Input(placeholder="kind, e.g. account.current", id="command-kind")
                            yield Input(placeholder='JSON payload, e.g. {"account":"main"}', id="command-payload")
                            yield Button("Send", id="command-send", variant="primary")
                        yield RichLog(id="command-output", wrap=True, highlight=False, classes="pane-body")
                    with TabPane("Trade", id="trade-tab"):
                        with Horizontal(id="trade-toolbar"):
                            yield Input(placeholder="account", id="trade-account")
                            yield Button("Status", id="trade-status")
                            yield Button("Acquire", id="trade-acquire", variant="primary")
                            yield Button("Release", id="trade-release", variant="warning")
                        yield RichLog(id="trade-output", wrap=True, highlight=False, classes="pane-body")
                    with TabPane("Raw", id="raw-tab"):
                        yield RichLog(id="raw-output", wrap=True, highlight=False, classes="pane-body")
            yield Input(placeholder="status, inspect, stop, command KIND JSON, trade-status [ACCOUNT]", id="command-input")
        yield Footer()

    def on_mount(self) -> None:
        self._append_output("#command-output", "Attached", self._target_payload())
        self._refresh_status()
        self._refresh_state()
        self._poll_logs()
        self.set_interval(0.5, self._poll_logs)
        self.set_interval(1.0, self._refresh_status)
        self.set_interval(2.0, self._refresh_state)

    def action_refresh_status(self) -> None:
        self._refresh_status()
        self._refresh_state()

    def action_inspect(self) -> None:
        self._run_operation("inspect", lambda: self.session.inspect(), output="#command-output")

    def action_stop_runtime(self) -> None:
        self._run_operation("stop", lambda: self.session.stop(), output="#command-output")

    def action_toggle_follow(self) -> None:
        self._follow_logs = not self._follow_logs
        self.query_one("#follow-switch", Switch).value = self._follow_logs

    def action_clear_logs(self) -> None:
        self.query_one("#logs", RichLog).clear()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "action-refresh":
            self.action_refresh_status()
        elif button_id == "action-inspect":
            self.action_inspect()
        elif button_id == "action-stop":
            self.action_stop_runtime()
        elif button_id == "logs-clear":
            self.action_clear_logs()
        elif button_id == "command-send":
            self._send_form_command()
        elif button_id in {"trade-status", "trade-acquire", "trade-release"}:
            self._send_trade_command(button_id)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "follow-switch":
            self._follow_logs = event.value

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "log-filter":
            self._log_filter = event.value.strip()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "command-kind":
            self.query_one("#command-payload", Input).focus()
            return
        if event.input.id == "command-payload":
            self._send_form_command()
            return
        if event.input.id == "trade-account":
            self._send_trade_command("trade-status")
            return
        if event.input.id != "command-input":
            return
        value = event.value.strip()
        event.input.value = ""
        if not value:
            return
        if value in {"exit", "quit", "q", "detach"}:
            self.exit()
            return
        try:
            operation, callback = self._operation_for_command(value)
        except ValueError as error:
            self._append_error(str(error), output="#command-output")
            return
        output = "#trade-output" if operation.startswith("trade-") else "#command-output"
        self._run_operation(operation, callback, output=output)

    def _poll_logs(self) -> None:
        if not self._follow_logs:
            return
        previous_directory = self.session.target.directory
        chunk = self.session.read_log_since(self._log_position)
        if self.session.target.directory != previous_directory:
            self.query_one("#logs", RichLog).clear()
        self._log_position = chunk.position
        if not chunk.text:
            return
        logs = self.query_one("#logs", RichLog)
        for line in chunk.text.splitlines():
            if self._log_filter and self._log_filter not in line:
                continue
            logs.write(line)

    def _refresh_status(self) -> None:
        status = self.session.status()
        view = AttachViewModel.from_status(status)
        self.query_one("#attach-status", Static).update(summary_text(status))
        self.query_one("#target-details", Static).update(_kv_text([("mode", view.mode), ("launch", view.launch_id), ("dir", view.directory)]))
        self.query_one("#runtime-details", Static).update(
            _kv_text([("phase", view.phase), ("heartbeat", view.heartbeat_label), ("records", view.record_count)])
        )
        self._update_metric("metric-phase", view.phase)
        self._update_metric("metric-heartbeat", view.heartbeat_age)
        self._update_metric("metric-records", view.record_count)
        self._update_metric("metric-log", _short_path(view.log_file))
        self._update_overview(view)
        self._update_records(view.records)
        self._update_raw(status=status)

    def _refresh_state(self) -> None:
        state = self.session.read_state()
        tree = self.query_one("#state-tree", Tree)
        tree.clear()
        tree.root.set_label("state")
        _populate_tree(tree.root, state)
        tree.root.expand()
        self._update_raw(state=state)

    def _send_form_command(self) -> None:
        kind_input = self.query_one("#command-kind", Input)
        payload_input = self.query_one("#command-payload", Input)
        kind = kind_input.value.strip()
        if not kind:
            self._append_error("command kind is required", output="#command-output")
            return
        try:
            payload = _parse_payload([payload_input.value.strip()] if payload_input.value.strip() else [])
        except ValueError as error:
            self._append_error(str(error), output="#command-output")
            return
        self._run_operation(kind, lambda: self.session.submit_command(kind, payload), output="#command-output")

    def _send_trade_command(self, button_id: str) -> None:
        if self.session.target.mode is not RuntimeMode.SYSTEM:
            self._append_error("trade commands are only available for system attach", output="#trade-output")
            return
        account = self.query_one("#trade-account", Input).value.strip()
        if button_id == "trade-status":
            self._run_operation("trade-status", lambda: self.session.trade_status(account or None), output="#trade-output")
            return
        if not account:
            self._append_error(f"{button_id} requires account", output="#trade-output")
            return
        if button_id == "trade-acquire":
            self._run_operation("trade-acquire", lambda: self.session.trade_acquire(account), output="#trade-output")
        elif button_id == "trade-release":
            self._run_operation("trade-release", lambda: self.session.trade_release(account), output="#trade-output")

    def _run_operation(self, label: str, callback: object, *, output: str) -> None:
        try:
            result = callback()  # type: ignore[operator]
        except Exception as error:
            self._append_error(str(error), output=output)
            return
        self._append_output(output, label, result if isinstance(result, Mapping) else {"result": result})
        self._refresh_status()

    def _operation_for_command(self, line: str) -> tuple[str, object]:
        parts = shlex.split(line)
        if not parts:
            raise ValueError("empty attach command")
        command = parts[0]
        args = parts[1:]
        if command == "status":
            return "status", self.session.status
        if command == "inspect":
            return "inspect", self.session.inspect
        if command == "stop":
            return "stop", self.session.stop
        if command == "trade-status":
            _require_system(self.session)
            account = args[0] if args else None
            _reject_extra(args[1:])
            return "trade-status", lambda: self.session.trade_status(account)
        if command == "trade-acquire":
            _require_system(self.session)
            account = _require_arg(args, "trade-acquire requires account")
            _reject_extra(args[1:])
            return "trade-acquire", lambda: self.session.trade_acquire(account)
        if command == "trade-release":
            _require_system(self.session)
            account = _require_arg(args, "trade-release requires account")
            _reject_extra(args[1:])
            return "trade-release", lambda: self.session.trade_release(account)
        if command != "command":
            raise ValueError(f"unsupported attach command: {command}")
        kind = _require_arg(args, "command requires kind")
        payload = _parse_payload(args[1:])
        return kind, lambda: self.session.submit_command(kind, payload)

    def _append_output(self, selector: str, label: str, payload: Mapping[str, object]) -> None:
        output = self.query_one(selector, RichLog)
        output.write(f"[b]{label}[/b]")
        output.write(Pretty(dict(payload), expand_all=False))

    def _append_error(self, message: str, *, output: str) -> None:
        panel = self.query_one(output, RichLog)
        panel.write("[red]error[/red]")
        panel.write(message)

    def _update_metric(self, metric_id: str, value: str) -> None:
        self.query_one(f"#{metric_id} .metric-value", Static).update(value)

    def _update_overview(self, view: AttachViewModel) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold cyan")
        table.add_column()
        table.add_row("Target", view.target_label)
        table.add_row("Directory", view.directory)
        table.add_row("State file", view.state_file)
        table.add_row("Log file", view.log_file)
        table.add_row("Heartbeat", view.heartbeat_label)
        overview = self.query_one("#overview-details", RichLog)
        overview.clear()
        overview.write(table)

    def _update_records(self, records: Iterable[Mapping[str, object]]) -> None:
        table = self.query_one("#records-table", DataTable)
        table.clear(columns=True)
        columns = ("phase", "launch_instance_id", "pid", "started_at", "directory")
        table.add_columns(*columns)
        for record in records:
            table.add_row(*(_text(record.get(column)) for column in columns))

    def _update_raw(self, *, status: Mapping[str, object] | None = None, state: Mapping[str, object] | None = None) -> None:
        raw = self.query_one("#raw-output", RichLog)
        if status is not None:
            raw.clear()
            raw.write("[b]status[/b]")
            raw.write(Pretty(dict(status), expand_all=False))
        if state is not None:
            raw.write("[b]state[/b]")
            raw.write(Pretty(dict(state), expand_all=False))

    def _target_payload(self) -> Mapping[str, object]:
        target = self.session.target
        return {
            "mode": target.mode.value,
            "launch_id": target.launch_id,
            "root": str(target.root),
            "directory": str(target.directory),
            "state_file": str(target.state_file),
            "log_file": None if target.log_file is None else str(target.log_file),
        }


def _metric(title: str, value: str, metric_id: str) -> Vertical:
    return Vertical(Static(title, classes="metric-title"), Static(value, classes="metric-value"), id=metric_id, classes="metric")


def _populate_tree(node: Any, value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(child, Mapping | list):
                branch = node.add(str(key), expand=key in {"runtime", "strategy", "accounts", "positions", "errors"})
                _populate_tree(branch, child)
            else:
                node.add_leaf(f"{key}: {_text(child)}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, Mapping | list):
                branch = node.add(f"[{index}]")
                _populate_tree(branch, child)
            else:
                node.add_leaf(f"[{index}]: {_text(child)}")
        return
    node.add_leaf(_text(value))


def _parse_payload(args: list[str]) -> Mapping[str, object]:
    if not args:
        return {}
    if len(args) > 1:
        raise ValueError("command accepts at most one JSON payload argument")
    try:
        payload = json.loads(args[0])
    except json.JSONDecodeError as error:
        raise ValueError(f"expected JSON object: {error}") from error
    if not isinstance(payload, Mapping):
        raise ValueError("expected JSON object")
    return payload


def _require_arg(args: list[str], message: str) -> str:
    if not args:
        raise ValueError(message)
    return args[0]


def _reject_extra(args: list[str]) -> None:
    if args:
        raise ValueError(f"unexpected arguments: {' '.join(args)}")


def _require_system(session: LaunchAttachSession) -> None:
    if session.target.mode is not RuntimeMode.SYSTEM:
        raise ValueError("trade commands are only available for system attach")


def _duration(value: object) -> str:
    if value is None:
        return "-"
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {remainder}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def _kv_text(rows: Iterable[tuple[str, str]]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in rows)


def _short_path(value: str) -> str:
    if value == "-":
        return value
    parts = value.rsplit("/", 2)
    return "/".join(parts[-2:]) if len(parts) >= 2 else value


def _text(value: object) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def summary_text(status: Mapping[str, object]) -> str:
    view = AttachViewModel.from_status(status)
    return "\n".join(
        [
            f"{view.target_label}  phase={view.phase}  heartbeat={view.heartbeat_at}  age={view.heartbeat_age}",
            f"directory={view.directory}",
            f"log={view.log_file}",
        ]
    )


__all__ = ["AttachViewModel", "RuntimeAttachApp", "summary_text"]
