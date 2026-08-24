"""Interactive launch configuration drafting and persistence."""

from __future__ import annotations

import os
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import typer

from ...agent import AgentResourceApplication
from ...account import AccountConfigurationApplication
from ...notification import NotificationAdminApplication
from ...reference import ReferenceProviderConfigurationApplication
from ...workspace import Workspace
from .configuration import LaunchConfigError, LaunchConfigurationApplication


class LaunchWizardExit(Exception):
    """Raised when q is entered at any text prompt in the Launch wizard."""


class LaunchResourceSetup(Exception):
    """Stop at a missing Workspace resource while retaining the working draft."""

    def __init__(self, resource: str, step: str) -> None:
        super().__init__(resource)
        self.resource = resource
        self.step = step


def _wizard_prompt(*args: Any, **kwargs: Any) -> Any:
    value = typer.prompt(*args, **kwargs)
    if isinstance(value, str) and value.strip().lower() == "q":
        raise LaunchWizardExit
    return value


@dataclass(frozen=True, slots=True)
class LaunchDraft:
    """Small user-facing subset of launch configuration.

    The wizard deliberately owns common fields only. Existing advanced fields
    are retained when editing an existing TOML document.
    """

    launch_id: str
    mode: str
    strategy: str
    accounts: tuple[str, ...]
    execution_enabled: bool
    account_scopes: Mapping[str, Mapping[str, Any]] | None = None
    market_profile: str | None = None
    market_scope: str | None = None
    execution_broker_id: str | None = None
    execution_channel: str | None = None
    execution_segment_key: str | None = None
    risk_profile: str | None = None
    live_safety: Mapping[str, Any] | None = None
    backtest_start: str | None = None
    backtest_end: str | None = None
    backtest_events: str | None = None
    agent: Mapping[str, Any] | None = None
    notifications: Mapping[str, Any] | None = None

    def apply(self, values: Mapping[str, Any]) -> dict[str, Any]:
        result = _copy_mapping(values)
        launch = _table(result, "launch")
        launch.update(
            {"id": self.launch_id, "mode": self.mode, "strategy": self.strategy}
        )

        result.pop("account", None)
        result["accounts"] = (
            {
                f"account_{index + 1}": {
                    "ref": account,
                    "enabled": True,
                    **_copy_mapping(self.account_scopes.get(account, {})),
                }
                for index, account in enumerate(self.accounts)
            }
            if self.account_scopes is not None
            else {
                f"account_{index + 1}": {"ref": account, "enabled": True}
                for index, account in enumerate(self.accounts)
            }
        )
        execution = _table(result, "execution")
        execution["enabled"] = self.execution_enabled
        if self.execution_broker_id and not execution.get("routes"):
            route_accounts = self.accounts or ("main",)
            channel = self.execution_channel or "spot"
            segment_key = self.execution_segment_key or channel
            execution["routes"] = [
                {
                    "route_id": f"{account}-{segment_key}",
                    "account_id": account,
                    "segment_key": segment_key,
                    "broker_id": self.execution_broker_id,
                    "execution_channel": channel,
                }
                for account in route_accounts
            ]
        if self.risk_profile:
            _table(result, "risk")["profile"] = self.risk_profile
        if self.mode == "live" and self.live_safety is not None:
            _table(_table(result, "live"), "safety").update(
                _copy_mapping(self.live_safety)
            )

        if self.mode == "backtest":
            backtest = _table(result, "backtest")
            market = _table(backtest, "market")
            if self.backtest_start:
                market["start"] = self.backtest_start
            if self.backtest_end:
                market["end"] = self.backtest_end
            if self.backtest_events:
                market["events"] = self.backtest_events
        elif self.market_profile:
            market = _table(_table(result, self.mode), "market")
            market["profile"] = self.market_profile
            market["scope"] = self.market_scope or "shared"
        if self.agent is not None:
            result["agent"] = _copy_mapping(self.agent)
        if self.notifications is not None:
            result["notifications"] = _copy_mapping(self.notifications)
        return result


