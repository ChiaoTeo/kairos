"""Interactive launch configuration drafting and persistence."""

from __future__ import annotations

import os
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

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
    execution_provider: str | None = None
    backtest_start: str | None = None
    backtest_end: str | None = None
    backtest_events: str | None = None

    def apply(self, values: Mapping[str, Any]) -> dict[str, Any]:
        result = _copy_mapping(values)
        launch = _table(result, "launch")
        launch.update(
            {"id": self.launch_id, "mode": self.mode, "strategy": self.strategy}
        )

        result.pop("account", None)
        result["accounts"] = {
            f"account_{index + 1}": {"ref": account, "enabled": True}
            for index, account in enumerate(self.accounts)
        }
        execution = _table(result, "execution")
        execution["enabled"] = self.execution_enabled
        if self.execution_provider:
            execution["provider"] = self.execution_provider

        if self.mode == "backtest":
            backtest = _table(result, "backtest")
            market = _table(backtest, "market")
            if self.backtest_start:
                market["start"] = self.backtest_start
            if self.backtest_end:
                market["end"] = self.backtest_end
            if self.backtest_events:
                market["events"] = self.backtest_events
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
    values: Mapping[str, Any] | None = None, *, default_launch_id: str = "new-launch"
) -> LaunchDraft:
    current = values or {}
    launch_value = current.get("launch")
    launch: Mapping[str, Any] = (
        launch_value if isinstance(launch_value, Mapping) else {}
    )
    mode = _prompt_choice(
        "运行模式", str(launch.get("mode", "paper")), ("backtest", "paper", "live")
    )
    strategy = str(launch.get("strategy") or "builtin:interactive")
    strategy = typer.prompt("策略引用", default=strategy)

    accounts = _account_defaults(current)
    account_text = typer.prompt(
        "账户引用（多个账户用逗号分隔，可留空）", default=", ".join(accounts)
    )
    account_refs = tuple(
        item.strip() for item in account_text.split(",") if item.strip()
    )

    execution_value = current.get("execution")
    execution: Mapping[str, Any] = (
        execution_value if isinstance(execution_value, Mapping) else {}
    )
    execution_enabled = typer.confirm(
        "启用 Execution", default=bool(execution.get("enabled", True))
    )
    provider_default = str(execution.get("provider", "simulated"))
    provider = (
        typer.prompt("Execution provider", default=provider_default)
        if execution_enabled
        else None
    )

    backtest_value = current.get("backtest")
    backtest: Mapping[str, Any] = (
        backtest_value if isinstance(backtest_value, Mapping) else {}
    )
    market_value = backtest.get("market")
    market: Mapping[str, Any] = (
        market_value if isinstance(market_value, Mapping) else {}
    )
    start = end = events = None
    if mode == "backtest":
        start = typer.prompt(
            "回测开始时间", default=str(market.get("start", "2024-01-01T00:00:00Z"))
        )
        end = typer.prompt(
            "回测结束时间", default=str(market.get("end", "2024-01-02T00:00:00Z"))
        )
        events = (
            typer.prompt(
                "回放事件文件（可留空）", default=str(market.get("events", ""))
            )
            or None
        )

    return LaunchDraft(
        launch_id=str(launch.get("id") or default_launch_id),
        mode=mode,
        strategy=strategy,
        accounts=account_refs,
        execution_enabled=execution_enabled,
        execution_provider=provider,
        backtest_start=start,
        backtest_end=end,
        backtest_events=events,
    )


def draft_preview(values: Mapping[str, Any]) -> str:
    launch = values.get("launch", {})
    execution = values.get("execution", {})
    accounts = values.get("accounts", {})
    return "\n".join(
        [
            "Launch configuration preview",
            f"  id: {launch.get('id', '')}",
            f"  mode: {launch.get('mode', '')}",
            f"  strategy: {launch.get('strategy', '')}",
            f"  accounts: {', '.join(str(v.get('ref')) for v in accounts.values() if isinstance(v, Mapping)) or '(none)'}",
            f"  execution: {'enabled' if execution.get('enabled', True) else 'disabled'}",
        ]
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
        typer.prompt(f"{label} [{'/'.join(choices)}]", default=default).strip().lower()
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
