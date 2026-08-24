"""Native Textual form for common Launch configuration fields."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Input, Label, Select
from textual.worker import Worker

from kairospy.application.launch.application import LaunchConfigurationApplication
from kairospy.application.launch.application.wizard import (
    LaunchDraft,
    build_and_validate,
    load_values,
)

from ..widgets import WorkspaceHeader


class LaunchSetupScreen(Screen[dict[str, Any] | None]):
    """Edit the common Launch surface while retaining advanced TOML fields."""

    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(self, launch_id: str, source: Path | None = None) -> None:
        super().__init__()
        self.launch_id = launch_id
        self.source = source
        self.values = self._load_source(source)
        self.sub_title = f"首页 › 策略与运行 › {launch_id} › 配置"

    @staticmethod
    def _load_source(source: Path | None) -> dict[str, Any]:
        return load_values(source) if source is not None and source.is_file() else {}

    def compose(self) -> ComposeResult:
        launch = _mapping(self.values.get("launch"))
        mode = str(launch.get("mode") or "paper")
        execution = _mapping(self.values.get("execution"))
        risk = _mapping(self.values.get("risk"))
        live = _mapping(_mapping(self.values.get("live")).get("safety"))
        backtest = _mapping(_mapping(self.values.get("backtest")).get("market"))
        market = _mapping(_mapping(self.values.get(mode)).get("market"))
        route = _first_mapping(execution.get("routes"))
        accounts = _account_refs(self.values)

        yield WorkspaceHeader()
        yield Label(f"配置 Launch · {self.launch_id}", id="page-title")
        with VerticalScroll(id="launch-form"):
            yield Label("运行模式")
            yield Select(
                (("回测", "backtest"), ("模拟交易", "paper"), ("实盘", "live")),
                value=mode,
                allow_blank=False,
                id="launch-mode",
            )
            yield Label("策略引用")
            yield Input(
                value=str(launch.get("strategy") or "builtin:interactive"),
                id="launch-strategy",
            )
            yield Label("交易账户（逗号分隔）")
            yield Input(value=",".join(accounts), id="launch-accounts")

            with Vertical(id="connected-fields"):
                yield Label("Market 连接 Profile")
                yield Input(value=str(market.get("profile") or ""), id="market-profile")
                yield Label("Market 范围")
                yield Select(
                    (("Workspace 共享", "shared"), ("Launch Instance", "instance")),
                    value=str(market.get("scope") or "shared"),
                    allow_blank=False,
                    id="market-scope",
                )

            yield Checkbox(
                "启用 Execution",
                value=bool(execution.get("enabled", mode != "backtest")),
                id="execution-enabled",
            )
            with Vertical(id="execution-fields"):
                yield Label("Execution participant / broker")
                yield Input(
                    value=str(route.get("broker_id") or "simulated"),
                    id="execution-broker",
                )
                yield Label("Execution channel")
                yield Input(
                    value=str(route.get("execution_channel") or "spot"),
                    id="execution-channel",
                )
                yield Label("Account segment key")
                yield Input(
                    value=str(route.get("segment_key") or "spot"),
                    id="execution-segment",
                )

            with Vertical(id="backtest-fields"):
                yield Label("回测开始时间")
                yield Input(
                    value=str(backtest.get("start") or "2024-01-01T00:00:00Z"),
                    id="backtest-start",
                )
                yield Label("回测结束时间")
                yield Input(
                    value=str(backtest.get("end") or "2024-01-02T00:00:00Z"),
                    id="backtest-end",
                )
                yield Label("回放事件文件（可留空）")
                yield Input(value=str(backtest.get("events") or ""), id="backtest-events")

            with Vertical(id="live-fields"):
                yield Label("Risk Profile")
                yield Input(
                    value=str(risk.get("profile") or "production-default"),
                    id="risk-profile",
                )
                yield Checkbox(
                    "允许真实订单副作用",
                    value=bool(live.get("trading_enabled", False)),
                    id="live-trading",
                )
                yield Checkbox(
                    "强制仅允许限价单",
                    value=bool(live.get("require_limit_orders", True)),
                    id="live-limit-only",
                )
                yield Label("单笔最大名义金额")
                yield Input(
                    value=str(live.get("max_order_notional") or "1000"),
                    id="live-max-notional",
                )

            yield Label(
                "未在此表单展示的 Agent、通知、MCP 与高级字段会原样保留。",
                id="launch-form-help",
            )
            yield Label("", id="launch-form-error")
            with Horizontal(classes="form-actions"):
                yield Button("取消", id="cancel")
                yield Button("保存草稿", id="save-draft")
                yield Button("发布配置", id="publish", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self._update_mode_fields(self._select("launch-mode"))
        self._update_execution_fields()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "launch-mode" and event.value is not Select.NULL:
            self._update_mode_fields(str(event.value))

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "execution-enabled":
            self._update_execution_fields()

    def _update_mode_fields(self, mode: str) -> None:
        self.query_one("#connected-fields", Vertical).display = mode != "backtest"
        self.query_one("#backtest-fields", Vertical).display = mode == "backtest"
        self.query_one("#live-fields", Vertical).display = mode == "live"

    def _update_execution_fields(self) -> None:
        self.query_one("#execution-fields", Vertical).display = self.query_one(
            "#execution-enabled", Checkbox
        ).value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()
            return
        publish = event.button.id == "publish"
        state = self.app.state  # type: ignore[attr-defined]
        if state.dry_run or state.no_exec:
            self.query_one("#launch-form-error", Label).update(
                f"预览：{'发布配置' if publish else '保存草稿'}"
            )
            return
        self.query_one("#launch-form-error", Label).update("正在校验并保存…")
        self.query_one("#save-draft", Button).disabled = True
        self.query_one("#publish", Button).disabled = True
        self.run_worker(
            lambda: self._save(publish=publish),
            name="launch-publish" if publish else "launch-draft-save",
            group="launch-save",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _save(self, *, publish: bool) -> dict[str, Any]:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        mode = self._select("launch-mode")
        accounts = tuple(
            item.strip()
            for item in self._input("launch-accounts").split(",")
            if item.strip()
        )
        enabled = self.query_one("#execution-enabled", Checkbox).value
        draft = LaunchDraft(
            launch_id=self.launch_id,
            mode=mode,
            strategy=self._required_input("launch-strategy"),
            accounts=accounts,
            execution_enabled=enabled,
            market_profile=(
                self._input("market-profile") if mode != "backtest" else None
            ),
            market_scope=(self._select("market-scope") if mode != "backtest" else None),
            execution_broker_id=(self._input("execution-broker") if enabled else None),
            execution_channel=(self._input("execution-channel") if enabled else None),
            execution_segment_key=(
                self._input("execution-segment") if enabled else None
            ),
            risk_profile=(self._input("risk-profile") if mode == "live" else None),
            live_safety=(
                {
                    "trading_enabled": self.query_one("#live-trading", Checkbox).value,
                    "require_limit_orders": self.query_one(
                        "#live-limit-only", Checkbox
                    ).value,
                    "max_order_notional": self._required_input("live-max-notional"),
                }
                if mode == "live"
                else None
            ),
            backtest_start=(self._input("backtest-start") if mode == "backtest" else None),
            backtest_end=(self._input("backtest-end") if mode == "backtest" else None),
            backtest_events=(
                self._input("backtest-events") or None if mode == "backtest" else None
            ),
        )
        values = draft.apply(self.values)
        application = LaunchConfigurationApplication()
        status = application.save_draft(owner.paths.root, self.launch_id, values)
        if not publish:
            return status
        if not status["ready"]:
            raise ValueError("配置仍有阻塞项：" + "；".join(status["issues"]))
        destination = owner.paths.launch_config(self.launch_id)
        report = build_and_validate(destination, values, owner.paths.root)
        application.discard_draft(owner.paths.root, self.launch_id)
        return {"status": "published", "path": str(destination), **report}

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "launch-save":
            return
        self.query_one("#save-draft", Button).disabled = False
        self.query_one("#publish", Button).disabled = False
        if event.state.name == "ERROR":
            self.query_one("#launch-form-error", Label).update(
                f"保存失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            result = event.worker.result
            self.dismiss(dict(result) if isinstance(result, dict) else {})

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _input(self, id_: str) -> str:
        return self.query_one(f"#{id_}", Input).value.strip()

    def _required_input(self, id_: str) -> str:
        value = self._input(id_)
        if not value:
            raise ValueError("请填写所有必填项")
        return value

    def _select(self, id_: str) -> str:
        value = self.query_one(f"#{id_}", Select).value
        if value is Select.NULL:
            raise ValueError("请选择所有必填项")
        return str(value)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, list) and value and isinstance(value[0], Mapping):
        return value[0]
    return {}


def _account_refs(values: Mapping[str, Any]) -> tuple[str, ...]:
    accounts = values.get("accounts")
    if not isinstance(accounts, Mapping):
        return ()
    return tuple(
        str(value["ref"])
        for value in accounts.values()
        if isinstance(value, Mapping) and value.get("ref")
    )
