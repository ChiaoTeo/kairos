from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import importlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Callable, Mapping, Protocol, TypeVar

from kairospy.application.usecases.account.application.runtime import default_account_books
from kairospy.application.support.messaging import Message
from kairospy.application.support.launch.domain.identity import LaunchIdentity
from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.support.launch.application.runtime import LaunchRuntimeResult
from kairospy.application.support.launch.application.configuration import ConfiguredAccount, ConfiguredCredential
from kairospy.application.support.launch.application.artifacts import LaunchOutputLog, write_launch_log_section
from kairospy.application.system.application.resources import TradingLaunchSpec, TradingSystemResources
from kairospy.application.system.application.runtime import TradingSystem
from kairospy.application.usecases.workspace.domain.workspace import AccountRecord, KairosWorkspace
from kairospy.application.usecases.strategy.protocol import Strategy
from kairospy.application.support.launch.application.system_commands import SystemCommandDispatcher
from kairospy.application.support.launch.application.commands import SystemCommandResult
from kairospy.application.support.launch.services.command_queue import SystemCommandFileQueue
from kairospy.application.support.launch.application.protocol import LaunchTarget, LaunchTargetDescriptor, StopSignalBindable
from kairospy.application.support.launch.application.configuration import LaunchAccountConfig
from kairospy.domain.account import AccountBookRef, AccountIdentity
from kairospy.application.usecases.account.application.runtime import account_book_route


_LAUNCH_INSTANCE_ID_ENV = "KAIROS_LAUNCH_INSTANCE_ID"
ResultT = TypeVar("ResultT")


class LaunchRun(Protocol[ResultT]):
    def __call__(self) -> ResultT:
        ...


class TradingConfigurationError(ValueError):
    pass


def bind_stop_signal(market_data: object, stop_requested: Callable[[], bool]) -> None:
    """Bind the launch control signal to a runtime market-data source."""

    if not isinstance(market_data, StopSignalBindable):
        raise TypeError(
            "launch target market data does not support the required "
            "set_stop_signal() lifecycle capability"
        )
    market_data.set_stop_signal(stop_requested)


