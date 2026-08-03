from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import importlib
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping, Protocol, TypeVar

from kairospy.application.usecases.account.domain.books import default_account_books
from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.application.support.runtime.domain.lines import RuntimeLine
from kairospy.application.support.runtime.domain.modes import RuntimeMode
from kairospy.application.support.runtime.application.launch import RuntimeLaunchResult
from kairospy.application.support.composition.application.launcher import ConfiguredLaunchComposer, market_feed_resolver_builder
from kairospy.application.support.composition.application.system import compose_system
from kairospy.application.support.composition.application.artifacts import launch_output
from kairospy.application.support.composition.application.common import reference_runtime
from kairospy.application.support.composition.application.runtime_services import compose_runtime_assembly
from kairospy.application.support.launch.application.configuration import BacktestConfigurationError, BacktestLaunchResult, BrokerFactory, ConfiguredAccount, ConfiguredBacktest, ConfiguredCredential, ConfiguredLive, ConfiguredPaper, LiveConfigurationError, LiveLaunchResult, LiveMarketFeedFactory, PaperConfigurationError, PaperLaunchResult, PaperMarketFeedFactory, configured_backtest, configured_live, configured_paper
from kairospy.application.support.system.application.artifacts import LaunchOutputLog, write_launch_log_section
from kairospy.application.support.runtime.application.launch.resources import TradingRuntimeResources, TradingLaunchSpec
from kairospy.application.support.system.application.runtime import TradingSystem, TradingSystemSession
from kairospy.application.support.system.application.workspace import AccountRecord, KairosWorkspace
from kairospy.application.usecases.strategy.application.cli import CliStrategyBase
from kairospy.application.usecases.strategy.protocol import Strategy
from kairospy.application.support.system.application.session import SystemCommandDispatcher, SystemCommandFileQueue, SystemCommandResult
from kairospy.application.support.launch.application.protocol import StopSignalBindable
from kairospy.application.support.runtime.domain.connections import DefaultConnectionManager
from kairospy.application.support.system.application.config import ConfigError, LaunchAccountConfig, load_launch_config
from kairospy.domain.account import AccountBookRef, AccountIdentity
from kairospy.application.usecases.account.domain.routing import account_book_route
from kairospy.application.support.system.application.config import SYSTEM_LAUNCH_ID


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


@dataclass(frozen=True, slots=True)
class LaunchTarget:
    """A resolved launch target for the system control plane.

    The configured mode object stays private to launch application. Callers
    only need the target identity, its planned directory and ``run``.
    """

    mode: RuntimeMode
    launch_id: str
    launch_directory: Path
    _runner: Callable[[], object]
    _bind_stop: Callable[[Callable[[], bool]], None]

    def run(self) -> object:
        return self._runner()

    def bind_stop(self, stop_requested: Callable[[], bool]) -> None:
        self._bind_stop(stop_requested)


@dataclass(frozen=True, slots=True)
class LaunchTargetDescriptor:
    mode: RuntimeMode
    launch_id: str
    launch_directory: Path


