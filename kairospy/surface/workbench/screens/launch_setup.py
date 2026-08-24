"""Native Textual form for common Launch configuration fields."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Input, Label, Select
from textual.worker import Worker

from kairospy.system.apps.launch.application import LaunchConfigurationApplication
from kairospy.system.apps.launch.application.wizard import (
    LaunchDraft,
    build_and_validate,
    load_values,
)

from ..dialogs import ConfirmDialog
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
        account_scopes = _account_scopes(self.values)
        agent = _mapping(self.values.get("agent"))
        profile = _mapping(agent.get("profile"))
        model = _mapping(agent.get("model"))
        review = _mapping(_mapping(agent.get("capabilities")).get("intent_review"))
        notifications = _mapping(self.values.get("notifications"))
        notification_route = _first_route(notifications.get("routes"))

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
            yield Label("账户范围 JSON（按账户配置 segments / trade）")
            yield Input(
                value=json.dumps(account_scopes, ensure_ascii=False),
                id="launch-account-scopes",
            )

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

            yield Label("Agent")
            yield Checkbox(
                "启用 Agent",
                value=bool(agent.get("enabled", False)),
                id="agent-enabled",
            )
            with Vertical(id="agent-fields"):
                yield Checkbox(
                    "Agent 不可用时阻止 Launch",
                    value=bool(agent.get("required", False)),
                    id="agent-required",
                )
                yield Label("Agent goal")
                yield Input(
                    value=str(
                        profile.get("goal")
                        or "Review execution intents against bounded risk and supplied context"
                    ),
                    id="agent-goal",
                )
                yield Label("Agent Profile version")
                yield Input(
                    value=str(profile.get("version") or "1"),
                    id="agent-profile-version",
                )
                yield Label("Review rubric（逗号分隔）")
                yield Input(
                    value=",".join(
                        _strings(profile.get("rubric"))
                        or (
                            "Prefer bounded risk",
                            "Use fresh evidence",
                            "Abstain when evidence is insufficient",
                        )
                    ),
                    id="agent-rubric",
                )
                yield Label("Invalidation rules（逗号分隔）")
                yield Input(
                    value=",".join(
                        _strings(profile.get("invalidation_rules"))
                        or ("Abstain when required context is unavailable",)
                    ),
                    id="agent-invalidation",
                )
                yield Label("Allowed reason codes（逗号分隔，可留空）")
                yield Input(
                    value=",".join(_strings(profile.get("reason_codes"))),
                    id="agent-reason-codes",
                )
                yield Label("Allowed risk flags（逗号分隔，可留空）")
                yield Input(
                    value=",".join(_strings(profile.get("risk_flags"))),
                    id="agent-risk-flags",
                )
                yield Label("Intent review 初始模式")
                yield Select(
                    (("Shadow", "shadow"), ("Gate", "gate"), ("Revise", "revise")),
                    value=str(review.get("initial_mode") or "shadow"),
                    allow_blank=False,
                    id="agent-initial-mode",
                )
                yield Label("Strategy 可切换模式（逗号分隔）")
                yield Input(
                    value=",".join(
                        _strings(review.get("strategy_selectable_modes"))
                        or ("shadow", "gate", "revise")
                    ),
                    id="agent-selectable-modes",
                )
                yield Label("Agent 审核操作（逗号分隔）")
                yield Input(
                    value=",".join(
                        _strings(review.get("operations"))
                        or ("target_position",)
                    ),
                    id="agent-operations",
                )
                yield Label("必需 context keys（逗号分隔，可留空）")
                yield Input(
                    value=",".join(_strings(review.get("required_contexts"))),
                    id="agent-required-contexts",
                )
                yield Label("模型连接（paper/live）")
                yield Input(
                    value=str(model.get("connection") or ""),
                    id="agent-model-connection",
                )
                yield Label("固定模型 snapshot（paper/live）")
                yield Input(value=str(model.get("model") or ""), id="agent-model")
                yield Label("Fixture 文件（backtest）")
                yield Input(
                    value=str(agent.get("fixture_path") or "fixtures/agent.jsonl"),
                    id="agent-fixture",
                )
                yield Label("MCP servers JSON 数组")
                yield Input(
                    value=json.dumps(agent.get("mcp") or [], ensure_ascii=False),
                    id="agent-mcp",
                )

            yield Label("通知")
            yield Checkbox(
                "启用通知",
                value=bool(notifications.get("enabled", False)),
                id="notifications-enabled",
            )
            with Vertical(id="notification-fields"):
                yield Label("通知目标 ID")
                yield Input(value=notification_route, id="notification-destination")
                yield Checkbox(
                    "通知失败时阻止 Launch",
                    value=bool(notifications.get("required", False)),
                    id="notifications-required",
                )
                yield Checkbox(
                    "发送生命周期事件",
                    value=bool(notifications.get("lifecycle_routes", ["ops"])),
                    id="notifications-lifecycle",
                )

            yield Label(
                "其余高级字段会原样保留；资源必须先在“管理运行资源”中完成真实验证。",
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
        self._update_optional_fields()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "launch-mode" and event.value is not Select.NULL:
            self._update_mode_fields(str(event.value))

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "execution-enabled":
            self._update_execution_fields()
        elif event.checkbox.id in {"agent-enabled", "notifications-enabled"}:
            self._update_optional_fields()

    def _update_mode_fields(self, mode: str) -> None:
        self.query_one("#connected-fields", Vertical).display = mode != "backtest"
        self.query_one("#backtest-fields", Vertical).display = mode == "backtest"
        self.query_one("#live-fields", Vertical).display = mode == "live"

    def _update_execution_fields(self) -> None:
        self.query_one("#execution-fields", Vertical).display = self.query_one(
            "#execution-enabled", Checkbox
        ).value

    def _update_optional_fields(self) -> None:
        self.query_one("#agent-fields", Vertical).display = self.query_one(
            "#agent-enabled", Checkbox
        ).value
        self.query_one("#notification-fields", Vertical).display = self.query_one(
            "#notifications-enabled", Checkbox
        ).value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()
            return
        publish = event.button.id == "publish"
        if publish and not self.app.state.yes:  # type: ignore[attr-defined]
            self.app.push_screen(
                ConfirmDialog(
                    "发布 Launch 配置",
                    "将校验并替换这个 Launch 的已发布配置；现有实例不受影响。",
                    confirm_label="发布",
                ),
                lambda confirmed: self._submit(publish=True) if confirmed else None,
            )
            return
        self._submit(publish=publish)

    def _submit(self, *, publish: bool) -> None:
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
        account_scopes_value = self._input("launch-account-scopes") or "{}"
        try:
            raw_account_scopes = json.loads(account_scopes_value)
        except json.JSONDecodeError as error:
            raise ValueError(f"账户范围 JSON 无效：{error}") from error
        if not isinstance(raw_account_scopes, dict) or not all(
            isinstance(key, str) and isinstance(value, dict)
            for key, value in raw_account_scopes.items()
        ):
            raise ValueError("账户范围必须是 account ID 到 JSON object 的映射")
        unknown_scopes = sorted(set(raw_account_scopes) - set(accounts))
        if unknown_scopes:
            raise ValueError(
                "账户范围包含未选择账户：" + "、".join(unknown_scopes)
            )
        enabled = self.query_one("#execution-enabled", Checkbox).value
        draft = LaunchDraft(
            launch_id=self.launch_id,
            mode=mode,
            strategy=self._required_input("launch-strategy"),
            accounts=accounts,
            execution_enabled=enabled,
            account_scopes=raw_account_scopes,
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
        values["agent"] = self._agent_values(mode)
        values["notifications"] = self._notification_values(mode)
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

    def _agent_values(self, mode: str) -> dict[str, Any]:
        if not self.query_one("#agent-enabled", Checkbox).value:
            return {"enabled": False, "required": False}
        current = dict(_mapping(self.values.get("agent")))
        profile = dict(_mapping(current.get("profile")))
        profile.update(
            {
                "version": self._required_input("agent-profile-version"),
                "goal": self._required_input("agent-goal"),
                "rubric": list(_csv(self._required_input("agent-rubric"))),
                "invalidation_rules": list(
                    _csv(self._required_input("agent-invalidation"))
                ),
                "reason_codes": list(_csv(self._input("agent-reason-codes"))),
                "risk_flags": list(_csv(self._input("agent-risk-flags"))),
            }
        )
        try:
            mcp = json.loads(self._input("agent-mcp") or "[]")
        except json.JSONDecodeError as error:
            raise ValueError(f"MCP JSON 无效：{error}") from error
        if not isinstance(mcp, list) or not all(isinstance(item, dict) for item in mcp):
            raise ValueError("MCP servers 必须是 JSON object 数组")
        current.update(
            {
                "enabled": True,
                "required": self.query_one("#agent-required", Checkbox).value,
                "runtime": "fixture" if mode == "backtest" else "model-agent",
                "profile": profile,
                "mcp": mcp,
            }
        )
        capabilities = dict(_mapping(current.get("capabilities")))
        review = dict(_mapping(capabilities.get("intent_review")))
        review.update(
            {
                "initial_mode": (
                    "shadow" if mode != "backtest" else self._select("agent-initial-mode")
                ),
                "strategy_selectable_modes": list(
                    _csv(self._required_input("agent-selectable-modes"))
                ),
                "operations": list(
                    _csv(self._required_input("agent-operations"))
                ),
                "failure_policy": "reject_new_exposure",
                "required_contexts": list(
                    _csv(self._input("agent-required-contexts"))
                ),
                "revisions": dict(_mapping(review.get("revisions"))),
            }
        )
        capabilities["intent_review"] = review
        current["capabilities"] = capabilities
        if mode == "backtest":
            current.pop("model", None)
            current["fixture_path"] = self._required_input("agent-fixture")
        else:
            current.pop("fixture_path", None)
            current["model"] = {
                **dict(_mapping(current.get("model"))),
                "connection": self._required_input("agent-model-connection"),
                "model": self._required_input("agent-model"),
            }
        return current

    def _notification_values(self, mode: str) -> dict[str, Any]:
        if mode == "backtest" or not self.query_one(
            "#notifications-enabled", Checkbox
        ).value:
            return {"enabled": False, "required": False}
        destination = self._required_input("notification-destination")
        current = dict(_mapping(self.values.get("notifications")))
        current.update(
            {
                "enabled": True,
                "required": self.query_one(
                    "#notifications-required", Checkbox
                ).value,
                "routes": {"ops": [destination]},
                "default_routes": ["ops"],
                "lifecycle_routes": (
                    ["ops"]
                    if self.query_one("#notifications-lifecycle", Checkbox).value
                    else []
                ),
                "queue_capacity": int(current.get("queue_capacity", 256)),
                "shutdown_grace_seconds": float(
                    current.get("shutdown_grace_seconds", 5)
                ),
            }
        )
        return current

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


def _account_scopes(values: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    accounts = values.get("accounts")
    if not isinstance(accounts, Mapping):
        return {}
    return {
        str(value["ref"]): {
            str(key): item
            for key, item in value.items()
            if key not in {"ref", "enabled"}
        }
        for value in accounts.values()
        if isinstance(value, Mapping) and value.get("ref")
    }


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _csv(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


def _first_route(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    for route in value.values():
        if isinstance(route, list) and route:
            return str(route[0])
    return ""