class TradingSystemLauncher:
    def _run_composed(
        self,
        composed: object,
        configured: object,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> object:
        """Run and finalize a graph produced by composition.

        Lease ownership, runtime lifecycle, status logging, and artifacts are
        launch concerns and intentionally stay out of composition.
        """
        required = ("resources", "launch_id", "mode", "strategy", "launch_directory", "normalized_config", "lifecycle", "build_result")
        if any(not hasattr(composed, name) for name in required):
            raise TypeError("composition must return ComposedLaunch")
        if stop_requested is not None:
            bind_stop_signal(composed.resources.data, stop_requested)

        def run() -> object:
            runtime = self._launch_runtime(
                launch_id=composed.launch_id,
                mode=composed.mode,
                strategy=composed.strategy,
                launch_directory=composed.launch_directory,
                normalized_config=composed.normalized_config,
                resources=composed.resources,
                lifecycle=composed.lifecycle,
            )
            result = composed.build_result(runtime)
            self._write_account_status(composed.launch_directory, result)
            self._write_artifacts(composed.launch_directory, result, composed.normalized_config, composed.resources.assembly)
            return result

        return self._with_account_leases(configured, composed.mode, run) if configured is not None else run()

    def run_composed(self, composed: object, configured: object | None = None, *, stop_requested: Callable[[], bool] | None = None) -> object:
        """Execute a composition-owned resource graph under launch lifecycle."""

        return self._run_composed(composed, configured, stop_requested=stop_requested)

    def run_resources(
        self,
        *,
        launch_id: str,
        mode: RuntimeMode,
        strategy: Strategy,
        launch_directory: Path,
        normalized_config: Mapping[str, object],
        resources: TradingSystemResources,
        lifecycle: object | None = None,
    ) -> LaunchRuntimeResult:
        """Run an already assembled resource graph."""

        return self._launch_configured(
            launch_id=launch_id,
            mode=mode,
            strategy=strategy,
            launch_directory=launch_directory,
            normalized_config=normalized_config,
            resources=resources,
            lifecycle=lifecycle,
        )

    def _launch_configured(
        self,
        *,
        launch_id: str,
        mode: RuntimeMode,
        strategy: Strategy,
        launch_directory: Path,
        normalized_config: Mapping[str, object],
        resources: TradingSystemResources,
        lifecycle: object | None = None,
    ) -> LaunchRuntimeResult:
        identity = LaunchIdentity(launch_id, mode)
        write_launch_log_section(
            launch_directory,
            "Launch Environment",
            {
                "launch_id": identity.launch_id,
                "mode": identity.mode.value,
                "launch_instance_id": _launch_instance_id(launch_id),
                "launch_directory": launch_directory,
                "strategy_id": getattr(strategy, "strategy_id", None),
            },
        )
        write_launch_log_section(launch_directory, "System Status", {"phase": "starting"})
        resources = TradingSystemResources(
            business=resources.business,
            input_streams=resources.input_streams,
            data=resources.data,
            account=resources.account,
            reference=resources.reference,
            trading_execution=resources.trading_execution,
            connection_scope=resources.connection_scope,
            message_bus=resources.message_bus,
            assembly=resources.assembly,
        )
        terminal_output = sys.stdout if getattr(sys.stdout, "isatty", lambda: False)() else None
        with LaunchOutputLog(launch_directory, stdout=terminal_output, stderr=terminal_output):
            result = TradingSystem(
                TradingLaunchSpec(
                    launch_id=launch_id,
                    mode=mode,
                    strategy=strategy,
                    launch_directory=launch_directory,
                    normalized_config=normalized_config,
                    resources=resources,
                    lifecycle=lifecycle,
                )
            ).run()
        write_launch_log_section(
            launch_directory,
            "System Status",
            {
                "phase": "stopped",
                "events": getattr(result.runtime, "event_count", None),
                "intents": _intent_count(result),
            },
            stdout=terminal_output,
        )
        return result

    def _launch_runtime(
        self,
        *,
        launch_id: str,
        mode: RuntimeMode,
        strategy: Strategy,
        launch_directory: Path,
        normalized_config: Mapping[str, object],
        resources: TradingSystemResources,
        lifecycle: object | None,
    ) -> LaunchRuntimeResult:
        return self._launch_configured(
            launch_id=launch_id,
            mode=mode,
            strategy=strategy,
            launch_directory=launch_directory,
            normalized_config=normalized_config,
            resources=resources,
            lifecycle=lifecycle,
        )

    def load_strategy(self, path: str) -> Strategy:
        if ":" not in path:
            raise ValueError("strategy must use module:callable")
        module_name, callable_name = path.split(":", 1)
        module = importlib.import_module(module_name)
        factory = getattr(module, callable_name)
        strategy = factory() if callable(factory) else factory
        if not hasattr(strategy, "strategy_id"):
            raise ValueError("strategy object must expose strategy_id")
        return strategy

    def _write_artifacts(
        self,
        launch_directory: Path,
        result: object,
        normalized_config: Mapping[str, object],
        assembly: object | None,
    ) -> None:
        if assembly is None or not callable(getattr(assembly, "output", None)):
            raise TypeError("launch resources must provide an output assembly")
        assembly.output(launch_directory).write_result(result=result, normalized_config=normalized_config)

    def _write_account_status(self, launch_directory: Path, result: object) -> None:
        account_view = getattr(result, "account_view", None)
        write_launch_log_section(
            launch_directory,
            "Account Status",
            {
                "cash": getattr(account_view, "cash", None),
                "equity": getattr(account_view, "equity", None),
                "initial_equity": getattr(account_view, "initial_equity", None),
                "net_profit": getattr(account_view, "net_profit", None),
                "total_return": getattr(account_view, "total_return", None),
            },
        )

    def read_event_jsonl(self, path: Path) -> tuple[Message, ...]:
        if not path.exists():
            raise ValueError(f"events file does not exist: {path}")
        events: list[Message] = []
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, Mapping):
                raise ValueError(f"event row {index} must be a JSON object")
            events.append(self._event_from_mapping(row, fallback_sequence=index))
        return tuple(events)

    def account_resolver(self, config_path: Path):
        workspace = KairosWorkspace.resolve(config_path)

        def resolve(account_ref: str) -> ConfiguredAccount:
            return _configured_account_from_record(workspace.accounts.get(account_ref), workspace=workspace)

        return resolve

    def _with_account_leases(self, configured: object, mode: RuntimeMode, run: LaunchRun[ResultT]) -> ResultT:
        accounts = _trade_lease_accounts(configured)
        if not accounts:
            return run()
        workspace = KairosWorkspace.resolve(getattr(configured, "launch_directory", None))
        leases = workspace.account_locks.acquire_many(
            accounts,
            launch_id=str(getattr(configured, "launch_id")),
            launch_instance_id=_launch_instance_id(str(getattr(configured, "launch_id"))),
            mode=mode.value,
        )
        workspace.operations.append(
            "account.trade_lock.acquire",
            target={"launch": str(getattr(configured, "launch_id"))},
            payload={"accounts": [identity.value for identity, _environment in accounts]},
        )
        try:
            return run()
        finally:
            leases.release()
            workspace.operations.append(
                "account.trade_lock.release",
                target={"launch": str(getattr(configured, "launch_id"))},
                payload={"accounts": [identity.value for identity, _environment in accounts]},
            )

    def _event_from_mapping(self, row: Mapping[str, object], *, fallback_sequence: int) -> Message:
        raw_time = row.get("time")
        if not isinstance(raw_time, str):
            raise ValueError("event time must be an ISO-8601 string")
        return Message(
            topic=f"{str(row.get('domain') or row.get('stream') or 'data')}.{str(row.get('kind') or 'event')}",
            published_at=datetime.fromisoformat(raw_time),
            producer="cli.events",
            producer_sequence=int(row.get("sequence") or fallback_sequence),
            payload=row.get(
                "payload",
                {key: value for key, value in row.items() if key not in {"domain", "stream", "kind", "time", "sequence"}},
            ),
        )