def load_values(path: Path) -> dict[str, Any]:
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise LaunchConfigError(
            f"unable to read launch config {path}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise LaunchConfigError(f"launch config root must be a TOML table: {path}")
    return value


def prompt_draft(
    values: Mapping[str, Any] | None = None,
    *,
    default_launch_id: str = "new-launch",
    workspace: Workspace | None = None,
    on_step: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> LaunchDraft:
    current = values or {}
    working = _copy_mapping(current)
    launch_value = working.get("launch")
    launch: Mapping[str, Any] = (
        launch_value if isinstance(launch_value, Mapping) else {}
    )
    mode = _prompt_choice(
        "运行模式", str(launch.get("mode", "paper")), ("backtest", "paper", "live")
    )
    strategy = str(launch.get("strategy") or "builtin:interactive")
    strategy = _wizard_prompt("策略引用", default=strategy)
    working_launch = _table(working, "launch")
    working_launch.update(
        {
            "id": str(launch.get("id") or default_launch_id),
            "mode": mode,
            "strategy": strategy,
        }
    )
    _checkpoint(on_step, "mode_and_strategy", working)

    account_refs, account_scopes = _prompt_account_scopes(
        working, mode=mode, workspace=workspace
    )
    working.pop("account", None)
    working["accounts"] = {
        f"account_{index + 1}": {
            "ref": account,
            "enabled": True,
            **_copy_mapping(account_scopes.get(account, {})),
        }
        for index, account in enumerate(account_refs)
    }
    _checkpoint(on_step, "accounts_and_execution_scope", working)
    market_profile, market_scope = _prompt_market_profile(
        working, mode=mode, workspace=workspace
    )
    if mode != "backtest":
        market_config = _table(_table(working, mode), "market")
        if market_profile:
            market_config.update(
                {"profile": market_profile, "scope": market_scope or "shared"}
            )
        else:
            market_config.pop("profile", None)
            market_config.pop("scope", None)
    _checkpoint(on_step, "market_and_data", working)

    execution_value = working.get("execution")
    execution: Mapping[str, Any] = (
        execution_value if isinstance(execution_value, Mapping) else {}
    )
    execution_enabled = (
        False
        if mode == "backtest" and not account_refs
        else typer.confirm(
            "启用 Execution",
            default=bool(execution.get("enabled", True)) and bool(account_refs),
        )
    )
    routes = execution.get("routes")
    first_route = routes[0] if isinstance(routes, list) and routes else {}
    participant_default = str(
        first_route.get("broker_id", "simulated")
        if isinstance(first_route, Mapping)
        else "simulated"
    )
    broker_id = (
        _wizard_prompt("Execution participant", default=participant_default)
        if execution_enabled
        else None
    )
    channel = (
        _wizard_prompt(
            "Execution channel",
            default=str(
                first_route.get("execution_channel", "spot")
                if isinstance(first_route, Mapping)
                else "spot"
            ),
        )
        if execution_enabled
        else None
    )
    segment_key = (
        _wizard_prompt(
            "Account segment key",
            default=str(
                first_route.get("segment_key", channel or "spot")
                if isinstance(first_route, Mapping)
                else channel or "spot"
            ),
        )
        if execution_enabled
        else None
    )
    risk_value = working.get("risk")
    risk: Mapping[str, Any] = risk_value if isinstance(risk_value, Mapping) else {}
    risk_profile = (
        _wizard_prompt(
            "Risk profile",
            default=str(risk.get("profile") or "production-default"),
        )
        if mode == "live"
        else (
            str(risk["profile"])
            if isinstance(risk.get("profile"), str) and risk.get("profile")
            else None
        )
    )
    live_safety: dict[str, Any] | None = None
    if mode == "live":
        live_value = working.get("live")
        current_live = live_value if isinstance(live_value, Mapping) else {}
        safety_value = current_live.get("safety")
        current_safety = safety_value if isinstance(safety_value, Mapping) else {}
        trading_enabled = typer.confirm(
            "允许此 Launch 产生真实订单副作用",
            default=bool(current_safety.get("trading_enabled", False)),
        )
        require_limit_orders = typer.confirm(
            "强制仅允许限价单",
            default=bool(current_safety.get("require_limit_orders", True)),
        )
        max_notional = (
            _wizard_prompt(
                "单笔最大名义金额",
                default=str(current_safety.get("max_order_notional") or "1000"),
            ).strip()
            if trading_enabled
            else None
        )
        live_safety = {
            "trading_enabled": trading_enabled,
            "require_limit_orders": require_limit_orders,
            **(
                {"max_order_notional": max_notional} if max_notional is not None else {}
            ),
        }

    backtest_value = working.get("backtest")
    backtest: Mapping[str, Any] = (
        backtest_value if isinstance(backtest_value, Mapping) else {}
    )
    market_value = backtest.get("market")
    market: Mapping[str, Any] = (
        market_value if isinstance(market_value, Mapping) else {}
    )
    start = end = events = None
    if mode == "backtest":
        start = _wizard_prompt(
            "回测开始时间", default=str(market.get("start", "2024-01-01T00:00:00Z"))
        )
        end = _wizard_prompt(
            "回测结束时间", default=str(market.get("end", "2024-01-02T00:00:00Z"))
        )
        events = (
            _wizard_prompt(
                "回放事件文件（可留空）", default=str(market.get("events", ""))
            )
            or None
        )

    working_execution = _table(working, "execution")
    working_execution["enabled"] = execution_enabled
    if broker_id and not working_execution.get("routes"):
        route_accounts = account_refs or ("main",)
        route_channel = channel or "spot"
        route_segment = segment_key or route_channel
        working_execution["routes"] = [
            {
                "route_id": f"{account}-{route_segment}",
                "account_id": account,
                "segment_key": route_segment,
                "broker_id": broker_id,
                "execution_channel": route_channel,
            }
            for account in route_accounts
        ]
    if risk_profile:
        _table(working, "risk")["profile"] = risk_profile
    if live_safety is not None:
        _table(_table(working, "live"), "safety").update(live_safety)
    if mode == "backtest":
        working_market = _table(_table(working, "backtest"), "market")
        if start:
            working_market["start"] = start
        if end:
            working_market["end"] = end
        if events:
            working_market["events"] = events
        else:
            working_market.pop("events", None)
    _checkpoint(on_step, "execution_and_risk", working)

    agent = prompt_agent_config(working, mode=mode, workspace=workspace)
    working["agent"] = _copy_mapping(agent)
    _checkpoint(on_step, "agent", working)
    notifications = prompt_notification_config(working, mode=mode, workspace=workspace)
    working["notifications"] = _copy_mapping(notifications)
    _checkpoint(on_step, "notifications", working)

    return LaunchDraft(
        launch_id=str(launch.get("id") or default_launch_id),
        mode=mode,
        strategy=strategy,
        accounts=account_refs,
        execution_enabled=execution_enabled,
        account_scopes=account_scopes,
        market_profile=market_profile,
        market_scope=market_scope,
        execution_broker_id=broker_id,
        execution_channel=channel,
        execution_segment_key=segment_key,
        risk_profile=risk_profile,
        live_safety=live_safety,
        backtest_start=start,
        backtest_end=end,
        backtest_events=events,
        agent=agent,
        notifications=notifications,
    )


def _checkpoint(
    on_step: Callable[[str, Mapping[str, Any]], None] | None,
    step: str,
    values: Mapping[str, Any],
) -> None:
    """Persist one completed product step without exposing mutable wizard state."""

    if on_step is not None:
        on_step(step, _copy_mapping(values))


def _prompt_account_scopes(
    values: Mapping[str, Any], *, mode: str, workspace: Workspace | None
) -> tuple[tuple[str, ...], dict[str, dict[str, Any]]]:
    if mode == "backtest":
        return (), {}
    if workspace is None:
        raise typer.BadParameter("paper/live Launch requires a Workspace")
    application = AccountConfigurationApplication(workspace)
    accounts = application.list()
    verified = {
        str(value["account_id"]): value
        for value in accounts
        if value.get("verification_status") == "verified"
        and (
            str(value.get("environment") or "").lower() in {"live", "testnet"}
            if mode == "live"
            else str(value.get("environment") or "").lower()
            in {"paper", "simulated", "simulation", "sandbox", "testnet"}
        )
    }
    unavailable = [
        f"{value.get('account_id')}({_verification_label(str(value.get('verification_status') or 'pending'))})"
        for value in accounts
        if str(value.get("account_id")) not in verified
    ]
    if unavailable:
        typer.echo("以下账户当前不可选：" + ", ".join(unavailable))
    if not verified:
        typer.echo("当前没有符合模式且已手动验证的账户。")
        typer.echo(
            "  1. 保存 Launch 草稿，前往配置交易账户\n"
            "  2. 明确以无账户、无 Execution 方案继续"
        )
        action = _prompt_choice(
            "下一步",
            "1",
            ("1", "2"),
        )
        if action == "1":
            raise LaunchResourceSetup("accounts", "accounts_and_execution_scope")
        typer.echo("已明确选择：本 Launch 不使用交易账户或 Execution。")
        return (), {}
    defaults = tuple(value for value in _account_defaults(values) if value in verified)
    typer.echo("可选择的已验证账户：" + ", ".join(verified))
    text = _wizard_prompt(
        "账户引用（多个账户用逗号分隔，可留空）", default=", ".join(defaults)
    )
    selected = tuple(
        dict.fromkeys(item.strip() for item in text.split(",") if item.strip())
    )
    unknown = [value for value in selected if value not in verified]
    if unknown:
        raise typer.BadParameter(
            "Launch 只能选择符合当前模式且已验证的账户：" + ", ".join(unknown)
        )
    current_accounts = values.get("accounts")
    current_by_ref = {
        str(item.get("ref")): item
        for item in (
            current_accounts.values() if isinstance(current_accounts, Mapping) else ()
        )
        if isinstance(item, Mapping) and item.get("ref")
    }
    scopes: dict[str, dict[str, Any]] = {}
    for account_id in selected:
        account = verified[account_id]
        previous = current_by_ref.get(account_id, {})
        available_segments = tuple(
            str(value) for value in account.get("segments") or () if str(value)
        )
        previous_segments = tuple(
            str(value) for value in previous.get("segments") or () if str(value)
        )
        segment_text = _wizard_prompt(
            f"{account_id} segment scope",
            default=", ".join(previous_segments or available_segments),
        ).strip()
        segments = tuple(
            dict.fromkeys(
                value.strip() for value in segment_text.split(",") if value.strip()
            )
        )
        invalid = sorted(set(segments) - set(available_segments))
        if invalid:
            raise typer.BadParameter(
                f"{account_id} 未验证这些 segment：{', '.join(invalid)}"
            )
        capabilities = {str(value) for value in account.get("capabilities") or ()}
        trade = typer.confirm(
            f"允许 {account_id} 交易",
            default=bool(previous.get("trade", mode == "live")),
        )
        if trade and "trade" not in capabilities:
            raise typer.BadParameter(f"{account_id} 的手动测试未验证 trade 权限")
        scopes[account_id] = {"trade": trade, "segments": list(segments)}
    return selected, scopes


def _prompt_market_profile(
    values: Mapping[str, Any], *, mode: str, workspace: Workspace | None
) -> tuple[str | None, str | None]:
    if mode == "backtest":
        return None, None
    mode_value = values.get(mode)
    current_market = (
        mode_value.get("market") if isinstance(mode_value, Mapping) else None
    )
    current_profile = (
        str(current_market.get("profile"))
        if isinstance(current_market, Mapping) and current_market.get("profile")
        else "workspace-default"
    )
    choices = ["workspace-default"]
    if workspace is not None:
        connections = ReferenceProviderConfigurationApplication(workspace).list()
        if any(item.get("verification_status") == "verified" for item in connections):
            choices.append("massive")
    if current_profile not in choices:
        typer.echo(f"当前数据 profile {current_profile} 不可选，因为对应资源尚未验证。")
        current_profile = choices[0]
    selected = _prompt_choice("市场与数据模式", current_profile, tuple(choices))
    if selected == "workspace-default":
        return None, None
    return selected, "shared"


def prompt_notification_config(
    values: Mapping[str, Any], *, mode: str, workspace: Workspace | None
) -> dict[str, Any]:
    current_value = values.get("notifications")
    current: Mapping[str, Any] = (
        current_value if isinstance(current_value, Mapping) else {}
    )
    if mode == "backtest":
        return {"enabled": False, "required": False}
    enabled = typer.confirm("启用通知", default=bool(current.get("enabled", False)))
    if not enabled:
        return {"enabled": False, "required": False}
    if workspace is None:
        raise typer.BadParameter("Notification selection requires a Workspace")
    destinations = [
        item
        for item in NotificationAdminApplication(workspace).list()
        if item.get("verification_status") == "verified" and item.get("enabled", True)
    ]
    if not destinations:
        typer.echo(
            "当前没有已发送真实测试消息且验证成功的通知渠道；"
            "将保留通知启用意图并把草稿标记为需要处理。"
        )
        return {"enabled": True, "required": False, "routes": {}}
    destination_ids = tuple(str(item["destination_id"]) for item in destinations)
    routes = current.get("routes")
    current_destinations = next(
        (
            tuple(str(item) for item in route)
            for route in (routes.values() if isinstance(routes, Mapping) else ())
            if isinstance(route, list) and route
        ),
        (),
    )
    typer.echo("可选择的已验证通知渠道：" + ", ".join(destination_ids))
    selected = _wizard_prompt(
        "通知渠道 id",
        default=current_destinations[0]
        if current_destinations[0:1]
        else destination_ids[0],
    ).strip()
    if selected not in destination_ids:
        raise typer.BadParameter(f"通知渠道尚未手动验证：{selected}")
    required = typer.confirm(
        "通知失败时阻止 Launch", default=bool(current.get("required", False))
    )
    lifecycle = typer.confirm(
        "发送生命周期事件", default=bool(current.get("lifecycle_routes", ["ops"]))
    )
    return {
        "enabled": True,
        "required": required,
        "routes": {"ops": [selected]},
        "default_routes": ["ops"],
        "lifecycle_routes": ["ops"] if lifecycle else [],
        "queue_capacity": int(current.get("queue_capacity", 256)),
        "shutdown_grace_seconds": float(current.get("shutdown_grace_seconds", 5)),
    }


def _verification_label(status: str) -> str:
    return {
        "verified": "已验证",
        "pending": "待测试",
        "retest_required": "需重新测试",
        "failed": "测试失败",
    }.get(status, status)


def prompt_agent_config(
    values: Mapping[str, Any], *, mode: str, workspace: Workspace | None
) -> dict[str, Any]:
    current_value = values.get("agent")
    current: Mapping[str, Any] = (
        current_value if isinstance(current_value, Mapping) else {}
    )
    current = _expand_legacy_agent_resources(current, workspace)
    enabled = typer.confirm("启用 Agent", default=bool(current.get("enabled", False)))
    if not enabled:
        return {"enabled": False, "required": False}
    profile_value = current.get("profile")
    profile: Mapping[str, Any] = (
        profile_value if isinstance(profile_value, Mapping) else {}
    )
    profile_config = {
        "version": _wizard_prompt(
            "Agent Profile version", default=str(profile.get("version") or "1")
        ).strip(),
        "goal": _wizard_prompt(
            "Agent goal",
            default=str(
                profile.get("goal")
                or "Review execution intents against bounded risk and supplied context"
            ),
        ).strip(),
        "rubric": list(
            _prompt_csv(
                "Review rubric",
                _string_items(profile.get("rubric"))
                or (
                    "Prefer bounded risk",
                    "Use fresh evidence",
                    "Abstain when evidence is insufficient",
                ),
            )
        ),
        "invalidation_rules": list(
            _prompt_csv(
                "Invalidation rules",
                _string_items(profile.get("invalidation_rules"))
                or ("Abstain when required context is unavailable",),
            )
        ),
        "reason_codes": list(
            _prompt_csv(
                "Allowed reason codes（可留空）",
                _string_items(profile.get("reason_codes")),
            )
        ),
        "risk_flags": list(
            _prompt_csv(
                "Allowed risk flags（可留空）",
                _string_items(profile.get("risk_flags")),
            )
        ),
    }

    required = typer.confirm(
        "Agent 不可用时阻止 Launch", default=bool(current.get("required", False))
    )
    agent = _copy_mapping(current)
    agent.update(
        {
            "enabled": True,
            "required": required,
            "runtime": "fixture" if mode == "backtest" else "model-agent",
            "profile": profile_config,
        }
    )
    if mode == "backtest":
        agent.pop("model", None)
        agent["fixture_path"] = _wizard_prompt(
            "Agent fixture path",
            default=str(current.get("fixture_path") or "fixtures/agent.jsonl"),
        ).strip()
    else:
        if workspace is None:
            raise typer.BadParameter("Agent requires a Workspace AI model connection")
        agent.pop("fixture_path", None)
        resources = AgentResourceApplication(workspace)
        connections = tuple(
            item
            for item in resources.model_connections()
            if item.get("verification_status") == "verified"
        )
        if not connections:
            typer.echo(
                "当前没有可用的 AI 模型连接；"
                "将保留 Agent 启用意图并把草稿标记为需要处理。"
            )
            agent.pop("model", None)
            return agent
        model_value = current.get("model")
        model: Mapping[str, Any] = (
            model_value if isinstance(model_value, Mapping) else {}
        )
        by_id = {str(item["connection_id"]): item for item in connections}
        connection_ids = tuple(by_id)
        current_connection = model.get("connection", model.get("credential"))
        typer.echo("可用模型连接：")
        for index, connection_id in enumerate(connection_ids, start=1):
            item = by_id[connection_id]
            typer.echo(
                f"  {index}. {connection_id} · "
                f"{item.get('provider_label') or item.get('provider')}"
            )
        connection = _wizard_prompt(
            f"模型连接（{', '.join(connection_ids)}）",
            default=(
                str(current_connection)
                if isinstance(current_connection, str)
                and current_connection in connection_ids
                else connection_ids[0]
            ),
        ).strip()
        if connection not in connection_ids:
            raise typer.BadParameter(
                f"AI model connection is not available: {connection}"
            )
        current_model = model.get("model")
        verified_model = by_id[connection].get("model")
        model_id = (
            _wizard_prompt("固定模型 snapshot", default=str(current_model)).strip()
            if isinstance(current_model, str) and current_model.strip()
            else _wizard_prompt(
                "固定模型 snapshot", default=str(verified_model or "")
            ).strip()
        )
        if (
            resources.model_verification(connection, model=model_id).get(
                "verification_status"
            )
            != "verified"
        ):
            raise typer.BadParameter(
                f"AI model {connection}/{model_id} has not been manually tested"
            )
        model_config = _copy_mapping(model)
        model_config.pop("provider", None)
        model_config.pop("credential", None)
        model_config.update(
            {
                "connection": connection,
                "model": model_id,
            }
        )
        agent["model"] = model_config

    capabilities_value = current.get("capabilities")
    capabilities: Mapping[str, Any] = (
        capabilities_value if isinstance(capabilities_value, Mapping) else {}
    )
    review_value = capabilities.get("intent_review")
    review: Mapping[str, Any] = (
        review_value if isinstance(review_value, Mapping) else {}
    )
    initial_mode = (
        "shadow"
        if mode != "backtest"
        else _prompt_choice(
            "Agent 初始模式",
            str(review.get("initial_mode") or "shadow"),
            ("shadow", "gate", "revise"),
        )
    )
    selectable = _prompt_csv(
        "Strategy 可切换模式",
        _string_items(review.get("strategy_selectable_modes"))
        or ("shadow", "gate", "revise"),
    )
    operations = _prompt_csv(
        "Agent 审核操作",
        _string_items(review.get("operations")) or ("target_position",),
    )
    required_contexts = _prompt_csv(
        "必需 context keys（可留空）",
        _string_items(review.get("required_contexts")),
    )
    revisions_value = review.get("revisions")
    capabilities_config = _copy_mapping(capabilities)
    review_config = _copy_mapping(review)
    review_config.update(
        {
            "initial_mode": initial_mode,
            "strategy_selectable_modes": list(selectable),
            "operations": list(operations),
            "failure_policy": "reject_new_exposure",
            "required_contexts": list(required_contexts),
            "revisions": (
                _copy_mapping(revisions_value)
                if isinstance(revisions_value, Mapping)
                else {}
            ),
        }
    )
    capabilities_config["intent_review"] = review_config
    agent["capabilities"] = capabilities_config
    agent["mcp"] = _prompt_inline_mcp(current.get("mcp"))
    typer.echo("Agent 有效访问范围：" + _effective_agent_scope(values))
    return agent


def _prompt_inline_mcp(current: object) -> list[dict[str, Any]]:
    current_items = current if isinstance(current, (list, tuple)) else ()
    count = int(
        _wizard_prompt("MCP server 数量", default=str(len(current_items))).strip()
        or "0"
    )
    if count < 0 or count > 16:
        raise typer.BadParameter("MCP server 数量必须在 0 到 16 之间")
    if not count:
        return []
    result: list[dict[str, Any]] = []
    for index in range(count):
        previous = current_items[index] if index < len(current_items) else {}
        previous = previous if isinstance(previous, Mapping) else {}
        server_id = _wizard_prompt(
            f"MCP {index + 1} id", default=str(previous.get("id") or "kairos-context")
        ).strip()
        transport = _prompt_choice(
            f"MCP {index + 1} transport",
            str(previous.get("transport") or "stdio"),
            ("stdio", "streamable_http"),
        )
        entry: dict[str, Any] = {
            "id": server_id,
            "transport": transport,
            "allowed_tools": list(
                _prompt_csv(
                    f"MCP {index + 1} allowed tools",
                    _string_items(previous.get("allowed_tools"))
                    or ("account.get_position", "risk.get_effective_limits"),
                )
            ),
            "scope_enforced": True,
            "max_result_bytes": int(previous.get("max_result_bytes", 65_536)),
            "max_rows": int(previous.get("max_rows", 200)),
            "timeout_seconds": float(previous.get("timeout_seconds", 5)),
            "required": typer.confirm(
                f"MCP {index + 1} 不可用时阻止 Launch",
                default=bool(previous.get("required", False)),
            ),
        }
        if transport == "stdio":
            entry["command"] = _wizard_prompt(
                f"MCP {index + 1} command", default=str(previous.get("command") or "")
            ).strip()
            entry["args"] = list(
                _prompt_csv(
                    f"MCP {index + 1} arguments（可留空）",
                    _string_items(previous.get("args")),
                )
            )
        else:
            entry["url"] = _wizard_prompt(
                f"MCP {index + 1} HTTPS URL", default=str(previous.get("url") or "")
            ).strip()
            credential = _wizard_prompt(
                f"MCP {index + 1} credential ref（可留空）",
                default=str(previous.get("credential") or ""),
            ).strip()
            if credential:
                entry["credential"] = credential
        result.append(entry)
    return result


def _expand_legacy_agent_resources(
    current: Mapping[str, Any], workspace: Workspace | None
) -> Mapping[str, Any]:
    """Read old Workspace Profile/MCP selections into a Launch working copy."""

    if workspace is None:
        return current
    result = _copy_mapping(current)
    migrated = False
    profile_id = current.get("profile")
    if isinstance(profile_id, str) and profile_id.strip():
        path = workspace.paths.agent_profiles_root() / f"{profile_id}.toml"
        try:
            loaded = tomllib.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
            loaded = {}
        profile = loaded.get("profile", loaded)
        if isinstance(profile, Mapping):
            inline = {
                str(key): _copy_value(value)
                for key, value in profile.items()
                if key != "id"
            }
            if inline:
                result["profile"] = inline
                migrated = True

    selections = current.get("mcp")
    legacy_selections = (
        selections
        if isinstance(selections, list)
        and any(
            isinstance(item, Mapping) and "server" in item and "profile" in item
            for item in selections
        )
        else None
    )
    if legacy_selections is not None:
        try:
            loaded = tomllib.loads(
                workspace.paths.agent_mcp_config().read_text(encoding="utf-8")
            )
        except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
            loaded = {}
        servers = loaded.get("servers")
        profiles = loaded.get("profiles")
        inline_mcp: list[dict[str, Any]] = []
        if isinstance(servers, Mapping) and isinstance(profiles, Mapping):
            for selection in legacy_selections:
                if not isinstance(selection, Mapping):
                    continue
                server_id = selection.get("server")
                policy_id = selection.get("profile")
                server = servers.get(server_id) if isinstance(server_id, str) else None
                policy = profiles.get(policy_id) if isinstance(policy_id, str) else None
                if not isinstance(server, Mapping) or not isinstance(policy, Mapping):
                    continue
                inline_mcp.append(
                    {
                        "id": server_id,
                        **_copy_mapping(server),
                        **{
                            str(key): _copy_value(value)
                            for key, value in policy.items()
                            if key != "server"
                        },
                    }
                )
        if inline_mcp:
            result["mcp"] = inline_mcp
            migrated = True
    if migrated:
        typer.echo("已将旧 Workspace Agent Profile/MCP 展开到当前 Launch 工作草稿。")
    return result


def _prompt_csv(
    label: str, default: tuple[str, ...], *, hint: str | None = None
) -> tuple[str, ...]:
    prompt = label if hint is None else f"{label} [{hint}]"
    value = _wizard_prompt(prompt, default=", ".join(default), show_default=True)
    return tuple(
        dict.fromkeys(item.strip() for item in value.split(",") if item.strip())
    )


def _string_items(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item.strip())


def draft_preview(values: Mapping[str, Any]) -> str:
    launch = _mapping(values.get("launch"))
    mode = str(launch.get("mode") or "")
    execution = _mapping(values.get("execution"))
    accounts = _mapping(values.get("accounts"))
    agent = _mapping(values.get("agent"))
    notifications = _mapping(values.get("notifications"))
    risk = _mapping(values.get("risk"))

    account_lines: list[str] = []
    for entry in accounts.values():
        if not isinstance(entry, Mapping):
            continue
        ref = str(entry.get("ref") or "")
        segments = ",".join(str(item) for item in entry.get("segments", ()) or ())
        access = "允许交易" if entry.get("trade") is True else "只读"
        account_lines.append(f"{ref} / {segments or '全部 segment'} / {access}")

    if mode == "backtest":
        backtest = _mapping(values.get("backtest"))
        market = _mapping(backtest.get("market"))
        market_summary = (
            f"replay={market.get('events') or '(未选择)'} · "
            f"{market.get('start') or '?'} → {market.get('end') or '?'}"
        )
    else:
        mode_config = _mapping(values.get(mode))
        market = _mapping(mode_config.get("market"))
        market_summary = (
            f"{market.get('profile') or 'workspace-default'} / "
            f"{market.get('scope') or 'shared'}"
        )

    model = _mapping(agent.get("model"))
    profile = _mapping(agent.get("profile"))
    mcp = agent.get("mcp") if isinstance(agent.get("mcp"), list) else []
    allowed_tools = {
        str(tool)
        for server in mcp
        if isinstance(server, Mapping)
        for tool in (server.get("allowed_tools") or ())
    }
    write_tools = {
        tool
        for tool in allowed_tools
        if any(
            marker in tool.lower()
            for marker in ("create", "submit", "place", "cancel", "write", "modify")
        )
    }
    routes = _mapping(notifications.get("routes"))
    destinations = sorted(
        {
            str(destination)
            for selected in routes.values()
            if isinstance(selected, list)
            for destination in selected
        }
    )
    live_safety = _mapping(_mapping(values.get("live")).get("safety"))
    max_notional = live_safety.get("max_order_notional")
    return "\n".join(
        [
            "运行方案最终摘要（不含 Secret）",
            f"  名称         {launch.get('id', '')}",
            f"  Strategy     {launch.get('strategy', '')}",
            f"  模式         {mode.upper()}{'（会连接真实账户）' if mode == 'live' else ''}",
            f"  市场与数据   {market_summary}",
            f"  账户         {'；'.join(account_lines) or '(无)'}",
            f"  Execution    {'开启' if execution.get('enabled', True) else '关闭'} · {len(execution.get('routes', ()) or ())} 条 route",
            f"  风险         {risk.get('profile') or '(未配置)'}"
            + (
                f" · 单笔最大名义金额 {max_notional}"
                if max_notional is not None
                else ""
            ),
            (
                "  Agent        关闭"
                if not agent.get("enabled", False)
                else "  Agent        开启 · "
                f"{model.get('connection') or model.get('credential') or 'fixture'} / {model.get('model') or agent.get('runtime') or ''}"
                f" · Profile v{profile.get('version') or '?'}"
            ),
            (
                "  Agent scope  -"
                if not agent.get("enabled", False)
                else f"  Agent scope  {_effective_agent_scope(values)}"
            ),
            f"  工具范围     {len(allowed_tools)} 个工具 · {len(write_tools)} 个疑似写入工具",
            (
                "  通知         关闭"
                if not notifications.get("enabled", False)
                else f"  通知         {', '.join(destinations) or '(未选择)'} · "
                f"{'required' if notifications.get('required') else 'optional'}"
            ),
        ]
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _effective_agent_scope(values: Mapping[str, Any]) -> str:
    accounts = _mapping(values.get("accounts"))
    account_scopes = []
    for item in accounts.values():
        if not isinstance(item, Mapping) or not item.get("ref"):
            continue
        segments = ",".join(str(value) for value in item.get("segments") or ())
        access = "trade" if item.get("trade") is True else "read"
        account_scopes.append(f"{item['ref']}[{segments or 'all'}:{access}]")
    launch = _mapping(values.get("launch"))
    mode = str(launch.get("mode") or "")
    market = _mapping(_mapping(values.get(mode)).get("market"))
    return (
        f"accounts={','.join(account_scopes) or 'none'} · "
        f"market={market.get('profile') or mode or 'default'} · "
        "MCP 不可扩大范围"
    )


def write_atomic(path: Path, values: Mapping[str, Any]) -> None:
    _replace_with_toml(path, values)


def _replace_with_toml(path: Path, values: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_toml_document(values))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def build_and_validate(
    path: Path, values: Mapping[str, Any], workspace_root: Path
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_toml_document(values))
            handle.flush()
            os.fsync(handle.fileno())
        report = LaunchConfigurationApplication().validate(
            Path(temporary), workspace_root=workspace_root
        )
        if not report["valid"]:
            raise LaunchConfigError("; ".join(report["issues"]))
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return {**report, "path": str(path)}


def _account_defaults(values: Mapping[str, Any]) -> tuple[str, ...]:
    accounts = values.get("accounts")
    if isinstance(accounts, Mapping):
        return tuple(
            str(item.get("ref"))
            for item in accounts.values()
            if isinstance(item, Mapping) and item.get("ref")
        )
    account = values.get("account")
    if isinstance(account, Mapping) and account.get("ref"):
        return (str(account["ref"]),)
    return ()


def _prompt_choice(label: str, default: str, choices: tuple[str, ...]) -> str:
    value = (
        _wizard_prompt(f"{label} [{'/'.join(choices)}]", default=default)
        .strip()
        .lower()
    )
    if value not in choices:
        raise typer.BadParameter(f"{label} must be one of: {', '.join(choices)}")
    return value


def _table(values: dict[str, Any], key: str) -> dict[str, Any]:
    current = values.get(key)
    if not isinstance(current, dict):
        current = {}
        values[key] = current
    return current


def _copy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _copy_value(item) for key, item in value.items()}


def _copy_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _copy_mapping(value)
    if isinstance(value, list):
        return [_copy_value(item) for item in value]
    return value


def _toml_document(values: Mapping[str, Any]) -> str:
    lines: list[str] = []
    _write_table(lines, (), values)
    return "\n".join(lines).rstrip() + "\n"


def _write_table(
    lines: list[str], prefix: tuple[str, ...], values: Mapping[str, Any]
) -> None:
    scalars = [
        (key, value)
        for key, value in values.items()
        if not isinstance(value, Mapping) and not _is_list_of_tables(value)
    ]
    tables = [
        (key, value) for key, value in values.items() if isinstance(value, Mapping)
    ]
    array_tables = [
        (key, value) for key, value in values.items() if _is_list_of_tables(value)
    ]
    if prefix:
        if lines:
            lines.append("")
        lines.append(f"[{'.'.join(prefix)}]")
    for key, value in scalars:
        lines.append(f"{key} = {_toml_value(value)}")
    for key, value in tables:
        _write_table(lines, (*prefix, str(key)), value)
    for key, items in array_tables:
        for item in items:
            if lines:
                lines.append("")
            lines.append(f"[[{'.'.join((*prefix, str(key)))}]]")
            for item_key, item_value in item.items():
                lines.append(f"{item_key} = {_toml_value(item_value)}")


def _is_list_of_tables(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, Mapping) for item in value)
    )


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    if value is None:
        return '""'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return _toml_value(str(value))
