"""Launch configuration drafting and atomic persistence."""

from __future__ import annotations

import os
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .configuration import LaunchConfigError, LaunchConfigurationApplication


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
                    "environment": (
                        "paper"
                        if self.execution_broker_id in {"simulated", "paper"}
                        else self.mode
                    ),
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
    raw_mcp = agent.get("mcp")
    mcp: list[object] = list(raw_mcp) if isinstance(raw_mcp, list) else []
    allowed_tools: set[str] = set()
    for server in mcp:
        if not isinstance(server, Mapping):
            continue
        tools = server.get("allowed_tools")
        if isinstance(tools, list):
            allowed_tools.update(str(tool) for tool in tools)
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
