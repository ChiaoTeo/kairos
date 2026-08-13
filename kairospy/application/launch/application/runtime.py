"""Shared launch runtime orchestration.

CLI and Python are input/output adapters.  Both enter this application for
instance identity, component composition, lifecycle, cleanup and reporting.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Mapping

from ...account import AccountAdminApplication, TradeLeaseApplication
from ...data import DatasetCatalogApplication, DatasetReaderApplication
from ...market import materialize_replay_file, validate_replay_window
from ...strategy import StrategyProcessApplication
from ...system import ComponentProcessApplication, ReferenceProcessConfig
from ...workspace import Workspace
from ..domain.identity import new_instance_id
from .configuration import (
    LaunchConfig,
    LaunchConfigError,
    LaunchConfigurationApplication,
)
from .control import LaunchControlApplication
from .registry import LaunchRegistryApplication


class LaunchRuntimeError(RuntimeError):
    """Raised when a canonical launch cannot complete its runtime use case."""


def requires_reference_runtime(mode: str, has_static_replay: bool) -> bool:
    """A deterministic replay does not require the networked Reference runtime."""

    return not (mode == "backtest" and has_static_replay)


def account_component_name(account_id: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", account_id.strip()).strip("-")
    if not value:
        raise ValueError("account id cannot produce an empty component name")
    return f"account-{value}"


def acquire_launch_leases(
    workspace: Workspace,
    account_ids: list[str],
    *,
    launch_id: str,
    instance: str,
    mode: str,
) -> None:
    if mode == "live" and not account_ids:
        raise LaunchRuntimeError("live launch requires at least one account")
    accounts = AccountAdminApplication(workspace)
    leases = TradeLeaseApplication(workspace)
    acquired: list[tuple[str, str]] = []
    try:
        for account_id in account_ids:
            account = accounts.show(account_id)
            if mode == "live" and account.get("environment") not in {
                "live",
                "testnet",
            }:
                raise LaunchRuntimeError(
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


def release_launch_leases(
    workspace: Workspace, account_ids: list[str], *, instance: str
) -> None:
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
            pass


def write_instance_manifest(
    instance_workspace: Any,
    *,
    accounts: dict[str, dict[str, Any]],
    components: dict[str, dict[str, Any]],
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


def stop_component_safely(
    components: ComponentProcessApplication,
    component: str,
    *,
    instance_workspace: Any = None,
    socket_name: str | None = None,
) -> dict[str, Any]:
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


def cleanup_instance_components(
    owner: Workspace,
    instance_workspace: Any,
    account_ids: list[str] | None = None,
    *,
    stop_strategy: bool = True,
    stop_market: bool = False,
) -> dict[str, dict[str, Any]]:
    """Best-effort instance cleanup, including partial-start rollback."""

    components = ComponentProcessApplication(owner)
    stopped: dict[str, dict[str, Any]] = {}
    if stop_strategy:
        try:
            stopped["strategy"] = StrategyProcessApplication(owner).stop(
                instance_workspace.launch_id,
                instance_workspace.instance_id,
                instance_workspace.mode,
            )
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
            account_component_name(value) for value in (account_ids or [])
        ] or ["account"]
    for component in ("execution", "risk"):
        stopped[component] = stop_component_safely(
            components, component, instance_workspace=instance_workspace
        )
    for socket_name in account_names:
        stopped[f"account:{socket_name}"] = stop_component_safely(
            components,
            "account",
            socket_name=socket_name,
            instance_workspace=instance_workspace,
        )
    if stop_market:
        stopped["market"] = stop_component_safely(
            components, "market", instance_workspace=instance_workspace
        )
    return stopped


class LaunchRuntimeApplication:
    """The sole runtime path used by launch surfaces."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def _target(self, launch_id: str, instance: str, mode: str):
        return LaunchControlApplication(self.workspace).target(
            launch_id, instance, mode=mode
        )

    def running_instance(
        self, launch_id: str, mode: str | None = None
    ) -> dict[str, Any] | None:
        control = LaunchControlApplication(self.workspace)
        running: list[dict[str, Any]] = []
        for entry in reversed(
            LaunchRegistryApplication(self.workspace).instances(launch_id)
        ):
            if mode is not None and entry.get("mode") != mode:
                continue
            instance = str(entry.get("instance_id") or "")
            if not instance:
                continue
            entry_mode = str(entry.get("mode") or mode or "paper")
            status = control.status(
                control.target(launch_id, instance, mode=entry_mode)
            )
            if status.get("status") != "not_running":
                running.append({**entry, **status})
        if mode is None and len(running) > 1:
            raise LaunchRuntimeError(
                f"launch {launch_id} has multiple running instances; specify instance"
            )
        return running[0] if running else None

    def resolve_target(
        self,
        launch_id: str,
        mode: str | None = None,
        instance: str | None = None,
    ) -> tuple[str, str]:
        if instance and mode:
            return instance, mode
        active = self.running_instance(launch_id, mode)
        if active is not None:
            return str(active["instance_id"]), str(
                active.get("mode") or mode or "paper"
            )
        entries = LaunchRegistryApplication(self.workspace).instances(launch_id)
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

    def resolve_stop_target(
        self,
        launch_id: str,
        instance: str | None = None,
        mode: str | None = None,
    ) -> tuple[str, str]:
        if instance is not None and mode is not None:
            return instance, mode
        entries = LaunchRegistryApplication(self.workspace).instances(launch_id)
        if mode is not None:
            entries = [entry for entry in entries if entry.get("mode") == mode]
        if instance is not None:
            entries = [
                entry for entry in entries if entry.get("instance_id") == instance
            ]
        control = LaunchControlApplication(self.workspace)
        running: list[dict[str, Any]] = []
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
            raise LaunchRuntimeError(
                f"launch {launch_id} has multiple running instances; specify instance"
            )
        if running:
            return str(running[0]["instance_id"]), str(running[0]["mode"])
        if instance is not None or mode is not None:
            if len(entries) == 1:
                return str(entries[0]["instance_id"]), str(entries[0]["mode"])
            if not entries:
                raise LaunchRuntimeError(
                    f"launch instance is not registered: {launch_id}"
                )
            raise LaunchRuntimeError(
                f"launch {launch_id} has multiple matching instances; specify instance"
            )
        raise LaunchRuntimeError(f"launch is not running: {launch_id}")

    def start(
        self,
        config: LaunchConfig,
        *,
        instance_id: str | None = None,
        strategy_params: Mapping[str, Any] | None = None,
        account_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Start a canonical config through the shared Composition path."""

        config.require_valid()
        plan = config.plan()
        launch_id = plan.launch_id
        mode = plan.mode
        strategy = plan.strategy_ref
        instance = instance_id or new_instance_id()
        lease_account_ids = list(dict.fromkeys([*plan.account_refs, *account_ids]))
        registry = LaunchRegistryApplication(self.workspace)
        active = self.running_instance(launch_id, mode)
        if active is not None:
            raise LaunchRuntimeError(
                f"launch {launch_id} already has a running instance: "
                f"{active['instance_id']}"
            )
        registry.add(
            launch_id,
            mode=mode,
            instance_id=instance,
            strategy_ref=strategy,
            config_path=config.path if config.path.is_file() else None,
        )
        environment = LaunchConfigurationApplication().environment_config(
            config,
            workspace_root=self.workspace.paths.root,
            instance_id=instance,
        )
        registry.update_state(
            launch_id, mode=mode, instance_id=instance, state="starting"
        )
        try:
            acquire_launch_leases(
                self.workspace,
                lease_account_ids,
                launch_id=launch_id,
                instance=instance,
                mode=mode,
            )
        except Exception:
            registry.update_state(
                launch_id, mode=mode, instance_id=instance, state="failed"
            )
            raise

        market_instance_workspace = None
        try:
            instance_workspace = self.workspace.instance(mode, launch_id, instance)
            instance_workspace.prepare()
            market_runtime_profile = plan.market_profile
            market_replay_file: Path | None = None
            replay_is_materialized = False
            if plan.backtest_dataset_set is not None:
                market_replay_file = instance_workspace.market_state("replay.jsonl")
                readers = DatasetReaderApplication(
                    DatasetCatalogApplication(self.workspace)
                )
                read_plan = readers.plan(
                    plan.backtest_dataset_set,
                    start_time_unix_nanos=plan.backtest_start_time_unix_nanos,
                    end_time_unix_nanos=plan.backtest_end_time_unix_nanos,
                )
                readers.materialize_replay(read_plan, market_replay_file)
                replay_is_materialized = True
            elif plan.paper_events is not None:
                market_replay_file = plan.paper_events
            elif plan.backtest_replay_file is not None:
                market_replay_file = plan.backtest_replay_file
            if market_replay_file is not None:
                if not replay_is_materialized:
                    materialize_replay_file(
                        market_replay_file,
                        instance_workspace.market_state("replay.jsonl"),
                        catalog_root=self.workspace.paths.state / "market",
                    )
                validate_replay_window(
                    instance_workspace.market_state("replay.jsonl"),
                    start_time_unix_nanos=plan.backtest_start_time_unix_nanos,
                    end_time_unix_nanos=plan.backtest_end_time_unix_nanos,
                )

            execution_config = dict(plan.execution)
            execution_enabled = bool(execution_config.get("enabled", True))
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
            raw_routes = execution_config.get("routes")
            execution_routes: list[Mapping[str, Any]] | None = None
            if raw_routes is not None:
                if not isinstance(raw_routes, list) or not all(
                    isinstance(route, Mapping) for route in raw_routes
                ):
                    raise LaunchRuntimeError(
                        "execution.routes must be an array of route tables"
                    )
                execution_routes = [dict(route) for route in raw_routes]
            confirm_live = mode == "live" and bool(
                plan.live_safety and plan.live_safety.get("trading_enabled")
            )
            account_records = {
                account_id: AccountAdminApplication(self.workspace).show(account_id)
                for account_id in lease_account_ids
            }
            market_instance_workspace = (
                instance_workspace if plan.market_scope == "instance" else None
            )
            reference_required = requires_reference_runtime(
                mode, market_replay_file is not None
            )
            components = ComponentProcessApplication(self.workspace)
            if reference_required:
                components.ensure_running(
                    "reference", reference_config=ReferenceProcessConfig(self.workspace)
                )
            market_control = components.ensure_running(
                "market",
                market_runtime_profile=market_runtime_profile,
                instance_workspace=market_instance_workspace,
            )
            account_endpoints: dict[str, dict[str, Any]] = {}
            for bound_account_id in lease_account_ids:
                socket_name = account_component_name(bound_account_id)
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
            components.ensure_running("risk", instance_workspace=instance_workspace)
            component_endpoints: dict[str, dict[str, Any]] = {
                "risk": {
                    "socket": str(instance_workspace.socket("risk")),
                    "health": str(instance_workspace.health("risk")),
                },
                "market": {
                    "socket": (
                        str(instance_workspace.socket("market"))
                        if market_instance_workspace is not None
                        else str(self.workspace.paths.process_socket("market"))
                    ),
                    "health": (
                        str(instance_workspace.health("market"))
                        if market_instance_workspace is not None
                        else str(self.workspace.paths.health_file("market"))
                    ),
                },
                "reference": (
                    {
                        "socket": str(self.workspace.paths.process_socket("reference")),
                        "health": str(self.workspace.paths.health_file("reference")),
                        "required": True,
                    }
                    if reference_required
                    else {"required": False}
                ),
            }
            write_instance_manifest(
                instance_workspace,
                accounts=account_endpoints,
                components=component_endpoints,
            )
            if execution_enabled:
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
            write_instance_manifest(
                instance_workspace,
                accounts=account_endpoints,
                components=component_endpoints,
            )
            params = {**dict(plan.strategy_params), **dict(strategy_params or {})}
            StrategyProcessApplication(self.workspace).ensure_running(
                strategy,
                launch_id=launch_id,
                instance_id=instance,
                mode=mode,
                params=params,
                environment=environment.process_environment,
            )
            control = LaunchControlApplication(self.workspace)
            target = self._target(launch_id, instance, mode)
            started = control.start(target)
            if started.get("status") == "ready":
                started = control.strategy_control(target, "enable")
            if (
                mode == "backtest"
                and market_runtime_profile == "replay"
                and hasattr(market_control, "request")
            ):
                market_control.request("POST", "/v1/replay/resume")
            started.update(
                {
                    "launch_id": launch_id,
                    "mode": mode,
                    "instance_id": instance,
                    "normalized_config_hash": config.normalized_hash,
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
            return started
        except Exception:
            try:
                registry.update_state(
                    launch_id, mode=mode, instance_id=instance, state="failed"
                )
            except FileNotFoundError:
                pass
            try:
                cleanup_instance_components(
                    self.workspace,
                    self.workspace.instance(mode, launch_id, instance),
                    lease_account_ids,
                    stop_strategy=True,
                    stop_market=market_instance_workspace is not None,
                )
            finally:
                release_launch_leases(
                    self.workspace, lease_account_ids, instance=instance
                )
            raise

    def report(self, launch_id: str, *, instance: str | None = None) -> dict[str, Any]:
        resolved_instance, mode = self.resolve_target(
            launch_id, mode="backtest", instance=instance
        )
        if mode != "backtest":
            raise LaunchRuntimeError(
                "launch report is only available for backtest launches"
            )
        path = self.workspace.instance(mode, launch_id, resolved_instance).state(
            "backtest", "report.json"
        )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise LaunchRuntimeError(
                f"backtest report is not available: {path}"
            ) from error
        except json.JSONDecodeError as error:
            raise LaunchRuntimeError(f"backtest report is invalid: {path}") from error
        if not isinstance(value, dict):
            raise LaunchRuntimeError(f"backtest report must be an object: {path}")
        return value

    def wait(
        self,
        launch_id: str,
        *,
        instance: str | None = None,
        timeout: float = 3600.0,
    ) -> dict[str, Any]:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        resolved_instance, mode = self.resolve_target(
            launch_id, mode="backtest", instance=instance
        )
        if mode != "backtest":
            raise LaunchRuntimeError("launch wait is only available for backtests")
        target = self._target(launch_id, resolved_instance, mode)
        deadline = time.monotonic() + timeout
        value: dict[str, Any] = {}
        while time.monotonic() < deadline:
            value = LaunchControlApplication(self.workspace).status(target)
            if value.get("status") in {"not_running", "stopped", "failed"}:
                break
            time.sleep(0.1)
        else:
            raise LaunchRuntimeError(f"backtest did not finish within {timeout:g}s")
        instance_workspace = self.workspace.instance(mode, launch_id, resolved_instance)
        stopped = cleanup_instance_components(
            self.workspace,
            instance_workspace,
            [],
            stop_strategy=False,
            stop_market=True,
        )
        state = (
            "completed"
            if value.get("status") in {"not_running", "stopped"}
            else "failed"
        )
        LaunchRegistryApplication(self.workspace).update_state(
            launch_id,
            mode=mode,
            instance_id=resolved_instance,
            state=state,
        )
        report_path = instance_workspace.state("backtest", "report.json")
        report_value = (
            json.loads(report_path.read_text(encoding="utf-8"))
            if report_path.is_file()
            else None
        )
        return {
            "status": state,
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": mode,
            "report": report_value,
            "stopped": stopped,
            "next_action": (
                f"kairos launch report {launch_id}"
                if state == "completed"
                else f"kairos launch logs {launch_id}"
            ),
        }

    def component_status(self, instance_workspace: Any) -> dict[str, dict[str, Any]]:
        components = ComponentProcessApplication(self.workspace)
        try:
            manifest = json.loads(
                instance_workspace.component_manifest().read_text(encoding="utf-8")
            )
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            manifest = {}
        endpoints = manifest.get("components", {})
        result: dict[str, dict[str, Any]] = {}
        reference = (
            endpoints.get("reference", {}) if isinstance(endpoints, dict) else {}
        )
        reference_required = not (
            isinstance(reference, dict) and reference.get("required") is False
        )
        if reference_required:
            result["reference"] = components.status("reference")
        market = endpoints.get("market", {}) if isinstance(endpoints, dict) else {}
        market_socket = (
            str(market.get("socket") or "") if isinstance(market, dict) else ""
        )
        result["market"] = components.status(
            "market",
            instance_workspace=(
                instance_workspace
                if market_socket == str(instance_workspace.socket("market"))
                else None
            ),
        )
        for name in ("risk", "execution"):
            result[name] = components.status(
                name, instance_workspace=instance_workspace
            )
        accounts = manifest.get("accounts", {})
        if isinstance(accounts, dict):
            for account_id, item in accounts.items():
                socket_name = (
                    item.get("socket_name") if isinstance(item, dict) else None
                )
                result[f"account:{account_id}"] = components.status(
                    "account",
                    instance_workspace=instance_workspace,
                    socket_name=str(socket_name) if socket_name else None,
                )
        return result

    def status(self, launch_id: str, *, instance: str | None = None) -> dict[str, Any]:
        resolved_instance, mode = self.resolve_target(launch_id, instance=instance)
        value = LaunchControlApplication(self.workspace).status(
            self._target(launch_id, resolved_instance, mode)
        )
        return self.decorate_status(launch_id, resolved_instance, mode, value)

    def decorate_status(
        self,
        launch_id: str,
        instance: str,
        mode: str,
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Aggregate an instance-control result with component health."""

        instance_workspace = self.workspace.instance(mode, launch_id, instance)
        component_status = self.component_status(instance_workspace)
        strategy_status = value.get("status", "not_running")
        unhealthy = {
            name: item.get("status")
            for name, item in component_status.items()
            if item.get("status") not in {"ready", "running", "ok"}
        }
        all_stopped = all(
            item.get("status") in {"not_running", "stale"}
            for item in component_status.values()
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

    def stop(
        self,
        launch_id: str,
        *,
        instance: str | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        resolved_instance, resolved_mode = self.resolve_stop_target(
            launch_id, instance=instance, mode=mode
        )
        instance_workspace = self.workspace.instance(
            resolved_mode, launch_id, resolved_instance
        )
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
        components = ComponentProcessApplication(self.workspace)
        stopped: dict[str, dict[str, Any]] = {}
        try:
            stopped["strategy"] = LaunchControlApplication(self.workspace).stop(
                self._target(launch_id, resolved_instance, resolved_mode)
            )
        except Exception as error:
            stopped["strategy"] = {
                "component": "strategy",
                "status": "stop_failed",
                "error": str(error),
            }
        for component in ("execution", "risk"):
            stopped[component] = stop_component_safely(
                components, component, instance_workspace=instance_workspace
            )
        for socket_name in manifest_accounts or ["account"]:
            stopped[f"account:{socket_name}"] = stop_component_safely(
                components,
                "account",
                socket_name=socket_name,
                instance_workspace=instance_workspace,
            )
        entry = next(
            (
                item
                for item in LaunchRegistryApplication(self.workspace).list()
                if item.get("launch_id") == launch_id
                and item.get("mode") == resolved_mode
                and item.get("instance_id") == resolved_instance
            ),
            None,
        )
        market_shared = resolved_mode == "live"
        if entry is not None:
            config_value = entry.get("config")
            if isinstance(config_value, str) and Path(config_value).is_file():
                try:
                    market_shared = (
                        LaunchConfigurationApplication()
                        .load(
                            config_value,
                            workspace_root=self.workspace.paths.root,
                        )
                        .plan()
                        .market_scope
                        == "shared"
                    )
                    if not account_ids:
                        account_ids = list(
                            LaunchConfigurationApplication()
                            .load(
                                config_value,
                                workspace_root=self.workspace.paths.root,
                            )
                            .account_refs
                        )
                except (FileNotFoundError, LaunchConfigError, ValueError):
                    pass
        if not market_shared:
            stopped["market"] = stop_component_safely(
                components, "market", instance_workspace=instance_workspace
            )
        release_launch_leases(self.workspace, account_ids, instance=resolved_instance)
        try:
            LaunchRegistryApplication(self.workspace).update_state(
                launch_id,
                mode=resolved_mode,
                instance_id=resolved_instance,
                state="stopped",
            )
        except FileNotFoundError:
            pass
        issues = {
            name: result.get("error")
            for name, result in stopped.items()
            if result.get("status") == "stop_failed"
        }
        return {
            **stopped.get("strategy", {}),
            "launch_id": launch_id,
            "instance_id": resolved_instance,
            "mode": resolved_mode,
            "status": "stopped" if not issues else "degraded",
            "stopped_components": stopped,
            "stop_issues": issues,
            "next_action": (
                f"kairos launch start {launch_id}"
                if not issues
                else f"kairos launch logs {launch_id}"
            ),
        }


__all__ = [
    "LaunchRuntimeApplication",
    "LaunchRuntimeError",
    "account_component_name",
    "acquire_launch_leases",
    "cleanup_instance_components",
    "release_launch_leases",
    "requires_reference_runtime",
    "stop_component_safely",
    "write_instance_manifest",
]
