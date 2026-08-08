from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import typer

from kairospy.application.launch.application import (
    LaunchConfigError,
    LaunchConfigurationApplication,
    LaunchControlApplication,
    LaunchRegistryApplication,
    new_instance_id,
)
from kairospy.application.strategy import StrategyProcessApplication
from kairospy.application.system import ComponentProcessApplication, ReferenceProcessConfig
from kairospy.application.timeline import TimelineApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.application.account import AccountAdminApplication, TradeLeaseApplication
from kairospy.surface.cli.options import OutputFormat, render


launch_app = typer.Typer(no_args_is_help=True, help="Manage launch instances")
strategy_app = typer.Typer(no_args_is_help=True, help="Manage the strategy inside a launch instance")
launch_app.add_typer(strategy_app, name="strategy")


def _group(name: str, commands: tuple[str, ...]) -> typer.Typer:
    group = typer.Typer(no_args_is_help=True, help=f"Launch {name} commands")
    launch_app.add_typer(group, name=name)
    del commands
    return group


targets_app = _group("targets", ("add", "remove", "index", "list", "browse"))
diagnose_app = _group("diagnose", ("validate", "explain"))
replay_app = _group("replay", ("events",))
launch_timeline_app = _group("timeline", ("list",))


def _target(launch_id: str, instance: str, mode: str, workspace: Path):
    value = WorkspaceApplication().open(workspace)
    return LaunchControlApplication(value).target(launch_id, instance, mode=mode)


def _running_instance(owner, launch_id: str, mode: str) -> dict | None:
    """Return the one live instance for a launch, if one is reachable."""
    control = LaunchControlApplication(owner)
    entries = LaunchRegistryApplication(owner).instances(launch_id)
    for entry in reversed(entries):
        if entry.get("mode") != mode:
            continue
        instance = str(entry.get("instance_id") or "")
        if not instance:
            continue
        status = control.status(control.target(launch_id, instance, mode=mode))
        if status.get("status") != "not_running":
            return {**entry, **status}
    return None


def _emit(value: object, output: OutputFormat) -> None:
    typer.echo(render(value, output))


def _launch_config_path(owner, target: str | Path) -> Path:
    candidate = Path(target).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    if candidate.is_file():
        return candidate
    configured = owner.paths.launch_config(str(target))
    if configured.is_file():
        return configured
    raise FileNotFoundError(f"launch config does not exist: {candidate}")


def _acquire_launch_leases(workspace, account_ids: list[str], *, launch_id: str, instance: str, mode: str) -> None:
    if mode == "live" and not account_ids:
        raise typer.BadParameter("live launch requires at least one --account-id")
    accounts = AccountAdminApplication(workspace)
    leases = TradeLeaseApplication(workspace)
    acquired: list[tuple[str, str]] = []
    try:
        for account_id in account_ids:
            account = accounts.show(account_id)
            if mode == "live" and account.get("environment") not in {"live", "testnet"}:
                raise typer.BadParameter(f"account {account_id} is not a live/testnet account")
            broker = str(account.get("broker") or "")
            leases.acquire(broker=broker, account_id=account_id, environment=str(account.get("environment") or mode), launch_id=launch_id, launch_instance_id=instance, mode=mode)
            acquired.append((broker, account_id))
    except Exception:
        for broker, account_id in reversed(acquired):
            try:
                leases.release(f"{broker}.{account_id}", launch_instance_id=instance)
            except (FileNotFoundError, ValueError):
                pass
        raise


def _release_launch_leases(workspace, account_ids: list[str], *, instance: str) -> None:
    accounts = AccountAdminApplication(workspace)
    leases = TradeLeaseApplication(workspace)
    for account_id in account_ids:
        account = accounts.show(account_id)
        try:
            leases.release_account(
                str(account.get("broker") or ""),
                account_id,
                launch_instance_id=instance,
            )
        except (FileNotFoundError, ValueError):
            # Cleanup must never replace the original launch exception.
            pass


def _workspace_has_market_connections(workspace) -> bool:
    """Return whether the workspace opted into an explicit market catalog."""
    try:
        values = tomllib.loads(workspace.paths.manifest.read_text(encoding="utf-8"))
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return False
    market = values.get("market")
    connections = market.get("connections") if isinstance(market, dict) else None
    return isinstance(connections, dict) and bool(connections)


