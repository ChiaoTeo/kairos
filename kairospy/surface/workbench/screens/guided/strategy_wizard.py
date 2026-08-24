"""Launch configuration wizard state and persistence helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairospy.system.apps.launch.application import LaunchConfigurationApplication
from kairospy.system.apps.launch.application.wizard import (
    LaunchDraft,
    build_and_validate,
    draft_preview,
    load_values,
)

from .strategy_support import config_path as _config_path
from .strategy_support import owner as _owner


@dataclass(slots=True)
class LaunchWizardState:
    """Transient, explicit Launch setup state for the one command input."""

    launch_id: str
    source: Path | None = None
    values: dict[str, Any] = field(default_factory=dict)
    answers: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def open(cls, launch_id: str, source: Path | None = None) -> LaunchWizardState:
        values = load_values(source) if source is not None and source.is_file() else {}
        return cls(launch_id=launch_id, source=source, values=values)

    def next_prompt(self) -> tuple[str, str, str] | None:
        for name in self._step_names():
            if name not in self.answers:
                default = self._default(name)
                suffix = f"；直接回车使用 {default}" if default != "" else ""
                return (
                    name,
                    _LAUNCH_PROMPTS[name],
                    f"{suffix}；输入 /back 取消整个向导。",
                )
        return None

    def accept(self, name: str, raw: str) -> None:
        value = raw.strip() or self._default(name)
        if name in {
            "execution-enabled",
            "live-trading",
            "live-limit-only",
            "agent-enabled",
            "agent-required",
            "notifications-enabled",
            "notifications-required",
            "notifications-lifecycle",
        }:
            self.answers[name] = _parse_bool(value)
            return
        if name == "mode" and value not in {"backtest", "paper", "live"}:
            raise ValueError("运行模式必须是 backtest、paper 或 live")
        if name == "market-scope" and value not in {"shared", "instance"}:
            raise ValueError("Market 范围必须是 shared 或 instance")
        if name == "accounts":
            self.answers[name] = tuple(
                item.strip() for item in value.split(",") if item.strip()
            )
            return
        if name == "account-scopes":
            try:
                scopes = json.loads(value or "{}")
            except json.JSONDecodeError as error:
                raise ValueError(f"账户范围 JSON 无效：{error}") from error
            if not isinstance(scopes, dict) or not all(
                isinstance(key, str) and isinstance(item, dict)
                for key, item in scopes.items()
            ):
                raise ValueError("账户范围必须是 account ID 到 JSON object 的映射")
            accounts = set(self.answers.get("accounts") or ())
            unknown = sorted(set(scopes) - accounts)
            if unknown:
                raise ValueError("账户范围包含未选择账户：" + "、".join(unknown))
            self.answers[name] = scopes
            return
        if name == "agent-mcp":
            try:
                mcp = json.loads(value or "[]")
            except json.JSONDecodeError as error:
                raise ValueError(f"MCP JSON 无效：{error}") from error
            if not isinstance(mcp, list) or not all(
                isinstance(item, dict) for item in mcp
            ):
                raise ValueError("MCP servers 必须是 JSON object 数组")
            self.answers[name] = mcp
            return
        if name in {
            "agent-rubric",
            "agent-invalidation",
            "agent-reason-codes",
            "agent-risk-flags",
            "agent-selectable-modes",
            "agent-operations",
            "agent-required-contexts",
        }:
            values = tuple(item.strip() for item in value.split(",") if item.strip())
            if (
                name
                in {
                    "agent-rubric",
                    "agent-invalidation",
                    "agent-selectable-modes",
                    "agent-operations",
                }
                and not values
            ):
                raise ValueError(f"{_LAUNCH_PROMPTS[name]}至少需要一项")
            self.answers[name] = values
            return
        if name == "agent-initial-mode":
            if value not in {"shadow", "gate", "revise"}:
                raise ValueError("Intent review 初始模式必须是 shadow、gate 或 revise")
            self.answers[name] = value
            return
        if name in {"strategy", "backtest-start", "backtest-end"} and not value:
            raise ValueError(f"{_LAUNCH_PROMPTS[name]}不能为空")
        self.answers[name] = value

    def build_values(self) -> dict[str, Any]:
        mode = str(self.answers["mode"])
        execution_enabled = bool(self.answers["execution-enabled"])
        draft = LaunchDraft(
            launch_id=self.launch_id,
            mode=mode,
            strategy=str(self.answers["strategy"]),
            accounts=tuple(self.answers["accounts"]),
            account_scopes=dict(self.answers["account-scopes"]),
            execution_enabled=execution_enabled,
            market_profile=(
                str(self.answers["market-profile"]) if mode != "backtest" else None
            ),
            market_scope=(
                str(self.answers["market-scope"]) if mode != "backtest" else None
            ),
            execution_broker_id=(
                str(self.answers["execution-broker"]) if execution_enabled else None
            ),
            execution_channel=(
                str(self.answers["execution-channel"]) if execution_enabled else None
            ),
            execution_segment_key=(
                str(self.answers["execution-segment"]) if execution_enabled else None
            ),
            risk_profile=(
                str(self.answers["risk-profile"]) if mode == "live" else None
            ),
            live_safety=(
                {
                    "trading_enabled": bool(self.answers["live-trading"]),
                    "require_limit_orders": bool(self.answers["live-limit-only"]),
                    "max_order_notional": str(self.answers["live-max-notional"]),
                }
                if mode == "live"
                else None
            ),
            backtest_start=(
                str(self.answers["backtest-start"]) if mode == "backtest" else None
            ),
            backtest_end=(
                str(self.answers["backtest-end"]) if mode == "backtest" else None
            ),
            backtest_events=(
                str(self.answers["backtest-events"]) or None
                if mode == "backtest"
                else None
            ),
        )
        values = draft.apply(self.values)
        values["agent"] = self._agent_values(mode)
        values["notifications"] = self._notification_values(mode)
        return values

    def preview(self) -> str:
        return draft_preview(self.build_values())

    def _step_names(self) -> tuple[str, ...]:
        mode = str(self.answers.get("mode") or self._default("mode"))
        execution = bool(self.answers.get("execution-enabled", True))
        agent = bool(self.answers.get("agent-enabled", False))
        notifications = bool(self.answers.get("notifications-enabled", False))
        steps = ["mode", "strategy", "accounts", "account-scopes"]
        if mode == "backtest":
            steps.extend(("backtest-start", "backtest-end", "backtest-events"))
        else:
            steps.extend(("market-profile", "market-scope"))
        steps.append("execution-enabled")
        if execution:
            steps.extend(("execution-broker", "execution-channel", "execution-segment"))
        if mode == "live":
            steps.extend(
                (
                    "risk-profile",
                    "live-trading",
                    "live-limit-only",
                    "live-max-notional",
                )
            )
        steps.append("agent-enabled")
        if agent:
            steps.extend(
                (
                    "agent-required",
                    "agent-goal",
                    "agent-profile-version",
                    "agent-rubric",
                    "agent-invalidation",
                    "agent-reason-codes",
                    "agent-risk-flags",
                    "agent-initial-mode",
                    "agent-selectable-modes",
                    "agent-operations",
                    "agent-required-contexts",
                )
            )
            steps.extend(
                ("agent-fixture",)
                if mode == "backtest"
                else ("agent-model-connection", "agent-model")
            )
            steps.append("agent-mcp")
        if mode != "backtest":
            steps.append("notifications-enabled")
            if notifications:
                steps.extend(
                    (
                        "notification-destination",
                        "notifications-required",
                        "notifications-lifecycle",
                    )
                )
        return tuple(steps)

    def _default(self, name: str) -> str:
        launch = _mapping(self.values.get("launch"))
        mode = str(self.answers.get("mode") or launch.get("mode") or "paper")
        execution = _mapping(self.values.get("execution"))
        route = _first_mapping(execution.get("routes"))
        accounts = _account_refs(self.values)
        scopes = _account_scopes(self.values)
        market = _mapping(_mapping(self.values.get(mode)).get("market"))
        backtest = _mapping(_mapping(self.values.get("backtest")).get("market"))
        live = _mapping(_mapping(self.values.get("live")).get("safety"))
        agent = _mapping(self.values.get("agent"))
        profile = _mapping(agent.get("profile"))
        model = _mapping(agent.get("model"))
        review = _mapping(_mapping(agent.get("capabilities")).get("intent_review"))
        notifications = _mapping(self.values.get("notifications"))
        routes = _mapping(notifications.get("routes"))
        destinations = next(
            (
                str(item)
                for selected in routes.values()
                if isinstance(selected, list)
                for item in selected
            ),
            "",
        )
        defaults: dict[str, object] = {
            "mode": mode,
            "strategy": launch.get("strategy") or "builtin:interactive",
            "accounts": ",".join(accounts),
            "account-scopes": json.dumps(scopes, ensure_ascii=False),
            "market-profile": market.get("profile") or "",
            "market-scope": market.get("scope") or "shared",
            "backtest-start": backtest.get("start") or "2024-01-01T00:00:00Z",
            "backtest-end": backtest.get("end") or "2024-01-02T00:00:00Z",
            "backtest-events": backtest.get("events") or "",
            "execution-enabled": _bool_text(
                execution.get("enabled", mode != "backtest")
            ),
            "execution-broker": route.get("broker_id") or "simulated",
            "execution-channel": route.get("execution_channel") or "spot",
            "execution-segment": route.get("segment_key") or "spot",
            "risk-profile": _mapping(self.values.get("risk")).get("profile")
            or "production-default",
            "live-trading": _bool_text(live.get("trading_enabled", False)),
            "live-limit-only": _bool_text(live.get("require_limit_orders", True)),
            "live-max-notional": live.get("max_order_notional") or "1000",
            "agent-enabled": _bool_text(agent.get("enabled", False)),
            "agent-required": _bool_text(agent.get("required", False)),
            "agent-goal": profile.get("goal")
            or "Review execution intents against bounded risk and supplied context",
            "agent-profile-version": profile.get("version") or "1",
            "agent-rubric": ",".join(
                _strings(profile.get("rubric"))
                or (
                    "Prefer bounded risk",
                    "Use fresh evidence",
                    "Abstain when evidence is insufficient",
                )
            ),
            "agent-invalidation": ",".join(
                _strings(profile.get("invalidation_rules"))
                or ("Abstain when required context is unavailable",)
            ),
            "agent-reason-codes": ",".join(_strings(profile.get("reason_codes"))),
            "agent-risk-flags": ",".join(_strings(profile.get("risk_flags"))),
            "agent-initial-mode": review.get("initial_mode") or "shadow",
            "agent-selectable-modes": ",".join(
                _strings(review.get("strategy_selectable_modes"))
                or ("shadow", "gate", "revise")
            ),
            "agent-operations": ",".join(
                _strings(review.get("operations")) or ("target_position",)
            ),
            "agent-required-contexts": ",".join(
                _strings(review.get("required_contexts"))
            ),
            "agent-model-connection": model.get("connection") or "",
            "agent-model": model.get("model") or "",
            "agent-fixture": agent.get("fixture_path") or "fixtures/agent.jsonl",
            "agent-mcp": json.dumps(agent.get("mcp") or [], ensure_ascii=False),
            "notifications-enabled": _bool_text(notifications.get("enabled", False)),
            "notification-destination": destinations,
            "notifications-required": _bool_text(notifications.get("required", False)),
            "notifications-lifecycle": _bool_text(
                bool(notifications.get("lifecycle_routes", ["ops"]))
            ),
        }
        return str(defaults[name])

    def _agent_values(self, mode: str) -> dict[str, Any]:
        if not self.answers["agent-enabled"]:
            return {"enabled": False, "required": False}
        current = dict(_mapping(self.values.get("agent")))
        profile = dict(_mapping(current.get("profile")))
        profile.update(
            {
                "version": str(self.answers["agent-profile-version"]),
                "goal": str(self.answers["agent-goal"]),
                "rubric": list(self.answers["agent-rubric"]),
                "invalidation_rules": list(self.answers["agent-invalidation"]),
                "reason_codes": list(self.answers["agent-reason-codes"]),
                "risk_flags": list(self.answers["agent-risk-flags"]),
            }
        )
        current.update(
            {
                "enabled": True,
                "required": bool(self.answers["agent-required"]),
                "runtime": "fixture" if mode == "backtest" else "model-agent",
                "profile": profile,
                "mcp": list(self.answers["agent-mcp"]),
            }
        )
        capabilities = dict(_mapping(current.get("capabilities")))
        review = dict(_mapping(capabilities.get("intent_review")))
        review.update(
            {
                "initial_mode": (
                    "shadow"
                    if mode != "backtest"
                    else self.answers["agent-initial-mode"]
                ),
                "strategy_selectable_modes": list(
                    self.answers["agent-selectable-modes"]
                ),
                "operations": list(self.answers["agent-operations"]),
                "failure_policy": "reject_new_exposure",
                "required_contexts": list(self.answers["agent-required-contexts"]),
                "revisions": dict(_mapping(review.get("revisions"))),
            }
        )
        capabilities["intent_review"] = review
        current["capabilities"] = capabilities
        if mode == "backtest":
            current.pop("model", None)
            current["fixture_path"] = str(self.answers["agent-fixture"])
        else:
            current.pop("fixture_path", None)
            current["model"] = {
                **dict(_mapping(current.get("model"))),
                "connection": str(self.answers["agent-model-connection"]),
                "model": str(self.answers["agent-model"]),
            }
        return current

    def _notification_values(self, mode: str) -> dict[str, Any]:
        if mode == "backtest" or not self.answers.get("notifications-enabled", False):
            return {"enabled": False, "required": False}
        current = dict(_mapping(self.values.get("notifications")))
        current.update(
            {
                "enabled": True,
                "required": bool(self.answers["notifications-required"]),
                "routes": {"ops": [str(self.answers["notification-destination"])]},
                "default_routes": ["ops"],
                "lifecycle_routes": (
                    ["ops"] if self.answers["notifications-lifecycle"] else []
                ),
                "queue_capacity": int(current.get("queue_capacity", 256)),
                "shutdown_grace_seconds": float(
                    current.get("shutdown_grace_seconds", 5)
                ),
            }
        )
        return current


def open_new_launch_wizard(state: Any, launch_id: str) -> LaunchWizardState:
    owner = _owner(state)
    launch_id = launch_id.strip()
    if not launch_id:
        raise ValueError("Launch ID 不能为空")
    if owner.paths.launch_config(launch_id).exists():
        raise ValueError("同名 Launch 配置已经存在")
    draft = LaunchConfigurationApplication().draft_path(owner.paths.root, launch_id)
    return LaunchWizardState.open(launch_id, draft if draft.is_file() else None)


def open_edit_launch_wizard(state: Any, record: Mapping[str, Any]) -> LaunchWizardState:
    owner = _owner(state)
    launch_id = str(record["launch_id"])
    configured = record.get("config")
    source = (
        Path(str(configured)) if configured else owner.paths.launch_config(launch_id)
    )
    if not source.is_file():
        draft = LaunchConfigurationApplication().draft_path(owner.paths.root, launch_id)
        source = draft if draft.is_file() else None
    return LaunchWizardState.open(launch_id, source)


def save_launch_wizard(
    state: Any, wizard: LaunchWizardState, *, publish: bool
) -> dict[str, Any]:
    owner = _owner(state)
    values = wizard.build_values()
    application = LaunchConfigurationApplication()
    status = application.save_draft(owner.paths.root, wizard.launch_id, values)
    if not publish:
        return status
    if not status["ready"]:
        raise ValueError("配置仍有阻塞项：" + "；".join(status["issues"]))
    destination = owner.paths.launch_config(wizard.launch_id)
    report = build_and_validate(destination, values, owner.paths.root)
    application.discard_draft(owner.paths.root, wizard.launch_id)
    return {"status": "published", "path": str(destination), **report}


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, list):
        return {}
    return next((item for item in value if isinstance(item, Mapping)), {})


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item))


def _account_refs(values: Mapping[str, Any]) -> tuple[str, ...]:
    accounts = _mapping(values.get("accounts"))
    return tuple(
        str(item["ref"])
        for item in accounts.values()
        if isinstance(item, Mapping) and item.get("ref")
    )


def _account_scopes(values: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    accounts = _mapping(values.get("accounts"))
    scopes: dict[str, dict[str, Any]] = {}
    for item in accounts.values():
        if not isinstance(item, Mapping) or not item.get("ref"):
            continue
        scopes[str(item["ref"])] = {
            key: value for key, value in item.items() if key not in {"ref", "enabled"}
        }
    return scopes


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "是", "启用"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "否", "停用"}:
        return False
    raise ValueError("请输入 yes/no、true/false 或 1/0")


def _bool_text(value: object) -> str:
    return "yes" if bool(value) else "no"


_LAUNCH_PROMPTS = {
    "mode": "运行模式（backtest / paper / live）",
    "strategy": "Strategy 引用",
    "accounts": "交易账户 ID（逗号分隔，可留空）",
    "account-scopes": "账户范围 JSON object",
    "market-profile": "Market 连接 Profile",
    "market-scope": "Market 范围（shared / instance）",
    "backtest-start": "回测开始时间",
    "backtest-end": "回测结束时间",
    "backtest-events": "回放事件文件（可留空）",
    "execution-enabled": "是否启用 Execution（yes / no）",
    "execution-broker": "Execution participant / broker",
    "execution-channel": "Execution channel",
    "execution-segment": "Account segment key",
    "risk-profile": "Risk Profile",
    "live-trading": "是否允许真实订单副作用（yes / no）",
    "live-limit-only": "是否强制仅允许限价单（yes / no）",
    "live-max-notional": "单笔最大名义金额",
    "agent-enabled": "是否启用 Agent（yes / no）",
    "agent-required": "Agent 不可用时是否阻止 Launch（yes / no）",
    "agent-goal": "Agent goal",
    "agent-profile-version": "Agent Profile version",
    "agent-rubric": "Review rubric（逗号分隔）",
    "agent-invalidation": "Invalidation rules（逗号分隔）",
    "agent-reason-codes": "Allowed reason codes（逗号分隔，可留空）",
    "agent-risk-flags": "Allowed risk flags（逗号分隔，可留空）",
    "agent-initial-mode": "Intent review 初始模式（shadow / gate / revise）",
    "agent-selectable-modes": "Strategy 可切换模式（逗号分隔）",
    "agent-operations": "Agent 审核操作（逗号分隔）",
    "agent-required-contexts": "必需 context keys（逗号分隔，可留空）",
    "agent-model-connection": "模型连接",
    "agent-model": "固定模型 snapshot",
    "agent-fixture": "Agent Fixture 文件",
    "agent-mcp": "MCP servers JSON 数组",
    "notifications-enabled": "是否启用通知（yes / no）",
    "notification-destination": "通知目标 ID",
    "notifications-required": "通知失败时是否阻止 Launch（yes / no）",
    "notifications-lifecycle": "是否发送生命周期事件（yes / no）",
}


__all__ = [
    "LaunchWizardState",
    "open_edit_launch_wizard",
    "open_new_launch_wizard",
    "save_launch_wizard",
]