class TradingSystemLauncher:
    def __init__(self, composer: ConfiguredLaunchComposer | None = None) -> None:
        self._composer = composer or ConfiguredLaunchComposer()

    def describe_target(self, *, mode: RuntimeMode, config_path: str | Path) -> LaunchTargetDescriptor:
        try:
            launch_config = load_launch_config(Path(config_path))
            launch_config.require_mode(mode.value)
        except ConfigError as error:
            raise TradingConfigurationError(str(error)) from error
        mode_config = launch_config.values.get(mode.value)
        if not isinstance(mode_config, Mapping):
            mode_config = {}
        launches_root_value = mode_config.get("launches_root")
        launches_root = Path(".kairos/launches").resolve() if launches_root_value is None else _resolve_path(launches_root_value, root=launch_config.root)
        return LaunchTargetDescriptor(
            mode=mode,
            launch_id=launch_config.launch_id,
            launch_directory=launches_root / mode.value / launch_config.launch_id,
        )

    def launch_app_system(
        self,
        *,
        launch_id: str = SYSTEM_LAUNCH_ID,
        launch_directory: str | Path | None = None,
    ) -> RuntimeLaunchResult:
        if launch_id != SYSTEM_LAUNCH_ID:
            raise ValueError(f"system launch id is fixed: {SYSTEM_LAUNCH_ID}")
        directory = Path(launch_directory) if launch_directory is not None else Path(".kairos/launches") / RuntimeMode.SYSTEM.value / launch_id
        composed = compose_system(
            launch_directory=directory,
            launch_id=launch_id,
            source=_SystemCommandEventLine(directory),
        )
        return self._launch_configured(
            launch_id=launch_id,
            mode=RuntimeMode.SYSTEM,
            strategy=CliStrategyBase(),
            launch_directory=directory,
            normalized_config={
                "launch": {"id": launch_id, "mode": RuntimeMode.SYSTEM.value, "strategy": "builtin:CliStrategyBase"},
                "system": {"builtin": True, "interactive": True},
            },
            resources=composed.resources,
            lifecycle=composed.lifecycle,
        )

    def launch_backtest_config(self, config_path: str | Path) -> BacktestLaunchResult:
        try:
            configured = configured_backtest(Path(config_path))
        except BacktestConfigurationError as error:
            raise TradingConfigurationError(str(error)) from error
        return self.launch_configured_backtest(configured)

    def resolve_target(
        self,
        *,
        mode: RuntimeMode,
        config_path: str | Path,
        strategy_ref: str | None = None,
        launch_directory: str | Path | None = None,
    ) -> LaunchTarget:
        """Resolve a config into a runnable target without starting it.

        This is the application boundary used by system control. Mode
        configuration and composition details remain inside launch.
        """
        path = Path(config_path)
        stop_requested_holder: list[Callable[[], bool] | None] = [None]
        try:
            if mode is RuntimeMode.BACKTEST:
                configured: object = configured_backtest(path, strategy_ref=strategy_ref)
                runner = lambda: self.launch_configured_backtest(configured, stop_requested=stop_requested_holder[0])  # type: ignore[arg-type]
            elif mode is RuntimeMode.PAPER:
                configured = configured_paper(
                    path,
                    market_feed_resolver_builder=self._market_feed_resolver_builder("paper"),
                    account_resolver=self._account_resolver(path),
                    strategy_ref=strategy_ref,
                )
                runner = lambda: self.launch_configured_paper(configured, stop_requested=stop_requested_holder[0])  # type: ignore[arg-type]
            elif mode is RuntimeMode.LIVE:
                configured = configured_live(
                    path,
                    market_feed_resolver_builder=self._market_feed_resolver_builder("live"),
                    account_resolver=self._account_resolver(path),
                    strategy_ref=strategy_ref,
                )
                runner = lambda: self.launch_configured_live(configured, stop_requested=stop_requested_holder[0])  # type: ignore[arg-type]
            else:
                raise ValueError("config targets support backtest, paper, and live modes")
        except (BacktestConfigurationError, PaperConfigurationError, LiveConfigurationError) as error:
            raise TradingConfigurationError(str(error)) from error

        if launch_directory is not None:
            directory = Path(launch_directory)
            configured = replace(configured, launch_directory=directory)
            if isinstance(configured, ConfiguredLive):
                configured = replace(configured, state_path=directory / "live_state.json")
            if isinstance(configured, ConfiguredBacktest):
                runner = lambda: self.launch_configured_backtest(configured, stop_requested=stop_requested_holder[0])  # type: ignore[arg-type]
            elif isinstance(configured, ConfiguredPaper):
                runner = lambda: self.launch_configured_paper(configured, stop_requested=stop_requested_holder[0])  # type: ignore[arg-type]
            else:
                runner = lambda: self.launch_configured_live(configured, stop_requested=stop_requested_holder[0])  # type: ignore[arg-type]

        def bind_stop(stop_requested: Callable[[], bool]) -> None:
            stop_requested_holder[0] = stop_requested

        return LaunchTarget(
            mode=mode,
            launch_id=str(getattr(configured, "launch_id")),
            launch_directory=Path(getattr(configured, "launch_directory")),
            _runner=runner,
            _bind_stop=bind_stop,
        )

    def launch_configured_backtest(
        self,
        configured: ConfiguredBacktest,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> BacktestLaunchResult:
        return self._run_composed(self._composer.backtest(configured), configured, stop_requested=stop_requested)

    def launch_paper_config(
        self,
        config_path: str | Path,
        *,
        market_feed_factory: PaperMarketFeedFactory | None = None,
    ) -> PaperLaunchResult:
        try:
            path = Path(config_path)
            configured = configured_paper(
                path,
                market_feed_factory=market_feed_factory,
                market_feed_resolver_builder=None if market_feed_factory is not None else self._market_feed_resolver_builder("paper"),
                account_resolver=self._account_resolver(path),
            )
        except PaperConfigurationError as error:
            raise TradingConfigurationError(str(error)) from error
        return self.launch_configured_paper(configured)

    def launch_configured_paper(
        self,
        configured: ConfiguredPaper,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> PaperLaunchResult:
        return self._run_composed(self._composer.paper(configured), configured, stop_requested=stop_requested)

    def launch_live_config(
        self,
        config_path: str | Path,
        *,
        market_feed_factory: LiveMarketFeedFactory | None = None,
        broker_factory: BrokerFactory | None = None,
    ) -> LiveLaunchResult:
        try:
            path = Path(config_path)
            configured = configured_live(
                path,
                market_feed_factory=market_feed_factory,
                market_feed_resolver_builder=None if market_feed_factory is not None else self._market_feed_resolver_builder("live"),
                broker_factory=broker_factory,
                account_resolver=self._account_resolver(path),
            )
            return self.launch_configured_live(configured)
        except LiveConfigurationError as error:
            raise TradingConfigurationError(str(error)) from error

    def launch_configured_live(
        self,
        configured: ConfiguredLive,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> LiveLaunchResult:
        return self._run_composed(self._composer.live(configured), configured, stop_requested=stop_requested)

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
        from kairospy.application.support.composition.application.common import ComposedLaunch

        if not isinstance(composed, ComposedLaunch):
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
            self._write_artifacts(composed.launch_directory, result, composed.normalized_config)
            return result

        return self._with_account_leases(configured, composed.mode, run)

    def launch_events(
        self,
        *,
        strategy_path: str,
        events_path: str | Path,
        launch_id: str = "kairos-launch",
        mode: RuntimeMode | str = RuntimeMode.BACKTEST,
        launch_directory: str | Path | None = None,
    ) -> RuntimeLaunchResult:
        runtime_mode = mode if isinstance(mode, RuntimeMode) else RuntimeMode(str(mode))
        strategy = self._load_strategy(strategy_path)
        event_path = Path(events_path)
        connections = DefaultConnectionManager()
        return self._launch_configured(
            launch_id=launch_id,
            mode=runtime_mode,
            strategy=strategy,
            launch_directory=Path(launch_directory) if launch_directory is not None else Path(".kairos/launches") / runtime_mode.value / launch_id,
            normalized_config={
                "launch": {"id": launch_id, "mode": runtime_mode.value, "strategy": strategy_path},
                "events": {"source": str(event_path)},
            },
            resources=TradingRuntimeResources(
                source=RuntimeLine(self._read_event_jsonl(event_path)),
                reference=reference_runtime(event_path),
                connection_scope=connections,
            ),
        )

    def open_system_session(
        self,
        *,
        strategy_path: str,
        launch_id: str = "kairos-system-session",
        mode: RuntimeMode | str = RuntimeMode.BACKTEST,
        launch_directory: str | Path | None = None,
    ) -> TradingSystemSession:
        runtime_mode = mode if isinstance(mode, RuntimeMode) else RuntimeMode(str(mode))
        strategy = self._load_strategy(strategy_path)
        return TradingSystem(
            TradingLaunchSpec(
                launch_id=launch_id,
                mode=runtime_mode,
                strategy=strategy,
                launch_directory=Path(launch_directory) if launch_directory is not None else Path(".kairos/launches") / runtime_mode.value / launch_id,
                normalized_config={
                    "launch": {"id": launch_id, "mode": runtime_mode.value, "strategy": strategy_path},
                    "system": {"interactive": True},
                },
                resources=TradingRuntimeResources(
                    reference=reference_runtime(launch_directory),
                    connection_scope=DefaultConnectionManager(),
                    assembly=compose_runtime_assembly(),
                ),
            )
        ).start()

    def _launch_configured(
        self,
        *,
        launch_id: str,
        mode: RuntimeMode,
        strategy: Strategy,
        launch_directory: Path,
        normalized_config: Mapping[str, object],
        resources: TradingRuntimeResources,
        lifecycle: object | None = None,
    ) -> RuntimeLaunchResult:
        write_launch_log_section(
            launch_directory,
            "Launch Environment",
            {
                "launch_id": launch_id,
                "mode": mode.value,
                "launch_directory": launch_directory,
                "strategy_id": getattr(strategy, "strategy_id", None),
            },
        )
        write_launch_log_section(launch_directory, "System Status", {"phase": "starting"})
        resources = TradingRuntimeResources(
            source=resources.source,
            components=resources.components,
            data=resources.data,
            account=resources.account,
            reference=resources.reference,
            trading_execution=resources.trading_execution,
            connection_scope=resources.connection_scope,
            market_stream_connections=resources.market_stream_connections,
            market_request_connections=resources.market_request_connections,
            account_connections=resources.account_connections,
            execution_connections=resources.execution_connections,
            reference_connections=resources.reference_connections,
            assembly=resources.assembly or compose_runtime_assembly(),
        )
        with LaunchOutputLog(launch_directory):
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
                "intents": getattr(result.runtime, "intent_count", None),
            },
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
        resources: TradingRuntimeResources,
        lifecycle: object | None,
    ) -> RuntimeLaunchResult:
        return self._launch_configured(
            launch_id=launch_id,
            mode=mode,
            strategy=strategy,
            launch_directory=launch_directory,
            normalized_config=normalized_config,
            resources=resources,
            lifecycle=lifecycle,
        )

    def _load_strategy(self, path: str) -> Strategy:
        if ":" not in path:
            raise ValueError("strategy must use module:callable")
        module_name, callable_name = path.split(":", 1)
        module = importlib.import_module(module_name)
        factory = getattr(module, callable_name)
        strategy = factory() if callable(factory) else factory
        if not hasattr(strategy, "strategy_id"):
            raise ValueError("strategy object must expose strategy_id")
        return strategy

    def _write_artifacts(self, launch_directory: Path, result: object, normalized_config: Mapping[str, object]) -> None:
        launch_output(launch_directory).write_result(result=result, normalized_config=normalized_config)

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

    def _read_event_jsonl(self, path: Path) -> tuple[RuntimeEnvelope, ...]:
        if not path.exists():
            raise ValueError(f"events file does not exist: {path}")
        events: list[RuntimeEnvelope] = []
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, Mapping):
                raise ValueError(f"event row {index} must be a JSON object")
            events.append(self._event_from_mapping(row, fallback_sequence=index))
        return tuple(events)

    def _account_resolver(self, config_path: Path):
        workspace = KairosWorkspace.resolve(config_path)

        def resolve(account_ref: str) -> ConfiguredAccount:
            return _configured_account_from_record(workspace.accounts.get(account_ref), workspace=workspace)

        return resolve

    def _market_feed_resolver_builder(self, mode_label: str):
        return market_feed_resolver_builder(mode_label, error_type=TradingConfigurationError)

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

    def _event_from_mapping(self, row: Mapping[str, object], *, fallback_sequence: int) -> RuntimeEnvelope:
        raw_time = row.get("time")
        if not isinstance(raw_time, str):
            raise ValueError("event time must be an ISO-8601 string")
        return RuntimeEnvelope(
            domain=str(row.get("domain") or row.get("stream") or "data"),
            kind=str(row.get("kind") or "event"),
            time=datetime.fromisoformat(raw_time),
            sequence=int(row.get("sequence") or fallback_sequence),
            payload=row.get(
                "payload",
                {key: value for key, value in row.items() if key not in {"domain", "stream", "kind", "time", "sequence"}},
            ),
        )


class _SystemCommandEventLine:
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
                yield RuntimeEnvelope(
                    "system",
                    "cli.command",
                    command.requested_at,
                    sequence,
                    {"command": command.kind, "args": dict(command.payload)},
                )
                sequence += 1
                self.queue.respond(SystemCommandResult.accepted(command, {"processed": True}))
            if _system_stop_requested(self.directory):
                return
            if not handled:
                now = time.monotonic()
                if now >= next_idle:
                    next_idle = now + 1.0
                    yield RuntimeEnvelope(
                        "system",
                        "system.idle",
                        datetime.now().astimezone(),
                        sequence,
                        {"status": "running"},
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
