from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import redirect_stdout
from dataclasses import asdict
from io import StringIO
import sys
import time
from pathlib import Path
from typing import Any

import typer
from prettytable import PrettyTable

from kairospy.investment.apps.account.application import AccountConfigurationApplication
from kairospy.investment.apps.market.application import read_replay_events
from kairospy.system.apps.launch.application import (
    LaunchConfigError,
    LaunchConfigurationApplication,
    LaunchControlApplication,
    LaunchInstanceTimelineApplication,
    LaunchRegistryApplication,
    LaunchRuntimeApplication,
    LaunchRuntimeError,
)
from kairospy.system.apps.launch.application.runtime import (
    acquire_launch_leases as _acquire_launch_leases,
    cleanup_instance_components as _cleanup_instance_components,
    release_launch_leases as _release_launch_leases,
    requires_reference_runtime as _requires_reference_runtime,
    stop_component_safely as _stop_component_safely,
)
from kairospy.system.apps.launch.application.connections import (
    resolve_instance_connections,
)
from kairospy.system.apps.components.application import (
    InstanceSystemClients,
    NativeCliApplication,
    UnixRestClient,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.surface.cli.options import (
    OutputFormat,
    effective_output,
    render,
    reset_command_output,
    set_command_output,
)


launch_app = typer.Typer(no_args_is_help=True, help="Manage launch instances")
draft_app = typer.Typer(no_args_is_help=True, help="Manage persisted Launch drafts")
launch_app.add_typer(draft_app, name="draft")
strategy_app = typer.Typer(
    no_args_is_help=True, help="Manage the strategy inside a launch instance"
)
launch_app.add_typer(strategy_app, name="strategy")
instance_app = typer.Typer(no_args_is_help=True, help="Inspect a launch instance")
instance_component_app = typer.Typer(
    no_args_is_help=True, help="Connect to components bound to a launch instance"
)
instance_component_market_app = typer.Typer(
    no_args_is_help=True,
    help="Connect to the Market component bound to a launch instance",
)
instance_component_account_app = typer.Typer(
    no_args_is_help=True,
    help="Connect to Account components bound to a launch instance",
)
instance_component_execution_app = typer.Typer(
    no_args_is_help=True,
    help="Connect to the Execution component bound to a launch instance",
)
instance_component_reference_app = typer.Typer(
    no_args_is_help=True,
    help="Connect to the Reference component bound to a launch instance",
)
instance_component_risk_app = typer.Typer(
    no_args_is_help=True,
    help="Connect to the Risk component bound to a launch instance",
)
instance_component_capital_app = typer.Typer(
    no_args_is_help=True,
    help="Connect to the Capital component bound to a launch instance",
)
instance_timeline_app = typer.Typer(
    no_args_is_help=True, help="Inspect lifecycle records from one launch instance"
)
launch_app.add_typer(instance_app, name="instance")
instance_app.add_typer(instance_component_app, name="component")
instance_component_app.add_typer(instance_component_account_app, name="account")
instance_component_app.add_typer(instance_component_market_app, name="market")
instance_component_app.add_typer(instance_component_execution_app, name="execution")
instance_component_app.add_typer(instance_component_reference_app, name="reference")
instance_component_app.add_typer(instance_component_risk_app, name="risk")
instance_component_app.add_typer(instance_component_capital_app, name="capital")
instance_app.add_typer(instance_timeline_app, name="timeline")


@launch_app.command("init", help="Create a Launch in the unified workbench.")
def init_launch(
    launch_id: str = typer.Argument("new-launch"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    del output
    owner = WorkspaceApplication().open(workspace)
    path = owner.paths.launch_config(launch_id)
    if path.exists():
        raise typer.BadParameter(f"launch config already exists: {path}")
    _open_launch_setup(owner, launch_id, None)


@launch_app.command("edit", help="Edit a Launch in the unified workbench.")
def edit_launch(
    launch_id: str = typer.Argument(..., help="Launch id or launch TOML path."),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    del output
    owner = WorkspaceApplication().open(workspace)
    candidate = Path(launch_id).expanduser()
    if candidate.is_file():
        source = candidate.resolve()
        resolved_id = source.stem
    else:
        application = LaunchConfigurationApplication()
        draft = application.draft_path(owner.paths.root, launch_id)
        source = draft if draft.is_file() else _launch_config_path(owner, launch_id)
        resolved_id = source.stem
    _open_launch_setup(owner, resolved_id, source)

@draft_app.command("list")
def list_launch_drafts(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(LaunchConfigurationApplication().list_drafts(owner.paths.root), output)


@draft_app.command("discard")
def discard_launch_draft(
    launch_id: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    LaunchConfigurationApplication().discard_draft(owner.paths.root, launch_id)
    _emit({"launch_id": launch_id, "status": "discarded"}, output)


def _open_launch_setup(owner: Any, launch_id: str, source: Path | None) -> None:
    from kairospy.surface.workbench import KairosWorkbenchApp, load_workbench_state

    state = load_workbench_state(Path(owner.paths.root))
    KairosWorkbenchApp(
        state,
        initial_launch_setup=(launch_id, source),
    ).run()

def _group(name: str, commands: tuple[str, ...]) -> typer.Typer:
    descriptions = {
        "targets": "Manage reusable launch targets.",
        "diagnose": "Validate and explain launch configuration.",
        "replay": "Inspect replay input and progress.",
    }
    group = typer.Typer(
        no_args_is_help=True, help=descriptions.get(name, f"Launch {name} commands")
    )
    launch_app.add_typer(group, name=name)
    del commands
    return group


targets_app = _group("targets", ("add", "remove", "index", "list", "browse"))
diagnose_app = _group("diagnose", ("validate", "explain"))
replay_app = _group("replay", ("events",))


def _target(launch_id: str, instance: str, mode: str, workspace: Path):
    value = WorkspaceApplication().open(workspace)
    return LaunchControlApplication(value).target(launch_id, instance, mode=mode)


def _running_instance(owner, launch_id: str, mode: str | None = None) -> dict | None:
    try:
        return LaunchRuntimeApplication(owner).running_instance(launch_id, mode)
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error


def _resolve_launch_target(
    owner,
    launch_id: str,
    mode: str | None,
    instance: str | None,
) -> tuple[str, str]:
    try:
        return LaunchRuntimeApplication(owner).resolve_target(
            launch_id, mode=mode, instance=instance
        )
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error


def _decorate_launch_status(
    owner, launch_id: str, instance: str, mode: str, value: dict
) -> dict:
    return LaunchRuntimeApplication(owner).decorate_status(
        launch_id, instance, mode, value
    )


def _resolve_stop_instance(
    owner,
    launch_id: str,
    instance: str | None,
    mode: str | None,
) -> tuple[str, str]:
    try:
        return LaunchRuntimeApplication(owner).resolve_stop_target(
            launch_id, instance=instance, mode=mode
        )
    except LaunchRuntimeError as error:
        raise typer.BadParameter(str(error)) from error


def _emit(value: object, output: OutputFormat) -> None:
    typer.echo(render(value, output))


def _emit_launch_account_balances(
    value: dict[str, object], output: OutputFormat
) -> None:
    if effective_output(output) is OutputFormat.JSON:
        _emit(value, output)
        return
    typer.echo(_render_launch_account_balances(value))


def _render_launch_account_balances(value: dict[str, object]) -> str:
    account_id = str(value["account_id"])
    balances = value.get("balances", [])
    if not isinstance(balances, list) or not balances:
        return f"No balances for account {account_id}."

    table = PrettyTable(["SEGMENT", "ASSET", "TOTAL", "AVAILABLE", "RESERVED"])
    table.align = "l"
    for balance in balances:
        if not isinstance(balance, dict):
            continue
        table.add_row(
            [
                balance.get("segment_key", balance.get("segment", "—")),
                balance.get("asset", "—"),
                balance.get("total", "—"),
                balance.get("available", "—"),
                balance.get("reserved", balance.get("locked", "—")),
            ]
        )
    context = (
        f"Account {account_id} · launch {value['launch_id']}/{value['instance_id']} "
        f"({value['mode']})"
    )
    return f"{context}\n{table}"


def _launch_config_path(owner, target: str | Path) -> Path:
    candidate = Path(target).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    if candidate.is_file():
        return candidate
    configured = owner.paths.launch_config(str(target))
    if configured.is_file():
        return configured
    raise FileNotFoundError(
        f"launch {target!s} has no configuration; expected {configured}. "
        "Run 'kairos project doctor' to inspect project readiness."
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
    """Restart a launch without duplicating its start/stop lifecycle logic."""
    stop_output = StringIO()
    output_token = set_command_output(OutputFormat.JSON)
    try:
        with redirect_stdout(stop_output):
            stop(
                launch_id,
                instance=instance,
                workspace=workspace,
                output=OutputFormat.JSON,
            )
    finally:
        reset_command_output(output_token)
    try:
        stopped = json.loads(stop_output.getvalue())
    except json.JSONDecodeError as error:
        raise typer.BadParameter("launch stop did not return a valid result") from error
    if stopped.get("status") != "stopped":
        issues = stopped.get("stop_issues") or {}
        raise typer.BadParameter(
            f"launch {launch_id} was not fully stopped; restart aborted: {issues}"
        )
    start(
        launch_id,
        strategy=None,
        config=None,
        params=None,
        account_id=[],
        confirm_live=confirm_live,
        workspace=workspace,
        output=output,
    )


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


@strategy_app.command("status")
def strategy_status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    _emit(
        LaunchControlApplication(owner).status(
            _target(launch_id, resolved_instance, mode, workspace)
        ),
        output,
    )


@strategy_app.command("decision")
def strategy_decision(
    launch_id: str,
    strategy_decision_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Show one end-to-end Strategy decision trace."""

    owner = WorkspaceApplication().open(workspace)
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    target = _target(launch_id, resolved_instance, mode, workspace)
    _emit(
        LaunchControlApplication(owner).decision(target, strategy_decision_id),
        output,
    )


def _strategy_action(action: str):
    def command(
        launch_id: str,
        instance: str | None = typer.Option(None, "--instance"),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        resolved_instance, mode = _resolve_launch_target(
            owner, launch_id, None, instance
        )
        target = _target(launch_id, resolved_instance, mode, workspace)
        _emit(
            LaunchControlApplication(owner).strategy_control(target, action),
            output,
        )

    command.__name__ = f"strategy_{action}"
    return command


for _action in ("enable", "pause", "resume", "refresh"):
    strategy_app.command(_action)(_strategy_action(_action))


def _instance_account_snapshot(
    owner,
    *,
    launch_id: str,
    instance: str | None,
    account_id: str,
) -> tuple[dict[str, Any], str, str]:
    from kairospy.primitives.account import AccountId

    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    connections = resolve_instance_connections(instance_workspace)
    clients = InstanceSystemClients.from_connections(connections)
    account_key = AccountId(account_id)
    client = clients.accounts.get(account_key)
    if client is None:
        raise typer.BadParameter(
            f"launch instance has no connected account component for {account_id}"
        )
    snapshot = client.current_view(account_key).snapshot(account_key)
    return asdict(snapshot), resolved_instance, mode


def _instance_account_client(
    owner,
    *,
    launch_id: str,
    instance: str | None,
    account_id: str,
):
    from kairospy.primitives.account import AccountId

    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    connections = resolve_instance_connections(instance_workspace)
    clients = InstanceSystemClients.from_connections(connections)
    account_key = AccountId(account_id)
    client = clients.accounts.get(account_key)
    if client is None:
        raise typer.BadParameter(
            f"launch instance has no connected account component for {account_id}"
        )
    return client, resolved_instance, mode


def _run_account_connected_command(
    owner: Any,
    launch_id: str,
    instance: str | None,
    account_id: str,
    command: str,
    arguments: list[str],
) -> dict[str, Any]:
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    value = NativeCliApplication(owner).run(
        "account",
        [
            "--account-id",
            account_id,
            "--launch-id",
            launch_id,
            "--launch-mode",
            mode,
            "--instance-id",
            resolved_instance,
            "connected",
            command,
            *arguments,
        ],
    )
    value.setdefault("account_id", account_id)
    value.setdefault("launch_id", launch_id)
    value.setdefault("instance_id", resolved_instance)
    value.setdefault("mode", mode)
    value.setdefault("scope", "launch-instance")
    return value


@instance_component_account_app.command("snapshot")
def launch_instance_component_account_snapshot(
    launch_id: str,
    account_id: str = typer.Option(..., "--account-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read one Account current view selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    snapshot, resolved_instance, mode = _instance_account_snapshot(
        owner, launch_id=launch_id, instance=instance, account_id=account_id
    )
    _emit(
        {
            **snapshot,
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_account_app.command("balances")
def launch_instance_component_account_balances(
    launch_id: str,
    account_id: str = typer.Option(..., "--account-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read balances from an Account component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    snapshot, resolved_instance, mode = _instance_account_snapshot(
        owner, launch_id=launch_id, instance=instance, account_id=account_id
    )
    balances = [
        balance
        for segment in snapshot["segments"]
        for balance in segment.get("balances", [])
    ]
    _emit_launch_account_balances(
        {
            "account_id": account_id,
            "balances": balances,
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_account_app.command("positions")
def launch_instance_component_account_positions(
    launch_id: str,
    account_id: str = typer.Option(..., "--account-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read positions from an Account component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    snapshot, resolved_instance, mode = _instance_account_snapshot(
        owner, launch_id=launch_id, instance=instance, account_id=account_id
    )
    positions = [
        position
        for segment in snapshot["segments"]
        for position in segment.get("positions", [])
    ]
    _emit(
        {
            "account_id": account_id,
            "positions": positions,
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


def _run_instance_market_connected_command(
    owner: Any,
    *,
    launch_id: str,
    instance: str | None,
    command: str,
    arguments: list[str],
    require_views: bool,
) -> dict[str, Any]:
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    connections = resolve_instance_connections(instance_workspace)
    connection = connections.market
    if connection is None:
        raise typer.BadParameter(
            f"launch {launch_id} instance {resolved_instance} has no connected Market component"
        )
    if not connection.socket.exists():
        raise typer.BadParameter(
            f"无法连接 launch {launch_id} instance {resolved_instance} Market 服务。"
            "请先查看该 launch instance 的组件状态。"
        )
    target = ["--socket", str(connection.socket)]
    if connection.view_root is not None:
        target.extend(("--view-root", str(connection.view_root)))
    elif require_views:
        raise typer.BadParameter(
            f"launch {launch_id} instance {resolved_instance} Market connection "
            "has no view root"
        )
    value = NativeCliApplication(owner).run(
        "market", ["connected", command, *target, *arguments]
    )
    return {
        **value,
        "launch_id": launch_id,
        "instance_id": resolved_instance,
        "mode": mode,
        "scope": "launch-instance",
    }


@instance_component_market_app.command("status")
def launch_instance_component_market_status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the Market component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    market = LaunchRuntimeApplication(owner).component_status(instance_workspace)[
        "market"
    ]
    _emit(
        {
            **market,
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_market_app.command("routes")
def launch_instance_component_market_routes(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    market_id: str | None = typer.Option(None, "--market-id"),
    instrument_id: str | None = typer.Option(None, "--instrument-id"),
    observation_kind: str | None = typer.Option(None, "--observation-kind"),
    provider: str | None = typer.Option(None, "--provider"),
    configured_only: bool = typer.Option(False, "--configured-only"),
    ready_only: bool = typer.Option(False, "--ready-only"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read Market provider-route readiness selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    arguments: list[str] = []
    for option, value in (
        ("--market-id", market_id),
        ("--instrument-id", instrument_id),
        ("--observation-kind", observation_kind),
        ("--provider", provider),
    ):
        if value is not None:
            arguments.extend((option, value))
    if configured_only:
        arguments.append("--configured-only")
    if ready_only:
        arguments.append("--ready-only")
    _emit(
        _run_instance_market_connected_command(
            owner,
            launch_id=launch_id,
            instance=instance,
            command="routes",
            arguments=arguments,
            require_views=False,
        ),
        output,
    )


@instance_component_market_app.command("snapshot")
def launch_instance_component_market_snapshot(
    launch_id: str,
    kind: str = typer.Argument(..., help="Snapshot kind: quote, bar, or greeks."),
    provider: str | None = typer.Option(None, "--provider"),
    market_id: str | None = typer.Option(None, "--market-id"),
    symbol: str | None = typer.Option(None, "--symbol"),
    exchange: str = typer.Option("binance", "--exchange"),
    market_type: str = typer.Option("spot", "--market-type"),
    timeframe: str | None = typer.Option(None, "--timeframe"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read one Market current view selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    if market_id is None:
        if not symbol:
            raise typer.BadParameter("snapshot requires --market-id or --symbol")
        market_id = f"market:{exchange.lower()}:{market_type.lower()}:{symbol.upper()}"
    arguments = [kind, "--market-id", market_id]
    if provider is not None:
        arguments.extend(("--provider", provider))
    if timeframe is not None:
        arguments.extend(("--timeframe", timeframe))
    _emit(
        _run_instance_market_connected_command(
            owner,
            launch_id=launch_id,
            instance=instance,
            command="snapshot",
            arguments=arguments,
            require_views=True,
        ),
        output,
    )


@instance_component_market_app.command("freshness")
def launch_instance_component_market_freshness(
    launch_id: str,
    market_id: str = typer.Option(..., "--market-id"),
    observation: str | None = typer.Option(None, "--observation"),
    provider: str | None = typer.Option(None, "--provider"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read Market freshness selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    arguments = ["--market-id", market_id]
    if observation is not None:
        arguments.extend(("--observation", observation))
    if provider is not None:
        arguments.extend(("--provider", provider))
    _emit(
        _run_instance_market_connected_command(
            owner,
            launch_id=launch_id,
            instance=instance,
            command="freshness",
            arguments=arguments,
            require_views=True,
        ),
        output,
    )


@instance_component_market_app.command("pause-replay")
def launch_instance_component_market_pause_replay(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Pause Market replay input selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_instance_market_connected_command(
            owner,
            launch_id=launch_id,
            instance=instance,
            command="pause-replay",
            arguments=[],
            require_views=False,
        ),
        output,
    )


@instance_component_market_app.command("resume-replay")
def launch_instance_component_market_resume_replay(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Resume Market replay input selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_instance_market_connected_command(
            owner,
            launch_id=launch_id,
            instance=instance,
            command="resume-replay",
            arguments=[],
            require_views=False,
        ),
        output,
    )


@instance_component_account_app.command("open-orders")
def launch_instance_component_account_open_orders(
    launch_id: str,
    account_id: str = typer.Option(..., "--account-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read observed orders from an Account component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_account_client(
        owner, launch_id=launch_id, instance=instance, account_id=account_id
    )
    from kairospy.primitives.account import AccountId

    account_key = AccountId(account_id)
    _emit(
        {
            **client.observed_orders_view(account_key).open_orders(account_key),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_account_app.command("refresh")
def launch_instance_component_account_refresh(
    launch_id: str,
    account_id: str = typer.Option(..., "--account-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Request refresh on an Account component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_account_connected_command(
            owner, launch_id, instance, account_id, "refresh", []
        ),
        output,
    )


@instance_component_account_app.command("reconcile")
def launch_instance_component_account_reconcile(
    launch_id: str,
    account_id: str = typer.Option(..., "--account-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Request reconciliation on an Account component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_account_connected_command(
            owner, launch_id, instance, account_id, "reconcile", []
        ),
        output,
    )


@instance_component_execution_app.command("status")
def launch_instance_component_execution_status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the Execution component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, resolved_mode = _resolve_launch_target(
        owner, launch_id, mode, instance
    )
    instance_workspace = owner.instance(resolved_mode, launch_id, resolved_instance)
    statuses = LaunchRuntimeApplication(owner).component_status(instance_workspace)
    if "execution" not in statuses:
        raise typer.BadParameter("launch instance has no connected execution component")
    _emit(
        {
            **statuses["execution"],
            "owner": "execution",
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": resolved_mode,
            "scope": "launch-instance",
        },
        output,
    )


def _run_execution_connected_command(
    owner: Any,
    launch_id: str,
    mode: str | None,
    instance: str | None,
    command: str,
    arguments: list[str],
) -> dict[str, Any]:
    resolved_instance, resolved_mode = _resolve_launch_target(
        owner, launch_id, mode, instance
    )
    value = NativeCliApplication(owner).run(
        "execution",
        [
            "connected",
            "--mode",
            resolved_mode,
            "--launch-id",
            launch_id,
            "--instance-id",
            resolved_instance,
            command,
            *arguments,
        ],
    )
    value.setdefault("launch_id", launch_id)
    value.setdefault("instance_id", resolved_instance)
    value.setdefault("mode", resolved_mode)
    value.setdefault("owner", "execution")
    value.setdefault("scope", "launch-instance")
    return value


def _execution_connected_passthrough(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None,
    mode: str | None,
    workspace: Path,
    output: OutputFormat,
    command: str,
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_execution_connected_command(
            owner,
            launch_id,
            mode,
            instance,
            command,
            list(ctx.args),
        ),
        output,
    )


_EXECUTION_PASSTHROUGH_CONTEXT = {
    "allow_extra_args": True,
    "ignore_unknown_options": True,
}


@instance_component_execution_app.command(
    "snapshot", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_snapshot(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read the launch-scoped Execution runtime snapshot."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "snapshot"
    )


@instance_component_execution_app.command(
    "routes", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_routes(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Execution route candidates."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "routes"
    )


@instance_component_execution_app.command(
    "orders", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_orders(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """List launch-scoped Execution orders."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "orders"
    )


@instance_component_execution_app.command(
    "open-orders", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_open_orders(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """List launch-scoped open Execution orders."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "open-orders"
    )


@instance_component_execution_app.command(
    "history", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_history(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """List launch-scoped closed Execution orders."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "history"
    )


@instance_component_execution_app.command(
    "fills", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_fills(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """List launch-scoped Execution fills."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "fills"
    )


@instance_component_execution_app.command(
    "events", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_events(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """List launch-scoped Execution lifecycle events."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "events"
    )


@instance_component_execution_app.command(
    "audit", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_audit(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Execution audit records."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "audit"
    )


@instance_component_execution_app.command(
    "inspect", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_inspect(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Inspect one launch-scoped Execution order."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "inspect"
    )


@instance_component_execution_app.command(
    "trace", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_trace(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Trace one launch-scoped Execution order."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "trace"
    )


@instance_component_execution_app.command(
    "journal", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_journal(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read the journal for one launch-scoped Execution order."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "journal"
    )


@instance_component_execution_app.command(
    "reconcile", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_reconcile(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Request launch-scoped Execution reconciliation."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "reconcile"
    )


@instance_component_execution_app.command(
    "unknown-remote-orders", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_unknown_remote_orders(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """List launch-scoped unknown remote Execution orders."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "unknown-remote-orders"
    )


@instance_component_execution_app.command(
    "submit", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_submit(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Submit an order through the launch-scoped Execution runtime."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "submit"
    )


@instance_component_execution_app.command(
    "cancel", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_cancel(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Cancel an order through the launch-scoped Execution runtime."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "cancel"
    )


@instance_component_execution_app.command(
    "replace", context_settings=_EXECUTION_PASSTHROUGH_CONTEXT
)
def launch_instance_component_execution_replace(
    ctx: typer.Context,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    mode: str | None = typer.Option(None, "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Replace an order through the launch-scoped Execution runtime."""
    _execution_connected_passthrough(
        ctx, launch_id, instance, mode, workspace, output, "replace"
    )


@instance_component_reference_app.command("status")
def launch_instance_component_reference_status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the Reference component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    statuses = LaunchRuntimeApplication(owner).component_status(instance_workspace)
    if "reference" not in statuses:
        raise typer.BadParameter("launch instance has no connected reference component")
    _emit(
        {
            **statuses["reference"],
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


def _launch_instance_component_named_status(
    owner, launch_id: str, component: str, instance: str | None
) -> tuple[dict[str, object], str, str]:
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    statuses = LaunchRuntimeApplication(owner).component_status(instance_workspace)
    if component not in statuses:
        raise typer.BadParameter(
            f"launch instance has no connected {component} component"
        )
    return statuses[component], resolved_instance, mode


@instance_component_risk_app.command("status")
def launch_instance_component_risk_status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the Risk component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    value, resolved_instance, mode = _launch_instance_component_named_status(
        owner, launch_id, "risk", instance
    )
    _emit(
        {
            **value,
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


def _run_risk_connected_command(
    owner: Any,
    launch_id: str,
    instance: str | None,
    command: str,
    arguments: list[str],
) -> dict[str, Any]:
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    value = NativeCliApplication(instance_workspace).run(
        "risk",
        [
            "connected",
            command,
            *arguments,
        ],
    )
    value.setdefault("launch_id", launch_id)
    value.setdefault("instance_id", resolved_instance)
    value.setdefault("mode", mode)
    value.setdefault("scope", "launch-instance")
    return value


@instance_component_risk_app.command("health")
def launch_instance_component_risk_health(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read launch-scoped Risk health through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(owner, launch_id, instance, "health", []),
        output,
    )


@instance_component_risk_app.command("latest")
def launch_instance_component_risk_latest(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    actor_id: str | None = typer.Option(None, "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Risk latest-view business facts."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, _mode = _resolve_launch_target(owner, launch_id, None, instance)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "latest",
            ["--actor-id", actor_id or f"risk:{resolved_instance}"],
        ),
        output,
    )


@instance_component_risk_app.command("limits")
def launch_instance_component_risk_limits(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    actor_id: str | None = typer.Option(None, "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Risk limit usage resources."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, _mode = _resolve_launch_target(owner, launch_id, None, instance)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "limits",
            ["--actor-id", actor_id or f"risk:{resolved_instance}"],
        ),
        output,
    )


@instance_component_risk_app.command("reservations")
def launch_instance_component_risk_reservations(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    actor_id: str | None = typer.Option(None, "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Risk active reservations."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, _mode = _resolve_launch_target(owner, launch_id, None, instance)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "reservations",
            ["--actor-id", actor_id or f"risk:{resolved_instance}"],
        ),
        output,
    )


@instance_component_risk_app.command("circuits")
def launch_instance_component_risk_circuits(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    actor_id: str | None = typer.Option(None, "--actor-id"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Risk circuit states."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, _mode = _resolve_launch_target(owner, launch_id, None, instance)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "circuits",
            ["--actor-id", actor_id or f"risk:{resolved_instance}"],
        ),
        output,
    )


@instance_component_risk_app.command("pre-trade-check")
def launch_instance_component_risk_pre_trade_check(
    launch_id: str,
    file: Path = typer.Option(..., "--file"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Evaluate launch-scoped Risk authorization through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "pre-trade-check",
            ["--file", str(file)],
        ),
        output,
    )


@instance_component_risk_app.command("authorize-reserve")
def launch_instance_component_risk_authorize_reserve(
    launch_id: str,
    file: Path = typer.Option(..., "--file"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Authorize and reserve launch-scoped Risk budget through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "authorize-reserve",
            ["--file", str(file)],
        ),
        output,
    )


@instance_component_risk_app.command("release")
def launch_instance_component_risk_release(
    launch_id: str,
    reservation_id: str = typer.Option(..., "--reservation-id"),
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Release a launch-scoped Risk reservation through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "release",
            [
                "--reservation-id",
                reservation_id,
                "--at-unix-nanos",
                str(at_unix_nanos),
            ],
        ),
        output,
    )


@instance_component_risk_app.command("consume")
def launch_instance_component_risk_consume(
    launch_id: str,
    reservation_id: str = typer.Option(..., "--reservation-id"),
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Consume a launch-scoped Risk reservation through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "consume",
            [
                "--reservation-id",
                reservation_id,
                "--at-unix-nanos",
                str(at_unix_nanos),
            ],
        ),
        output,
    )


@instance_component_risk_app.command("resize")
def launch_instance_component_risk_resize(
    launch_id: str,
    reservation_id: str = typer.Option(..., "--reservation-id"),
    amount: str = typer.Option(..., "--amount"),
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Resize a launch-scoped Risk reservation through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "resize",
            [
                "--reservation-id",
                reservation_id,
                "--amount",
                amount,
                "--at-unix-nanos",
                str(at_unix_nanos),
            ],
        ),
        output,
    )


def _risk_circuit_arguments(
    *,
    account_id: str | None,
    strategy_id: str | None,
    exchange_id: str | None,
) -> list[str]:
    arguments: list[str] = []
    if account_id is not None:
        arguments.extend(["--account-id", account_id])
    if strategy_id is not None:
        arguments.extend(["--strategy-id", strategy_id])
    if exchange_id is not None:
        arguments.extend(["--exchange-id", exchange_id])
    return arguments


@instance_component_risk_app.command("open-circuit")
def launch_instance_component_risk_open_circuit(
    launch_id: str,
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    reason: str = typer.Option(..., "--reason"),
    reset_at_unix_nanos: int | None = typer.Option(None, "--reset-at-unix-nanos"),
    account_id: str | None = typer.Option(None, "--account-id"),
    strategy_id: str | None = typer.Option(None, "--strategy-id"),
    exchange_id: str | None = typer.Option(None, "--exchange-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Open a launch-scoped Risk circuit through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    arguments = [
        "--at-unix-nanos",
        str(at_unix_nanos),
        "--reason",
        reason,
        *_risk_circuit_arguments(
            account_id=account_id,
            strategy_id=strategy_id,
            exchange_id=exchange_id,
        ),
    ]
    if reset_at_unix_nanos is not None:
        arguments.extend(["--reset-at-unix-nanos", str(reset_at_unix_nanos)])
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "open-circuit",
            arguments,
        ),
        output,
    )


@instance_component_risk_app.command("close-circuit")
def launch_instance_component_risk_close_circuit(
    launch_id: str,
    at_unix_nanos: int = typer.Option(..., "--at-unix-nanos"),
    account_id: str | None = typer.Option(None, "--account-id"),
    strategy_id: str | None = typer.Option(None, "--strategy-id"),
    exchange_id: str | None = typer.Option(None, "--exchange-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Close a launch-scoped Risk circuit through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "close-circuit",
            [
                "--at-unix-nanos",
                str(at_unix_nanos),
                *_risk_circuit_arguments(
                    account_id=account_id,
                    strategy_id=strategy_id,
                    exchange_id=exchange_id,
                ),
            ],
        ),
        output,
    )


@instance_component_risk_app.command("publish-policy")
def launch_instance_component_risk_publish_policy(
    launch_id: str,
    file: Path = typer.Option(..., "--file"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Publish a launch-scoped Risk policy through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "publish-policy",
            ["--file", str(file)],
        ),
        output,
    )


@instance_component_risk_app.command("advance-time")
def launch_instance_component_risk_advance_time(
    launch_id: str,
    event_time_unix_nanos: int = typer.Option(..., "--event-time-unix-nanos"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Advance launch-scoped Risk runtime time through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_risk_connected_command(
            owner,
            launch_id,
            instance,
            "advance-time",
            ["--event-time-unix-nanos", str(event_time_unix_nanos)],
        ),
        output,
    )


@instance_component_capital_app.command("status")
def launch_instance_component_capital_status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect the Capital component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    value, resolved_instance, mode = _launch_instance_component_named_status(
        owner, launch_id, "capital", instance
    )
    _emit(
        {
            **value,
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


def _instance_capital_client(owner, launch_id: str, instance: str | None):
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    connections = resolve_instance_connections(instance_workspace)
    clients = InstanceSystemClients.from_connections(connections)
    if clients.capital is None:
        raise typer.BadParameter("launch instance has no connected capital component")
    return clients.capital, resolved_instance, mode


def _run_capital_connected_command(
    owner: Any,
    launch_id: str,
    instance: str | None,
    command: str,
    arguments: list[str],
) -> dict[str, Any]:
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    value = NativeCliApplication(instance_workspace).run(
        "capital",
        [
            "connected",
            command,
            *arguments,
        ],
    )
    value.setdefault("launch_id", launch_id)
    value.setdefault("instance_id", resolved_instance)
    value.setdefault("mode", mode)
    value.setdefault("scope", "launch-instance")
    return value


@instance_component_capital_app.command("health")
def launch_instance_component_capital_health(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital health through its owner contract."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.health(),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("current")
def launch_instance_component_capital_current(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital current-view business facts."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_metadata(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("availabilities")
def launch_instance_component_capital_availabilities(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital availability facts from mmap."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_availabilities(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("objectives")
def launch_instance_component_capital_objectives(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital funding objectives from mmap."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_objectives(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("demands")
def launch_instance_component_capital_demands(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital demands from mmap."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_demands(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("plans")
def launch_instance_component_capital_plans(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital plans from mmap."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_plans(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("routes")
def launch_instance_component_capital_routes(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital routes from mmap."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_routes(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("reservations")
def launch_instance_component_capital_reservations(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital reservations from mmap."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_reservations(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("operations")
def launch_instance_component_capital_operations(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital operations from mmap."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_operations(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("alerts")
def launch_instance_component_capital_alerts(
    launch_id: str,
    capital_group_id: str = typer.Option(..., "--capital-group-id"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read launch-scoped Capital recovery alerts from mmap."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_capital_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.current_alerts(capital_group_id),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_capital_app.command("publish-funding-objective")
def launch_instance_component_capital_publish_funding_objective(
    launch_id: str,
    file: Path = typer.Option(..., "--file"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Publish a launch-scoped Capital funding objective."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_capital_connected_command(
            owner,
            launch_id,
            instance,
            "publish-funding-objective",
            ["--file", str(file)],
        ),
        output,
    )


@instance_component_capital_app.command("observe-demand")
def launch_instance_component_capital_observe_demand(
    launch_id: str,
    file: Path = typer.Option(..., "--file"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Observe a launch-scoped Capital demand."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_capital_connected_command(
            owner, launch_id, instance, "observe-demand", ["--file", str(file)]
        ),
        output,
    )


@instance_component_capital_app.command("cancel-funding-objective")
def launch_instance_component_capital_cancel_funding_objective(
    launch_id: str,
    file: Path = typer.Option(..., "--file"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Cancel a launch-scoped Capital funding objective."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_capital_connected_command(
            owner,
            launch_id,
            instance,
            "cancel-funding-objective",
            ["--file", str(file)],
        ),
        output,
    )


@instance_component_capital_app.command("reconcile-plan")
def launch_instance_component_capital_reconcile_plan(
    launch_id: str,
    file: Path = typer.Option(..., "--file"),
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Reconcile a launch-scoped Capital plan."""
    owner = WorkspaceApplication().open(workspace)
    _emit(
        _run_capital_connected_command(
            owner, launch_id, instance, "reconcile-plan", ["--file", str(file)]
        ),
        output,
    )


def _instance_reference_client(owner, launch_id: str, instance: str | None):
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    connections = resolve_instance_connections(instance_workspace)
    clients = InstanceSystemClients.from_connections(connections)
    if clients.reference is None:
        raise typer.BadParameter("launch instance has no connected reference component")
    return clients.reference.reader, resolved_instance, mode


@instance_component_reference_app.command("health")
def launch_instance_component_reference_health(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read Reference health selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_reference_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.health(),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_reference_app.command("catalog")
def launch_instance_component_reference_catalog(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.JSON, "--output", "--format"),
) -> None:
    """Read Reference catalog selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    client, resolved_instance, mode = _instance_reference_client(
        owner, launch_id, instance
    )
    _emit(
        {
            **client.catalog(),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


@instance_component_app.command("status")
def launch_instance_component_status(
    component: str,
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Inspect one component selected by a launch instance."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    statuses = LaunchRuntimeApplication(owner).component_status(instance_workspace)
    if component not in statuses:
        raise typer.BadParameter(
            f"launch instance has no connected component named {component}"
        )
    value = statuses[component]
    _emit(
        {
            **value,
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "scope": "launch-instance",
        },
        output,
    )


def _registry_command(action: str):
    def command(
        launch_id: str | None = typer.Argument(None),
        instance: str = typer.Option("default", "--instance"),
        mode: str = typer.Option("paper", "--mode"),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        app = LaunchRegistryApplication(WorkspaceApplication().open(workspace))
        if action in {"list", "browse", "index"}:
            value = (
                app.instances(launch_id)
                if action != "index"
                else {"path": str(app.path), "instances": app.list()}
            )
        elif action == "add":
            if not launch_id:
                raise typer.BadParameter("launch_id is required")
            config_path = Path(launch_id).expanduser()
            if config_path.is_file():
                try:
                    config = LaunchConfigurationApplication().load(
                        config_path, workspace_root=app.workspace.paths.root
                    )
                    config.require_valid()
                except LaunchConfigError as error:
                    raise typer.BadParameter(str(error)) from error
                value = app.add(
                    config.launch_id,
                    mode=config.mode,
                    instance_id=instance,
                    strategy_ref=config.strategy,
                    config_path=config.path,
                )
            else:
                value = app.add(launch_id, mode=mode, instance_id=instance)
        elif action == "remove":
            if not launch_id:
                raise typer.BadParameter("launch_id is required")
            value = app.remove(launch_id, mode=mode, instance_id=instance)
        else:
            value = app.list()
        _emit(value, output)

    command.__name__ = f"launch_target_{action}"
    return command


for _action in ("add", "remove", "index", "list", "browse"):
    targets_app.command(_action)(_registry_command(_action))


def _diagnose(action: str):
    def command(
        launch_id: str,
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        try:
            config_path = _launch_config_path(owner, launch_id)
        except FileNotFoundError as error:
            raise typer.BadParameter(str(error), param_hint="launch_id") from error
        application = LaunchConfigurationApplication()
        try:
            value = (
                application.validate(config_path, workspace_root=owner.paths.root)
                if action == "validate"
                else application.explain(config_path, workspace_root=owner.paths.root)
            )
        except LaunchConfigError as error:
            raise typer.BadParameter(str(error)) from error
        _emit(value, output)

    command.__name__ = f"launch_diagnose_{action}"
    return command


for _action in ("validate", "explain"):
    diagnose_app.command(_action)(_diagnose(_action))


@launch_app.command("instances", help="List current and historical launch instances.")
def instances(
    launch_id: str | None = typer.Argument(None),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(
        LaunchRegistryApplication(WorkspaceApplication().open(workspace)).instances(
            launch_id
        ),
        output,
    )


@launch_app.command("attach", help="Follow launch status and recent strategy output.")
def attach(
    launch_id: str,
    lines: int = typer.Option(
        100, "--lines", min=0, help="Number of recent strategy log lines to show."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    python: bool = typer.Option(
        False,
        "--python",
        help="Open a Python console routed to the running Strategy on this launch.",
    ),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    active = _running_instance(owner, launch_id)
    if active is None:
        raise typer.BadParameter(f"launch is not running: {launch_id}")
    instance = str(active["instance_id"])
    mode = str(active.get("mode") or "paper")
    target = _target(launch_id, instance, mode, workspace)
    log_path = owner.instance(mode, launch_id, instance).log("strategy", "process.log")
    if python:
        if effective_output(output) is not OutputFormat.TEXT:
            raise typer.BadParameter("--python requires text output")
        from kairospy.surface.workbench import KairosWorkbenchApp, load_workbench_state

        state = load_workbench_state(Path(owner.paths.root))
        KairosWorkbenchApp(state, initial_launch_attach=launch_id).run()
        return
    value = _decorate_launch_status(
        owner,
        launch_id,
        instance,
        mode,
        LaunchControlApplication(owner).status(target),
    )
    log_lines = (
        log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        if log_path.is_file() and lines
        else []
    )
    structured_logs = []
    for line in log_lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            record = {"message": line, "structured": False}
        structured_logs.append(record)
    value.update(
        {
            "socket": str(target.socket_path),
            "mode": mode,
            "launch_id": launch_id,
            "instance_id": instance,
            "stdout_log": str(log_path),
            "stdout": log_lines,
            "logs": structured_logs,
        }
    )
    _emit(value, output)


@launch_app.command("logs", help="Read or follow strategy logs for a launch.")
def logs(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    lines: int = typer.Option(
        100, "--lines", min=0, help="Number of recent log lines to show."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    follow: bool = typer.Option(
        False, "-f", "--follow", help="Follow the selected log file."
    ),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    if follow and effective_output(output) is not OutputFormat.TEXT:
        raise typer.BadParameter("--follow currently supports text output only")
    root = owner.instance(mode, launch_id, instance).root / "logs"
    files = (
        sorted(path for path in root.rglob("*") if path.is_file())
        if root.is_dir()
        else []
    )
    payload = {
        "path": str(root),
        "exists": root.exists(),
        "files": [str(path) for path in files],
    }
    if files:
        strategy_log = root / "strategy" / "process.log"
        latest = strategy_log if strategy_log.is_file() else files[-1]
        payload["latest"] = str(latest)
        content = latest.read_text(encoding="utf-8", errors="replace").splitlines()
        payload["lines"] = content[-lines:] if lines else []
    _emit(payload, output)
    if follow and files:
        position = latest.stat().st_size
        while True:
            try:
                with latest.open("r", encoding="utf-8", errors="replace") as stream:
                    stream.seek(position)
                    for line in stream:
                        typer.echo(line.rstrip("\n"), color=False)
                    position = stream.tell()
                time.sleep(0.25)
            except KeyboardInterrupt:
                return


@launch_app.command("artifacts", help="List files produced by a launch instance.")
def artifacts(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    root = owner.paths.launches / mode / launch_id / "instances" / resolved_instance
    _emit(
        {
            "path": str(root),
            "exists": root.exists(),
            "files": [str(path) for path in root.rglob("*")] if root.is_dir() else [],
        },
        output,
    )


@replay_app.command("events")
def replay_events(
    file: Path = typer.Option(..., "--file"),
    limit: int | None = typer.Option(None, "--limit"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(read_replay_events(file, limit=limit), output)


def _timeline_instance(owner, launch_id: str, instance_id: str):
    entries = [
        entry
        for entry in LaunchRegistryApplication(owner).instances(launch_id)
        if entry.get("instance_id") == instance_id
    ]
    if not entries:
        raise typer.BadParameter(
            f"launch instance is not registered: {launch_id}/{instance_id}"
        )
    if len(entries) > 1:
        raise typer.BadParameter(
            f"launch instance {launch_id}/{instance_id} exists in multiple modes"
        )
    mode = str(entries[0].get("mode") or "")
    if not mode:
        raise typer.BadParameter("registered launch instance has no mode")
    return owner.instance(mode, launch_id, instance_id)


@instance_timeline_app.command("list")
def launch_instance_timeline_list(
    launch_id: str = typer.Argument(..., help="Launch id."),
    instance_id: str = typer.Argument(..., help="Launch instance id."),
    limit: int | None = typer.Option(None, "--limit"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    try:
        instance = _timeline_instance(owner, launch_id, instance_id)
        records = LaunchInstanceTimelineApplication(instance).list(limit=limit)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        raise typer.BadParameter(str(error)) from error
    _emit(records, output)


@instance_timeline_app.command("export")
def launch_instance_timeline_export(
    launch_id: str = typer.Argument(..., help="Launch id."),
    instance_id: str = typer.Argument(..., help="Launch instance id."),
    destination: Path = typer.Option(
        ..., "--destination", "--output-file", help="Export file path."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    try:
        instance = _timeline_instance(owner, launch_id, instance_id)
        exported = LaunchInstanceTimelineApplication(instance).export(destination)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        raise typer.BadParameter(str(error)) from error
    _emit(
        {
            "launch_id": launch_id,
            "mode": instance.mode,
            "instance_id": instance_id,
            "destination": str(exported),
        },
        output,
    )
