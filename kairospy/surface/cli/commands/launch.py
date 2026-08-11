from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Mapping

import typer

from kairospy.application.market import materialize_replay_file
from kairospy.application.launch.application import (
    LaunchConfigError,
    LaunchConfigurationApplication,
    LaunchControlApplication,
    LaunchRegistryApplication,
    new_instance_id,
)
from kairospy.application.strategy import StrategyProcessApplication
from kairospy.application.system import (
    ComponentProcessApplication,
    ReferenceProcessConfig,
)
from kairospy.application.timeline import TimelineApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.application.account import AccountAdminApplication, TradeLeaseApplication
from kairospy.surface.cli.options import OutputFormat, effective_output, render


launch_app = typer.Typer(no_args_is_help=True, help="Manage launch instances")
strategy_app = typer.Typer(
    no_args_is_help=True, help="Manage the strategy inside a launch instance"
)
launch_app.add_typer(strategy_app, name="strategy")


def _group(name: str, commands: tuple[str, ...]) -> typer.Typer:
    descriptions = {
        "targets": "Manage reusable launch targets.",
        "diagnose": "Validate and explain launch configuration.",
        "replay": "Inspect replay input and progress.",
        "timeline": "Inspect events emitted by a launch.",
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
launch_timeline_app = _group("timeline", ("list",))


def _target(launch_id: str, instance: str, mode: str, workspace: Path):
    value = WorkspaceApplication().open(workspace)
    return LaunchControlApplication(value).target(launch_id, instance, mode=mode)


def _running_instance(owner, launch_id: str, mode: str | None = None) -> dict | None:
    """Return the one live instance for a launch, if one is reachable."""
    control = LaunchControlApplication(owner)
    entries = LaunchRegistryApplication(owner).instances(launch_id)
    running: list[dict] = []
    for entry in reversed(entries):
        if mode is not None and entry.get("mode") != mode:
            continue
        instance = str(entry.get("instance_id") or "")
        if not instance:
            continue
        entry_mode = str(entry.get("mode") or mode or "paper")
        status = control.status(control.target(launch_id, instance, mode=entry_mode))
        if status.get("status") != "not_running":
            running.append({**entry, **status})
    if mode is None and len(running) > 1:
        raise typer.BadParameter(
            f"launch {launch_id} has multiple running instances; pass --instance"
        )
    if running:
        return running[0]
    return None


def _resolve_launch_target(
    owner,
    launch_id: str,
    mode: str | None,
    instance: str | None,
) -> tuple[str, str]:
    """Resolve the user-facing launch target without requiring identity flags."""
    if instance and mode:
        return instance, mode
    active = _running_instance(owner, launch_id, mode)
    if active is not None:
        return str(active["instance_id"]), str(active.get("mode") or mode or "paper")
    entries = LaunchRegistryApplication(owner).instances(launch_id)
    matching = [
        entry
        for entry in entries
        if (mode is None or entry.get("mode") == mode)
        and (instance is None or entry.get("instance_id") == instance)
    ]
    if matching:
        entry = matching[-1]
        return str(entry.get("instance_id") or instance or "default"), str(
            entry.get("mode") or mode or "paper"
        )
    return instance or "default", mode or "paper"


def _resolve_instance(owner, launch_id: str, mode: str, instance: str | None) -> str:
    """Backward-compatible instance-only resolver for callers with a mode."""
    return _resolve_launch_target(owner, launch_id, mode, instance)[0]


def _read_component_manifest(instance_workspace) -> dict:
    try:
        value = json.loads(
            instance_workspace.component_manifest().read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _launch_component_status(owner, instance_workspace) -> dict[str, dict]:
    """Return component health for one launch without exposing service internals."""
    components = ComponentProcessApplication(owner)
    manifest = _read_component_manifest(instance_workspace)
    endpoints = manifest.get("components", {})
    result: dict[str, dict] = {}

    reference = endpoints.get("reference", {}) if isinstance(endpoints, dict) else {}
    reference_required = not (
        isinstance(reference, dict) and reference.get("required") is False
    )
    if reference_required:
        result["reference"] = components.status("reference")
    market = endpoints.get("market", {}) if isinstance(endpoints, dict) else {}
    market_socket = str(market.get("socket") or "") if isinstance(market, dict) else ""
    instance_market = market_socket == str(instance_workspace.socket("market"))
    result["market"] = components.status(
        "market",
        instance_workspace=instance_workspace if instance_market else None,
    )
    for name in ("risk", "execution"):
        result[name] = components.status(name, instance_workspace=instance_workspace)

    accounts = manifest.get("accounts", {})
    if isinstance(accounts, dict):
        for account_id, value in accounts.items():
            socket_name = value.get("socket_name") if isinstance(value, dict) else None
            key = f"account:{account_id}"
            result[key] = components.status(
                "account",
                instance_workspace=instance_workspace,
                socket_name=str(socket_name) if socket_name else None,
            )
    return result


def _decorate_launch_status(
    owner, launch_id: str, instance: str, mode: str, value: dict
) -> dict:
    """Add an aggregate launch view while preserving the strategy status fields."""
    instance_workspace = owner.instance(mode, launch_id, instance)
    component_status = _launch_component_status(owner, instance_workspace)
    strategy_status = value.get("status", "not_running")
    unhealthy = {
        name: status.get("status")
        for name, status in component_status.items()
        if status.get("status") not in {"ready", "running", "ok"}
    }
    all_stopped = all(
        status.get("status") in {"not_running", "stale"}
        for status in component_status.values()
    )
    aggregate = (
        "healthy"
        if strategy_status in {"ready", "running"} and not unhealthy
        else "degraded"
    )
    if strategy_status == "not_running" and all_stopped:
        aggregate = "not_running"
    return {
        **value,
        "launch_id": launch_id,
        "instance_id": instance,
        "mode": mode,
        "strategy_status": strategy_status,
        "launch_status": aggregate,
        "component_status": component_status,
        "component_issues": unhealthy,
    }


def _resolve_stop_instance(
    owner,
    launch_id: str,
    instance: str | None,
    mode: str | None,
) -> tuple[str, str]:
    """Resolve the instance targeted by ``launch stop``."""
    if instance is not None and mode is not None:
        return instance, mode

    entries = LaunchRegistryApplication(owner).instances(launch_id)
    if mode is not None:
        entries = [entry for entry in entries if entry.get("mode") == mode]
    if instance is not None:
        entries = [entry for entry in entries if entry.get("instance_id") == instance]

    control = LaunchControlApplication(owner)
    running: list[dict] = []
    for entry in entries:
        entry_instance = str(entry.get("instance_id") or "")
        entry_mode = str(entry.get("mode") or "")
        if not entry_instance or not entry_mode:
            continue
        status = control.status(
            control.target(launch_id, entry_instance, mode=entry_mode)
        )
        if status.get("status") != "not_running":
            running.append({**entry, **status})

    if len(running) > 1:
        raise typer.BadParameter(
            f"launch {launch_id} has multiple running instances; pass --instance"
        )
    if running:
        return str(running[0]["instance_id"]), str(running[0]["mode"])

    # An explicit identity component may still identify a stopped registry entry.
    if instance is not None or mode is not None:
        if len(entries) == 1:
            entry = entries[0]
            return str(entry["instance_id"]), str(entry["mode"])
        if not entries:
            raise typer.BadParameter(f"launch instance is not registered: {launch_id}")
        raise typer.BadParameter(
            f"launch {launch_id} has multiple matching instances; pass --instance"
        )

    raise typer.BadParameter(f"launch is not running: {launch_id}")


def _emit(value: object, output: OutputFormat) -> None:
    typer.echo(render(value, output))


def _requires_reference_runtime(mode: str, market_provider: str | None) -> bool:
    """Return whether this launch needs the shared Reference process.

    A deterministic static replay can construct its Market descriptor from the
    explicit subscription request. Requiring Reference in that case adds a
    networked catalog and Aeron driver to an otherwise offline backtest.
    """

    return not (mode == "backtest" and market_provider == "replay")


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


def _acquire_launch_leases(
    workspace, account_ids: list[str], *, launch_id: str, instance: str, mode: str
) -> None:
    if mode == "live" and not account_ids:
        raise typer.BadParameter("live launch requires at least one --account-id")
    accounts = AccountAdminApplication(workspace)
    leases = TradeLeaseApplication(workspace)
    acquired: list[tuple[str, str]] = []
    try:
        for account_id in account_ids:
            account = accounts.show(account_id)
            if mode == "live" and account.get("environment") not in {"live", "testnet"}:
                raise typer.BadParameter(
                    f"account {account_id} is not a live/testnet account"
                )
            broker = str(account.get("broker") or "")
            leases.acquire(
                broker=broker,
                account_id=account_id,
                environment=str(account.get("environment") or mode),
                launch_id=launch_id,
                launch_instance_id=instance,
                mode=mode,
            )
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
        try:
            account = accounts.show(account_id)
        except (FileNotFoundError, ValueError):
            continue
        try:
            leases.release_account(
                str(account.get("broker") or ""),
                account_id,
                launch_instance_id=instance,
            )
        except (FileNotFoundError, ValueError):
            # Cleanup must never replace the original launch exception.
            pass


def _account_component_name(account_id: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", account_id.strip()).strip("-")
    if not value:
        raise ValueError("account id cannot produce an empty component name")
    return f"account-{value}"


def _write_instance_manifest(
    instance_workspace, *, accounts: dict[str, dict], components: dict[str, dict]
) -> None:
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
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(manifest)


def _stop_component_safely(
    components: ComponentProcessApplication,
    component: str,
    *,
    instance_workspace=None,
    socket_name: str | None = None,
) -> dict:
    """Stop one component without allowing stale runtime files to abort cleanup."""
    try:
        return components.stop(
            component,
            instance_workspace=instance_workspace,
            socket_name=socket_name,
        )
    except Exception as error:
        return {
            "component": component,
            "status": "stop_failed",
            "error": str(error),
        }


def _cleanup_instance_components(
    owner,
    instance_workspace,
    account_ids: list[str] | None = None,
    *,
    stop_strategy: bool = True,
    stop_market: bool = False,
) -> dict[str, dict]:
    """Best-effort cleanup for an instance, including partial-start rollback."""
    components = ComponentProcessApplication(owner)
    stopped: dict[str, dict] = {}
    if stop_strategy:
        try:
            value = StrategyProcessApplication(owner).stop(
                instance_workspace.launch_id,
                instance_workspace.instance_id,
                instance_workspace.mode,
            )
            stopped["strategy"] = value
        except Exception as error:
            stopped["strategy"] = {
                "component": "strategy",
                "status": "stop_failed",
                "error": str(error),
            }
    try:
        manifest = json.loads(
            instance_workspace.component_manifest().read_text(encoding="utf-8")
        )
        account_names = [
            str(value.get("socket_name"))
            for value in manifest.get("accounts", {}).values()
            if value.get("socket_name")
        ]
    except (FileNotFoundError, json.JSONDecodeError, AttributeError, TypeError):
        account_names = [
            _account_component_name(value) for value in (account_ids or [])
        ] or ["account"]
    # Execution must quiesce before Risk and Account are torn down.
    for component in ("execution", "risk"):
        stopped[component] = _stop_component_safely(
            components, component, instance_workspace=instance_workspace
        )
    for socket_name in account_names:
        stopped[f"account:{socket_name}"] = _stop_component_safely(
            components,
            "account",
            socket_name=socket_name,
            instance_workspace=instance_workspace,
        )
    if stop_market:
        stopped["market"] = _stop_component_safely(
            components, "market", instance_workspace=instance_workspace
        )
    return stopped


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
        except (FileNotFoundError, ValueError) as error:
            raise typer.BadParameter(str(error), param_hint="launch_id") from error
    if config_path is not None:
        try:
            launch_config = LaunchConfigurationApplication().load(
                config_path, workspace_root=owner.paths.root
            )
            launch_config.require_valid()
        except LaunchConfigError as error:
            raise typer.BadParameter(str(error), param_hint="--config") from error
        positional_config = (
            launch_id is not None and Path(launch_id).expanduser().is_file()
        )
        if (
            launch_id is not None
            and not positional_config
            and launch_id != launch_config.launch_id
        ):
            raise typer.BadParameter("launch id does not match launch config")
        launch_id = launch_config.launch_id
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
    if not strategy:
        raise typer.BadParameter(
            "strategy is required (in launch config or --strategy)"
        )
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
        if config_path is None:
            raise typer.BadParameter("launch configuration path is required")
        launch_environment = LaunchConfigurationApplication().environment(
            config_path, workspace_root=owner.paths.root, instance_id=instance
        )
    registry.update_state(launch_id, mode=mode, instance_id=instance, state="starting")
    try:
        _acquire_launch_leases(
            owner, lease_account_ids, launch_id=launch_id, instance=instance, mode=mode
        )
    except Exception:
        registry.update_state(
            launch_id, mode=mode, instance_id=instance, state="failed"
        )
        raise
        market_instance_workspace = None
    try:
        instance_workspace = owner.instance(mode, launch_id, instance)
        instance_workspace.prepare()
        launch_plan = (
            launch_environment.config.plan() if launch_environment is not None else None
        )
        market_provider = None
        market_credential_id = None
        market_replay_file = None
        if launch_plan is not None:
            if launch_plan.paper_events is not None:
                market_provider, market_replay_file = "replay", launch_plan.paper_events
            elif launch_plan.backtest_replay_file is not None:
                market_provider, market_replay_file = (
                    "replay",
                    launch_plan.backtest_replay_file,
                )
            if market_replay_file is not None:
                market_replay_file = materialize_replay_file(
                    market_replay_file,
                    instance_workspace.state("backtest", "replay.jsonl"),
                    catalog_root=owner.paths.state / "market",
                )
            elif isinstance(launch_plan.mode_config.get("market"), dict):
                market_config = launch_plan.mode_config["market"]
                # Market owns the built-in provider catalog and discovers
                # credentialed products from the Workspace automatically.
                market_provider = market_config.get("provider") or "workspace"
                market_credential_id = market_config.get("credential_id")
            if market_provider is None and mode in {"paper", "live"}:
                market_provider = "workspace"
        execution_config = (
            dict(launch_plan.execution) if launch_plan is not None else {}
        )
        execution_provider = (
            str(execution_config["provider"])
            if execution_config.get("provider") is not None
            else None
        )
        execution_product = (
            str(execution_config["product"])
            if execution_config.get("product") is not None
            else None
        )
        raw_execution_routes = execution_config.get("routes")
        execution_routes: list[Mapping[str, Any]] | None = None
        if raw_execution_routes is not None:
            if not isinstance(raw_execution_routes, list) or not all(
                isinstance(route, Mapping) for route in raw_execution_routes
            ):
                raise ValueError("execution.routes must be an array of route tables")
            execution_routes = [dict(route) for route in raw_execution_routes]
        confirm_live = mode == "live" and bool(
            launch_plan is not None
            and launch_plan.live_safety
            and launch_plan.live_safety.get("trading_enabled")
        )
        account_records = {
            account_id: AccountAdminApplication(owner).show(account_id)
            for account_id in lease_account_ids
        }
        # A process can use either the Workspace shared Market or its own
        # instance Market. Live defaults to shared; replay/backtest default to
        # instance, while launch.market.scope can override that choice.
        market_instance_workspace = (
            instance_workspace
            if launch_plan is not None and launch_plan.market_scope == "instance"
            else None
        )
        # Reference is a Workspace-global catalog runtime. Its source registry
        # is built into Reference; it must not depend on Market configuration.
        reference_required = _requires_reference_runtime(mode, market_provider)
        if reference_required:
            ComponentProcessApplication(owner).ensure_running(
                "reference", reference_config=ReferenceProcessConfig(owner)
            )
        ComponentProcessApplication(owner).ensure_running(
            "market",
            market_provider=market_provider,
            market_replay_file=market_replay_file,
            market_credential_id=market_credential_id,
            instance_workspace=market_instance_workspace,
        )
        components = ComponentProcessApplication(owner)
        account_endpoints: dict[str, dict] = {}
        for bound_account_id in lease_account_ids:
            socket_name = _account_component_name(bound_account_id)
            account_provider = str(
                account_records[bound_account_id].get("broker") or "binance"
            )
            components.ensure_running(
                "account",
                account_id=bound_account_id,
                socket_name=socket_name,
                provider=account_provider,
                instance_workspace=instance_workspace,
            )
            account_endpoints[bound_account_id] = {
                "socket": str(instance_workspace.socket(socket_name)),
                "health": str(instance_workspace.health(socket_name)),
                "socket_name": socket_name,
            }
        # Account, Execution and Risk are all instance-owned runtime actors.
        # Risk is started even without an account binding so a strategy cannot
        # accidentally bypass the instance risk boundary.
        components.ensure_running("risk", instance_workspace=instance_workspace)
        component_endpoints = {
            "risk": {
                "socket": str(instance_workspace.socket("risk")),
                "health": str(instance_workspace.health("risk")),
            },
            "market": {
                "socket": str(instance_workspace.socket("market"))
                if market_instance_workspace is not None
                else str(owner.paths.process_socket("market")),
                "health": str(instance_workspace.health("market"))
                if market_instance_workspace is not None
                else str(owner.paths.health_file("market")),
            },
        }
        component_endpoints["reference"] = (
            {
                "socket": str(owner.paths.process_socket("reference")),
                "health": str(owner.paths.health_file("reference")),
                "required": True,
            }
            if reference_required
            else {"required": False}
        )
        # Execution reads this manifest during its own construction, so the
        # dependency endpoints must already be present before it starts.
        _write_instance_manifest(
            instance_workspace,
            accounts=account_endpoints,
            components=component_endpoints,
        )
        components.ensure_running(
            "execution",
            provider=execution_provider,
            product=execution_product,
            execution_routes=execution_routes,
            confirm_live=confirm_live,
            instance_workspace=instance_workspace,
        )
        component_endpoints["execution"] = {
            "socket": str(instance_workspace.socket("execution")),
            "health": str(instance_workspace.health("execution")),
        }
        _write_instance_manifest(
            instance_workspace,
            accounts=account_endpoints,
            components=component_endpoints,
        )
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
            strategy,
            launch_id=launch_id,
            instance_id=instance,
            mode=mode,
            params=strategy_params,
            environment=launch_environment.process_environment
            if launch_environment is not None
            else None,
        )
        control = LaunchControlApplication(owner)
        target = _target(launch_id, instance, mode, workspace)
        started = control.start(target)
        if started.get("status") == "ready":
            started = control.strategy_control(target, "enable")
        started.update(
            {
                "launch_id": launch_id,
                "mode": mode,
                "instance_id": instance,
                "next_action": (
                    f"kairos launch wait {launch_id}"
                    if mode == "backtest"
                    else f"kairos launch status {launch_id}"
                ),
            }
        )
        registry.update_state(
            launch_id,
            mode=mode,
            instance_id=instance,
            state=str(started.get("status") or "running"),
        )
        _emit(started, output)
    except Exception:
        try:
            registry.update_state(
                launch_id, mode=mode, instance_id=instance, state="failed"
            )
        except FileNotFoundError:
            pass
        try:
            _cleanup_instance_components(
                owner,
                owner.instance(mode, launch_id, instance),
                lease_account_ids,
                stop_strategy=True,
                stop_market=market_instance_workspace is not None,
            )
        finally:
            _release_launch_leases(owner, lease_account_ids, instance=instance)
        raise


@launch_app.command("status", help="Show aggregate strategy and dependency health.")
def status(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    instance, mode = _resolve_launch_target(owner, launch_id, None, instance)
    value = LaunchControlApplication(owner).status(
        _target(launch_id, instance, mode, workspace)
    )
    _emit(_decorate_launch_status(owner, launch_id, instance, mode, value), output)


@launch_app.command("report")
def report(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Read the immutable report emitted when a backtest replay completes."""
    owner = WorkspaceApplication().open(workspace)
    resolved_instance, mode = _resolve_launch_target(
        owner, launch_id, "backtest", instance
    )
    if mode != "backtest":
        raise typer.BadParameter(
            "launch report is only available for backtest launches"
        )
    path = owner.instance(mode, launch_id, resolved_instance).state(
        "backtest", "report.json"
    )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise typer.BadParameter(
            f"backtest report is not available: {path}. "
            f"Run 'kairos launch status {launch_id}' to check progress, then "
            f"'kairos launch wait {launch_id}'."
        ) from error
    except json.JSONDecodeError as error:
        raise typer.BadParameter(f"backtest report is invalid: {path}") from error
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
    resolved_instance, mode = _resolve_launch_target(
        owner, launch_id, "backtest", instance
    )
    if mode != "backtest":
        raise typer.BadParameter("launch wait is only available for backtest launches")
    target = _target(launch_id, resolved_instance, mode, workspace)
    deadline = time.monotonic() + timeout
    value: dict = {}
    while time.monotonic() < deadline:
        value = LaunchControlApplication(owner).status(target)
        if value.get("status") in {"not_running", "stopped", "failed"}:
            break
        time.sleep(0.1)
    else:
        raise typer.BadParameter(
            f"backtest did not finish within {timeout:g}s. "
            f"Run 'kairos launch status {launch_id}' or "
            f"'kairos launch logs {launch_id}' before retrying wait."
        )

    instance_workspace = owner.instance(mode, launch_id, resolved_instance)
    stopped = _cleanup_instance_components(
        owner,
        instance_workspace,
        [],
        stop_strategy=False,
        stop_market=True,
    )
    state = (
        "completed" if value.get("status") in {"not_running", "stopped"} else "failed"
    )
    LaunchRegistryApplication(owner).update_state(
        launch_id,
        mode=mode,
        instance_id=resolved_instance,
        state=state,
    )
    report_path = instance_workspace.state("backtest", "report.json")
    report_value = None
    if report_path.is_file():
        report_value = json.loads(report_path.read_text(encoding="utf-8"))
    _emit(
        {
            "status": state,
            "report": report_value,
            "stopped": stopped,
            "next_action": (
                f"kairos launch report {launch_id}"
                if state == "completed"
                else f"kairos launch logs {launch_id}"
            ),
        },
        output,
    )


@launch_app.command("stop", help="Stop a launch and release its runtime resources.")
def stop(
    launch_id: str,
    instance: str | None = typer.Option(None, "--instance"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    instance, mode = _resolve_stop_instance(owner, launch_id, instance, None)
    instance_workspace = owner.instance(mode, launch_id, instance)
    components = ComponentProcessApplication(owner)
    manifest_accounts: list[str] = []
    account_ids: list[str] = []
    try:
        manifest = json.loads(
            instance_workspace.component_manifest().read_text(encoding="utf-8")
        )
        accounts = manifest.get("accounts", {})
        if isinstance(accounts, dict):
            account_ids = [str(value) for value in accounts]
            manifest_accounts = [
                str(value.get("socket_name"))
                for value in accounts.values()
                if isinstance(value, dict) and value.get("socket_name")
            ]
    except (FileNotFoundError, json.JSONDecodeError, AttributeError, TypeError):
        pass

    stopped: dict[str, dict] = {}
    try:
        stopped["strategy"] = LaunchControlApplication(owner).stop(
            _target(launch_id, instance, mode, workspace)
        )
    except Exception as error:
        stopped["strategy"] = {
            "component": "strategy",
            "status": "stop_failed",
            "error": str(error),
        }

    for component in ("execution", "risk"):
        stopped[component] = _stop_component_safely(
            components, component, instance_workspace=instance_workspace
        )
    for socket_name in manifest_accounts or ["account"]:
        stopped[f"account:{socket_name}"] = _stop_component_safely(
            components,
            "account",
            socket_name=socket_name,
            instance_workspace=instance_workspace,
        )
    # The live Market is shared by launches and must not be stopped when one
    # launch instance exits. Instance-owned replay Market can be stopped here.
    market_shared = mode == "live"
    for entry in LaunchRegistryApplication(owner).list():
        if (
            entry.get("launch_id") == launch_id
            and entry.get("mode") == mode
            and entry.get("instance_id") == instance
        ):
            config_value = entry.get("config")
            if isinstance(config_value, str) and Path(config_value).is_file():
                try:
                    market_shared = (
                        LaunchConfigurationApplication()
                        .load(config_value, workspace_root=owner.paths.root)
                        .plan()
                        .market_scope
                        == "shared"
                    )
                except (FileNotFoundError, LaunchConfigError, ValueError):
                    pass
            break
    if not market_shared:
        stopped["market"] = _stop_component_safely(
            components, "market", instance_workspace=instance_workspace
        )

    # Cleanup is intentionally idempotent: a failed stop request must not keep
    # the account lease or leave the registry in a running state.
    if not account_ids:
        for entry in LaunchRegistryApplication(owner).list():
            if (
                entry.get("launch_id") == launch_id
                and entry.get("mode") == mode
                and entry.get("instance_id") == instance
            ):
                config_value = entry.get("config")
                if isinstance(config_value, str) and Path(config_value).is_file():
                    try:
                        account_ids = list(
                            LaunchConfigurationApplication()
                            .load(config_value, workspace_root=owner.paths.root)
                            .account_refs
                        )
                    except (FileNotFoundError, LaunchConfigError, ValueError):
                        pass
                break
    _release_launch_leases(owner, account_ids, instance=instance)
    try:
        LaunchRegistryApplication(owner).update_state(
            launch_id, mode=mode, instance_id=instance, state="stopped"
        )
    except FileNotFoundError:
        pass
    issues = {
        name: result.get("error")
        for name, result in stopped.items()
        if result.get("status") == "stop_failed"
    }
    value = {
        **stopped.get("strategy", {}),
        "launch_id": launch_id,
        "instance_id": instance,
        "mode": mode,
        "status": "stopped" if not issues else "degraded",
        "stopped_components": stopped,
        "stop_issues": issues,
        "next_action": (
            f"kairos launch start {launch_id}"
            if not issues
            else f"kairos launch logs {launch_id}"
        ),
    }
    _emit(value, output)


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
) -> None:
    owner = WorkspaceApplication().open(workspace)
    active = _running_instance(owner, launch_id)
    if active is None:
        raise typer.BadParameter(f"launch is not running: {launch_id}")
    instance = str(active["instance_id"])
    mode = str(active.get("mode") or "paper")
    target = _target(launch_id, instance, mode, workspace)
    value = _decorate_launch_status(
        owner,
        launch_id,
        instance,
        mode,
        LaunchControlApplication(owner).status(target),
    )
    log_path = owner.instance(mode, launch_id, instance).log("strategy.log")
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
        strategy_log = root / "strategy.log"
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
    _emit(TimelineApplication().list(file, limit=limit), output)


@launch_timeline_app.command("list")
def launch_timeline_list(
    file: Path = typer.Option(..., "--file"),
    limit: int | None = typer.Option(None, "--limit"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(TimelineApplication().list(file, limit=limit), output)
