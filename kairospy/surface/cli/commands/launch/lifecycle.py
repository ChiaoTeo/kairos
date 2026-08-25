"""Launch runtime lifecycle commands."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import typer

from kairospy.investment.apps.account.application import AccountConfigurationApplication
from kairospy.surface.cli.options import OutputFormat
from kairospy.system.apps.launch.application import (
    LaunchConfigError,
    LaunchConfigurationApplication,
    LaunchRuntimeApplication,
    LaunchRuntimeError,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication

from . import launch_app
from .support import (
    _emit,
    _launch_config_path,
)


@launch_app.command(
    "start", help="Start a configured strategy launch and its dependencies."
)
def start(
    launch_id: str | None = typer.Argument(None),
    strategy: str | None = typer.Option(
        None, "--strategy", help="Strategy import path: module:callable"
    ),
    config: Path | None = typer.Option(
        None, "--config", help="Launch TOML configuration path."
    ),
    params: str | None = typer.Option(
        None, "--params", help="JSON object passed to the strategy factory"
    ),
    account_id: list[str] = typer.Option(
        [], "--account-id", help="Account binding to lease; repeatable."
    ),
    confirm_live: bool = typer.Option(
        False,
        "--confirm-live",
        help="Explicitly acknowledge the displayed live account and risk scope.",
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    config_path: Path | None = config
    if config_path is None and launch_id is not None:
        try:
            config_path = _launch_config_path(owner, launch_id)
        except (FileNotFoundError, ValueError) as error:
            raise typer.BadParameter(str(error), param_hint="launch_id") from error
    if config_path is None:
        raise typer.BadParameter(
            "launch TOML config is required; pass --config or use "
            "config/launches/<launch-id>.toml"
        )
    try:
        launch_config = LaunchConfigurationApplication().load(
            config_path, workspace_root=owner.paths.root
        )
        launch_config.require_valid()
    except LaunchConfigError as error:
        raise typer.BadParameter(str(error), param_hint="--config") from error
    positional_config = launch_id is not None and Path(launch_id).expanduser().is_file()
    if (
        launch_id is not None
        and not positional_config
        and launch_id != launch_config.launch_id
    ):
        raise typer.BadParameter("launch id does not match launch config")
    if strategy is not None and strategy != launch_config.strategy:
        raise typer.BadParameter("--strategy does not match launch config")
    if launch_config.mode == "live":
        confirmation = _live_start_confirmation(owner, launch_config)
        typer.echo(confirmation)
        if not confirm_live:
            raise typer.BadParameter(
                "live Launch requires explicit --confirm-live; "
                "use `kairos interactive` for guided confirmation"
            )
    overrides: dict[str, Any] = {}
    if params:
        try:
            value = json.loads(params)
        except json.JSONDecodeError as error:
            raise typer.BadParameter("--params must be a JSON object") from error
        if not isinstance(value, dict):
            raise typer.BadParameter("--params must be a JSON object")
        overrides = value
    try:
        value = LaunchRuntimeApplication(owner).start(
            launch_config,
            strategy_params=overrides,
            account_ids=tuple(account_id),
        )
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(value, output)


@launch_app.command("status", help="Show aggregate strategy and dependency health.")
def status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    try:
        value = LaunchRuntimeApplication(owner).status(launch_id, instance=instance)
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(value, output)


@launch_app.command("report")
def report(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read the immutable report emitted when a backtest replay completes."""
    owner = WorkspaceApplication().open(workspace)
    try:
        value = LaunchRuntimeApplication(owner).report(launch_id, instance=instance)
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(value, output)


@launch_app.command("wait")
def wait(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    timeout: float = typer.Option(3600.0, "--timeout", min=0.1),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Wait for a backtest replay, tear down runtime actors, and return its report."""
    owner = WorkspaceApplication().open(workspace)
    try:
        value = LaunchRuntimeApplication(owner).wait(
            launch_id, instance=instance, timeout=timeout
        )
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(value, output)


@launch_app.command("stop", help="Stop a launch and release its runtime resources.")
def stop(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    try:
        value = LaunchRuntimeApplication(owner).stop(launch_id, instance=instance)
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(value, output)


@launch_app.command(
    "restart", help="Stop and start a launch with a new runtime instance."
)
def restart(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    confirm_live: bool = typer.Option(False, "--confirm-live"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    try:
        config_path = _launch_config_path(owner, launch_id)
        launch_config = LaunchConfigurationApplication().load(
            config_path, workspace_root=owner.paths.root
        )
        if launch_config.mode == "live":
            typer.echo(_live_start_confirmation(owner, launch_config))
            if not confirm_live:
                raise typer.BadParameter(
                    "live Launch requires explicit --confirm-live; "
                    "use `kairos interactive` for guided confirmation"
                )
        value = LaunchRuntimeApplication(owner).restart(
            launch_id, instance=instance, config_path=config_path
        )
    except (LaunchConfigError, LaunchRuntimeError, FileNotFoundError) as error:
        raise typer.BadParameter(str(error)) from error
    _emit(value, output)


def _live_start_confirmation(owner: Any, launch_config: Any) -> str:
    """Render the second, start-time live warning without resolving secrets."""

    plan = launch_config.plan()
    configured_accounts = _mapping(launch_config.values.get("accounts"))
    intent_by_ref = {
        str(value.get("ref")): value
        for value in configured_accounts.values()
        if isinstance(value, Mapping) and value.get("ref")
    }
    account_lines: list[str] = []
    accounts = AccountConfigurationApplication(owner)
    for account_id in plan.account_refs:
        try:
            account = accounts.show(account_id)
        except (KeyError, OSError, RuntimeError, ValueError):
            account = {}
        intent = intent_by_ref.get(account_id, {})
        segments = intent.get("segments") or account.get("segments") or ()
        account_lines.append(
            f"{account_id} / {account.get('environment') or 'live'} / "
            f"{','.join(str(value) for value in segments) or '全部 segment'} / "
            f"{'允许交易' if intent.get('trade') is True else '只读'}"
        )
    safety = plan.live_safety or {}
    maximum = safety.get("max_order_notional", "由 risk profile 限定")
    return "\n".join(
        (
            "LIVE 启动高风险确认（不会展示 Secret）",
            f"  Launch       {plan.launch_id}",
            f"  环境         LIVE（会产生真实外部副作用）",
            f"  真实账户     {'；'.join(account_lines) or '(无账户)'}",
            f"  风险 Profile {plan.risk_profile or '(未配置)'}",
            f"  单笔最大范围 {maximum}",
            f"  限价单要求   {'是' if safety.get('require_limit_orders', True) else '否'}",
        )
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