class SystemCommandProducer:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.queue = SystemCommandFileQueue(directory)
        self.dispatcher = SystemCommandDispatcher(directory)

    async def events(self):
        import asyncio
        import time

        sequence = 1
        next_idle = 0.0
        while True:
            handled = False
            for command in self.queue.pending():
                handled = True
                if _dispatcher_command(command.kind):
                    result = self.dispatcher.dispatch(command)
                    if command.kind == "runtime.stop" and result.status == "accepted":
                        _mirror_runtime_stop_command(self.directory, command, result.result)
                        self.queue.respond(result)
                        return
                    self.queue.respond(result)
                    continue
                yield Message(
                    topic="system.cli.command",
                    published_at=command.requested_at,
                    producer="system.commands",
                    producer_sequence=sequence,
                    payload={"command": command.kind, "args": dict(command.payload)},
                )
                sequence += 1
                self.queue.respond(SystemCommandResult.accepted(command, {"processed": True}))
            if _system_stop_requested(self.directory):
                return
            if not handled:
                now = time.monotonic()
                if now >= next_idle:
                    next_idle = now + 1.0
                    yield Message(
                        topic="system.idle",
                        published_at=datetime.now().astimezone(),
                        producer="system.commands",
                        producer_sequence=sequence,
                        payload={"status": "running"},
                    )
                    sequence += 1
                    continue
                await asyncio.sleep(0.05)


def _dispatcher_command(kind: str) -> bool:
    return kind in {
        "runtime.stop",
        "account.current",
        "account.balances",
        "account.positions",
        "account.open_orders",
        "account.pending_orders",
        "account.trade-status",
        "account.trade-acquire",
        "account.trade-release",
        "order.status",
    }