def _account_component_name(account_id: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", account_id.strip()).strip("-")
    if not value:
        raise ValueError("account id cannot produce an empty component name")
    return f"account-{value}"


def _write_instance_manifest(instance_workspace, *, accounts: dict[str, dict], components: dict[str, dict]) -> None:
    manifest = instance_workspace.component_manifest()
    manifest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "launch_id": instance_workspace.launch_id,
        "instance_id": instance_workspace.instance_id,
        "mode": instance_workspace.mode,
        "accounts": accounts,
        "components": components,
    }
    temporary = manifest.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(manifest)


def _cleanup_instance_components(owner, instance_workspace, account_ids: list[str] | None = None) -> None:
    components = ComponentProcessApplication(owner)
    try:
        manifest = json.loads(instance_workspace.component_manifest().read_text(encoding="utf-8"))
        account_names = [str(value.get("socket_name")) for value in manifest.get("accounts", {}).values() if value.get("socket_name")]
    except (FileNotFoundError, json.JSONDecodeError, AttributeError, TypeError):
        account_names = [_account_component_name(value) for value in (account_ids or [])] or ["account"]
    for component in ("risk", "execution"):
        try:
            components.stop(component, instance_workspace=instance_workspace)
        except (OSError, RuntimeError, ValueError):
            pass
    for socket_name in account_names:
        try:
            components.stop("account", socket_name=socket_name, instance_workspace=instance_workspace)
        except (OSError, RuntimeError, ValueError):
            pass