def _system_stop_requested(directory: Path) -> bool:
    command_path = directory / "command.json"
    if not command_path.exists():
        return False
    try:
        command = json.loads(command_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if not isinstance(command, Mapping):
        return False
    return str(command.get("desired_state") or "").strip().lower() == "stopped"


def _mirror_runtime_stop_command(directory: Path, command: object, result: Mapping[str, object]) -> None:
    reason = str(result.get("reason") or getattr(command, "payload", {}).get("reason") or "requested by system command")
    (directory / "command.json").write_text(
        json.dumps(
            {
                "command_id": getattr(command, "command_id"),
                "kind": getattr(command, "kind"),
                "desired_state": "stopped",
                "reason": reason,
                "actor": getattr(command, "actor"),
                "requested_at": getattr(command, "requested_at").isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _configured_account_from_record(account: AccountRecord, *, workspace: KairosWorkspace | None = None) -> ConfiguredAccount:
    _ = workspace
    return ConfiguredAccount(
        account.account_id,
        _int_value(account.values.get("index", 0)),
        account.venue or account.broker,
        _decimal_value(account.values.get("cash", "100000")),
        str(account.values.get("currency", "USD")),
        environment=str(account.environment),
        fee_rate=_decimal_value(account.values.get("fee_rate", "0")),
        credential=account.credential,
        credential_role="trade",
        credentials=tuple(
            ConfiguredCredential(
                credential.name,
                ref=credential.ref,
                kind=credential.kind,
                role=credential.role,
            )
            for credential in account.credentials
        ),
    )


def _trade_lease_accounts(configured: object) -> tuple[tuple[AccountIdentity, str], ...]:
    launch_accounts = getattr(configured, "launch_accounts", None)
    primary = getattr(configured, "account_config", None)
    configured_accounts = getattr(configured, "launch_account_configs", None)
    selected: dict[tuple[str, str], tuple[AccountIdentity, str]] = {}
    if isinstance(launch_accounts, Mapping) and launch_accounts:
        for alias, launch_account in launch_accounts.items():
            if not isinstance(launch_account, LaunchAccountConfig) or not launch_account.trade:
                continue
            account = configured_accounts.get(alias) if isinstance(configured_accounts, Mapping) else None
            if not isinstance(account, ConfiguredAccount):
                account = primary
            if (
                isinstance(account, ConfiguredAccount)
                and account.has_trade_credential()
                and _launch_account_can_trade(launch_account, account, configured)
            ):
                identity = AccountIdentity(account.venue, account.account_id)
                selected.setdefault((str(identity.broker), str(identity.account_id)), (identity, account.environment))
    elif isinstance(primary, ConfiguredAccount) and primary.has_trade_credential():
        identity = AccountIdentity(primary.venue, primary.account_id)
        selected.setdefault((str(identity.broker), str(identity.account_id)), (identity, primary.environment))
    return tuple(selected.values())


def _launch_account_can_trade(launch_account: LaunchAccountConfig, account: ConfiguredAccount, configured: object) -> bool:
    books = launch_account.books or default_account_books(account.venue, fallback=_default_trade_book(configured))
    for book in books:
        ref = AccountBookRef(account.venue, account.account_id, book)
        if account_book_route(ref, broker=ref.broker).can_trade:
            return True
    return False


def _default_trade_book(configured: object) -> str:
    for source_name in ("market",):
        value = getattr(configured, source_name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for config_name in ("paper_config", "live_config"):
        values = getattr(configured, config_name, None)
        if isinstance(values, Mapping):
            value = values.get("market")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "default"


def _launch_instance_id(launch_id: str) -> str:
    value = os.environ.get(_LAUNCH_INSTANCE_ID_ENV)
    if value is not None and value.strip():
        return value.strip()
    return f"{launch_id}:{os.getpid()}"


def _intent_count(result: object) -> int | None:
    intents = getattr(result, "intents", None)
    listing = getattr(intents, "list", None)
    return len(listing()) if callable(listing) else None


def _decimal_value(value: object) -> Decimal:
    return Decimal(str(value))


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("account index must be an integer")
    return int(value)


def _resolve_path(value: object, *, root: Path) -> Path:
    path = Path(str(value)).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


__all__ = [
    "LaunchTarget",
    "LaunchTargetDescriptor",
    "TradingConfigurationError",
    "TradingSystemLauncher",
    "bind_stop_signal",
]