@launch_app.command("start")
def start(
    launch_id: str | None = typer.Argument(None),
    mode: str | None = typer.Option(None, "--mode"),
    strategy: str | None = typer.Option(None, "--strategy", help="Strategy import path: module:callable"),
    config: Path | None = typer.Option(None, "--config", help="Launch TOML configuration path."),
    params: str | None = typer.Option(None, "--params", help="JSON object passed to the strategy factory"),
    account_id: list[str] = typer.Option([], "--account-id", help="Account binding to lease; repeatable."),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    instance = new_instance_id()
    launch_config = None
    config_path: Path | None = config
    if config_path is None and launch_id is not None:
        try:
            config_path = _launch_config_path(owner, launch_id)
        except (FileNotFoundError, ValueError):
            config_path = None
    if config_path is not None:
        try:
            launch_config = LaunchConfigurationApplication().load(config_path, workspace_root=owner.paths.root)
            launch_config.require_valid()
        except LaunchConfigError as error:
            raise typer.BadParameter(str(error), param_hint="--config") from error
        positional_config = launch_id is not None and Path(launch_id).expanduser().is_file()
        if launch_id is not None and not positional_config and launch_id != launch_config.launch_id:
            raise typer.BadParameter("launch id does not match launch config")
        launch_id = launch_config.launch_id
        if mode is not None and mode != launch_config.mode:
            raise typer.BadParameter("--mode does not match launch config")
        mode = launch_config.mode
        if strategy is not None and strategy != launch_config.strategy:
            raise typer.BadParameter("--strategy does not match launch config")
        strategy = launch_config.strategy
    if launch_config is None:
        raise typer.BadParameter(
            "launch TOML config is required; pass --config or use config/launches/<launch-id>.toml"
        )
    if not launch_id:
        raise typer.BadParameter("launch_id or --config is required")
    if not mode:
        mode = "paper"
    if not strategy:
        raise typer.BadParameter("strategy is required (in launch config or --strategy)")
    configured_account_ids = list(launch_config.account_refs)
    lease_account_ids = list(dict.fromkeys([*configured_account_ids, *account_id]))
    registry = LaunchRegistryApplication(owner)
    active = _running_instance(owner, launch_id, mode or launch_config.mode)
    if active is not None:
        raise typer.BadParameter(
            f"launch {launch_id} already has a running instance: {active['instance_id']}"
        )
    registry.add(
        launch_id,
        mode=mode,
        instance_id=instance,
        strategy_ref=strategy,
        config_path=config_path if launch_config is not None else None,
    )
    launch_environment = None
    if launch_config is not None:
        launch_environment = LaunchConfigurationApplication().environment(
            config_path, workspace_root=owner.paths.root, instance_id=instance
        )
    registry.update_state(launch_id, mode=mode, instance_id=instance, state="starting")
    try:
        _acquire_launch_leases(owner, lease_account_ids, launch_id=launch_id, instance=instance, mode=mode)
    except Exception:
        registry.update_state(launch_id, mode=mode, instance_id=instance, state="failed")
        raise
    try:
        instance_workspace = owner.instance(mode, launch_id, instance)
        instance_workspace.prepare()
        launch_plan = launch_environment.config.plan() if launch_environment is not None else None
        market_provider = None
        market_credential_id = None
        market_replay_file = None
        has_market_connections = _workspace_has_market_connections(owner)
        if launch_plan is not None:
            if launch_plan.paper_events is not None:
                market_provider, market_replay_file = "replay", launch_plan.paper_events
            elif launch_plan.backtest_replay_file is not None:
                market_provider, market_replay_file = "replay", launch_plan.backtest_replay_file
            elif isinstance(launch_plan.mode_config.get("market"), dict):
                market_config = launch_plan.mode_config["market"]
                # Market loads the complete Workspace connection catalog and
                # creates the matching provider lazily for each strategy
                # subscription. Inline provider fields remain a supported
                # launch-plan input form.
                market_provider = market_config.get("provider") or (
                    "workspace" if has_market_connections else "binance-spot-websocket"
                )
                market_credential_id = market_config.get("credential_id")
            if market_provider is None and mode in {"paper", "live"}:
                market_provider = "workspace" if has_market_connections else "binance-spot-websocket"
        execution_config = dict(launch_plan.execution) if launch_plan is not None else {}
        execution_provider = str(execution_config["provider"]) if execution_config.get("provider") is not None else None
        execution_product = str(execution_config["product"]) if execution_config.get("product") is not None else None
        confirm_live = mode == "live" and bool(
            launch_plan is not None and launch_plan.live_safety and launch_plan.live_safety.get("trading_enabled")
        )
        account_records = {
            account_id: AccountAdminApplication(owner).show(account_id)
            for account_id in lease_account_ids
        }
        # A process can use either the Workspace shared Market or its own
        # instance Market. Live defaults to shared; replay/backtest default to
        # instance, while launch.market.scope can override that choice.
        market_instance_workspace = (
            instance_workspace if launch_plan is not None and launch_plan.market_scope == "instance"
            else None
        )
        # Reference is a Workspace-global catalog runtime. It is started once
        # and is never placed in an instance workspace.
        if has_market_connections:
            ComponentProcessApplication(owner).ensure_running(
                "reference", reference_config=ReferenceProcessConfig(owner, provider="workspace")
            )
        ComponentProcessApplication(owner).ensure_running(
            "market", market_provider=market_provider, market_replay_file=market_replay_file,
            market_credential_id=market_credential_id,
            instance_workspace=market_instance_workspace,
        )
        components = ComponentProcessApplication(owner)
        account_endpoints: dict[str, dict] = {}
        for account_id in lease_account_ids:
            socket_name = _account_component_name(account_id)
            account_provider = str(account_records[account_id].get("broker") or "binance")
            components.ensure_running(
                "account", account_id=account_id, socket_name=socket_name,
                provider=account_provider, instance_workspace=instance_workspace
            )
            account_endpoints[account_id] = {
                "socket": str(instance_workspace.socket(socket_name)),
                "health": str(instance_workspace.health(socket_name)),
                "socket_name": socket_name,
            }
        # Account, Execution and Risk are all instance-owned runtime actors.
        # Risk is started even without an account binding so a strategy cannot
        # accidentally bypass the instance risk boundary.
        components.ensure_running("risk", instance_workspace=instance_workspace)
        component_endpoints = {
            "risk": {"socket": str(instance_workspace.socket("risk")), "health": str(instance_workspace.health("risk"))},
            "market": {
                "socket": str(instance_workspace.socket("market")) if market_instance_workspace is not None else str(owner.paths.process_socket("market")),
                "health": str(instance_workspace.health("market")) if market_instance_workspace is not None else str(owner.paths.health_file("market")),
            },
        }
        if has_market_connections:
            component_endpoints["reference"] = {"socket": str(owner.paths.process_socket("reference")), "health": str(owner.paths.health_file("reference"))}
        # Execution reads this manifest during its own construction, so the
        # dependency endpoints must already be present before it starts.
        _write_instance_manifest(instance_workspace, accounts=account_endpoints, components=component_endpoints)
        components.ensure_running(
            "execution", provider=execution_provider, product=execution_product,
            confirm_live=confirm_live, instance_workspace=instance_workspace
        )
        component_endpoints["execution"] = {
            "socket": str(instance_workspace.socket("execution")),
            "health": str(instance_workspace.health("execution")),
        }
        _write_instance_manifest(instance_workspace, accounts=account_endpoints, components=component_endpoints)
        strategy_params = dict(launch_config.strategy_params)
        if params:
            import json
            try:
                value = json.loads(params)
            except json.JSONDecodeError as error:
                raise typer.BadParameter("--params must be a JSON object") from error
            if not isinstance(value, dict):
                raise typer.BadParameter("--params must be a JSON object")
            strategy_params = {**(strategy_params or {}), **value}
        StrategyProcessApplication(owner).ensure_running(
            strategy, launch_id=launch_id, instance_id=instance, mode=mode,
            params=strategy_params,
            environment=launch_environment.process_environment if launch_environment is not None else None,
        )
        control = LaunchControlApplication(owner)
        target = _target(launch_id, instance, mode, workspace)
        started = control.start(target)
        if started.get("status") == "ready":
            started = control.strategy_control(target, "enable")
        started.update({
            "launch_id": launch_id,
            "mode": mode,
            "instance_id": instance,
        })
        registry.update_state(
            launch_id,
            mode=mode,
            instance_id=instance,
            state=str(started.get("status") or "running"),
        )
        _emit(started, output)
    except Exception:
        try:
            registry.update_state(launch_id, mode=mode, instance_id=instance, state="failed")
        except FileNotFoundError:
            pass
        try:
            _cleanup_instance_components(owner, owner.instance(mode, launch_id, instance), lease_account_ids)
        finally:
            _release_launch_leases(owner, lease_account_ids, instance=instance)
        raise


@launch_app.command("status")
def status(
    launch_id: str,
    instance: str = typer.Option(..., "--instance"),
    mode: str = typer.Option("paper", "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(LaunchControlApplication(WorkspaceApplication().open(workspace)).status(_target(launch_id, instance, mode, workspace)), output)


@launch_app.command("stop")
def stop(
    launch_id: str,
    instance: str = typer.Option(..., "--instance"),
    mode: str = typer.Option("paper", "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    value = LaunchControlApplication(owner).stop(_target(launch_id, instance, mode, workspace))
    instance_workspace = owner.instance(mode, launch_id, instance)
    components = ComponentProcessApplication(owner)
    manifest_accounts: list[str] = []
    try:
        manifest = json.loads(instance_workspace.component_manifest().read_text(encoding="utf-8"))
        manifest_accounts = [str(value.get("socket_name")) for value in manifest.get("accounts", {}).values() if value.get("socket_name")]
    except (FileNotFoundError, json.JSONDecodeError, AttributeError, TypeError):
        pass
    for component in ("risk", "execution"):
        components.stop(component, instance_workspace=instance_workspace)
    for socket_name in manifest_accounts or ["account"]:
        components.stop("account", socket_name=socket_name, instance_workspace=instance_workspace)
    # The live Market is shared by launches and must not be stopped when one
    # launch instance exits. Instance-owned replay Market can be stopped here.
    market_shared = mode == "live"
    for entry in LaunchRegistryApplication(owner).list():
        if entry.get("launch_id") == launch_id and entry.get("mode") == mode and entry.get("instance_id") == instance:
            config_value = entry.get("config")
            if isinstance(config_value, str) and Path(config_value).is_file():
                try:
                    market_shared = LaunchConfigurationApplication().load(
                        config_value, workspace_root=owner.paths.root
                    ).plan().market_scope == "shared"
                except (FileNotFoundError, LaunchConfigError, ValueError):
                    pass
            break
    if not market_shared:
        components.stop("market", instance_workspace=instance_workspace)
    for entry in LaunchRegistryApplication(owner).list():
        if entry.get("launch_id") != launch_id or entry.get("mode") != mode or entry.get("instance_id") != instance:
            continue
        config_value = entry.get("config")
        if isinstance(config_value, str) and Path(config_value).is_file():
            try:
                refs = list(LaunchConfigurationApplication().load(config_value, workspace_root=owner.paths.root).account_refs)
                _release_launch_leases(owner, refs, instance=instance)
            except (FileNotFoundError, LaunchConfigError, ValueError):
                pass
        break
    try:
        LaunchRegistryApplication(owner).update_state(launch_id, mode=mode, instance_id=instance, state="stopped")
    except FileNotFoundError:
        pass
    _emit(value, output)


@strategy_app.command("status")
def strategy_status(
    launch_id: str,
    instance: str = typer.Option(..., "--instance"),
    mode: str = typer.Option("paper", "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(LaunchControlApplication(WorkspaceApplication().open(workspace)).status(_target(launch_id, instance, mode, workspace)), output)


def _strategy_action(action: str):
    def command(
        launch_id: str,
        instance: str = typer.Option(..., "--instance"),
        mode: str = typer.Option("paper", "--mode"),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        target = _target(launch_id, instance, mode, workspace)
        _emit(LaunchControlApplication(WorkspaceApplication().open(workspace)).strategy_control(target, action), output)

    command.__name__ = f"strategy_{action}"
    return command


for _action in ("enable", "pause", "resume", "refresh"):
    strategy_app.command(_action)(_strategy_action(_action))


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
            value = app.instances(launch_id) if action != "index" else {"path": str(app.path), "instances": app.list()}
        elif action == "add":
            if not launch_id: raise typer.BadParameter("launch_id is required")
            config_path = Path(launch_id).expanduser()
            if config_path.is_file():
                try:
                    config = LaunchConfigurationApplication().load(config_path, workspace_root=app.workspace.paths.root)
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
            if not launch_id: raise typer.BadParameter("launch_id is required")
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
        instance: str = typer.Option("default", "--instance"),
        mode: str = typer.Option("paper", "--mode"),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        try:
            config_path = _launch_config_path(owner, launch_id)
        except FileNotFoundError:
            config_path = None
        if config_path is not None:
            application = LaunchConfigurationApplication()
            try:
                value = application.validate(config_path, workspace_root=owner.paths.root) if action == "validate" else application.explain(config_path, workspace_root=owner.paths.root)
            except LaunchConfigError as error:
                raise typer.BadParameter(str(error)) from error
        else:
            value = LaunchRegistryApplication(owner).diagnose(launch_id, mode=mode, instance_id=instance)
            if action == "explain":
                value["explanation"] = "launch instance identity, registry entry and instance-owned control socket"
        _emit(value, output)
    command.__name__ = f"launch_diagnose_{action}"
    return command


for _action in ("validate", "explain"):
    diagnose_app.command(_action)(_diagnose(_action))


@launch_app.command("instances")
def instances(
    launch_id: str | None = typer.Argument(None),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(LaunchRegistryApplication(WorkspaceApplication().open(workspace)).instances(launch_id), output)


@launch_app.command("attach")
def attach(
    launch_id: str,
    mode: str = typer.Option("paper", "--mode"),
    lines: int = typer.Option(100, "--lines", min=0, help="Number of recent strategy log lines to show."),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    active = _running_instance(owner, launch_id, mode)
    if active is None:
        raise typer.BadParameter(f"launch is not running: {launch_id}")
    instance = str(active["instance_id"])
    target = _target(launch_id, instance, mode, workspace)
    value = LaunchControlApplication(owner).status(target)
    log_path = owner.paths.logs / "launches" / mode / launch_id / instance / "strategy.log"
    log_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:] if log_path.is_file() and lines else []
    structured_logs = []
    for line in log_lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            record = {"message": line, "structured": False}
        structured_logs.append(record)
    value.update({
        "socket": str(target.socket_path),
        "mode": mode,
        "launch_id": launch_id,
        "instance_id": instance,
        "stdout_log": str(log_path),
        "stdout": log_lines,
        "logs": structured_logs,
    })
    _emit(value, output)


@launch_app.command("logs")
def logs(
    launch_id: str,
    instance: str = typer.Option("default", "--instance"),
    mode: str = typer.Option("paper", "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    root = owner.paths.logs / "launches" / mode / launch_id / instance
    files = sorted(path for path in root.rglob("*") if path.is_file()) if root.is_dir() else []
    payload = {"path": str(root), "exists": root.exists(), "files": [str(path) for path in files]}
    if files:
        latest = files[-1]
        payload["latest"] = str(latest)
        payload["lines"] = latest.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]
    _emit(payload, output)


@launch_app.command("artifacts")
def artifacts(
    launch_id: str,
    instance: str = typer.Option("default", "--instance"),
    mode: str = typer.Option("paper", "--mode"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    root = owner.paths.launches / mode / launch_id / "instances" / instance
    _emit({"path": str(root), "exists": root.exists(), "files": [str(path) for path in root.rglob("*")] if root.is_dir() else []}, output)


@replay_app.command("events")
def replay_events(
    file: Path = typer.Option(..., "--file"),
    limit: int | None = typer.Option(None, "--limit"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(TimelineApplication().list(file, limit=limit), output)


@launch_timeline_app.command("list")
def launch_timeline_list(
    file: Path = typer.Option(..., "--file"),
    limit: int | None = typer.Option(None, "--limit"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(TimelineApplication().list(file, limit=limit), output)
