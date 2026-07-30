from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import importlib
import json
import os
from pathlib import Path
from typing import Callable, Mapping, TypeVar

from kairospy.application.protocol import RuntimeEnvelope, RuntimeLine
from kairospy.application.modes import RuntimeMode
from kairospy.application.runtime.launch import RuntimeLaunchResult
from kairospy.application.service.modes.backtest import BacktestConfigurationError, BacktestLaunchResult, ConfiguredBacktest, configured_backtest
from kairospy.application.service.modes.live.account import LiveAccountService
from kairospy.application.service.modes.live.config import BrokerFactory, ConfiguredLive, LiveConfigurationError, LiveLaunchResult, MarketFeedFactory as LiveMarketFeedFactory, configured_live
from kairospy.application.service.modes.paper.config import ConfiguredPaper, PaperConfigurationError, PaperLaunchResult, MarketFeedFactory as PaperMarketFeedFactory, configured_paper
from kairospy.application.service.modes.common import ConfiguredAccount, ConfiguredCredential
from kairospy.application.service.domain.account.routing import account_book_route
from kairospy.application.service.domain.account import SimulatedAccount
from kairospy.application.service.domain.market import MarketDataSpec
from kairospy.application.service.runtime.account import SimulatedAccountService
from kairospy.application.service.runtime.execution import AccountTradeAuthority, AuthorizingAccountPort, AuthorizingTradingExecutionService, SimulatedExecutionService
from kairospy.application.system.artifacts.logging import LaunchOutputLog, write_launch_log_section
from kairospy.application.system.artifacts.output import LaunchOutput
from kairospy.application.system.facade.resources import DriverName, ExchangeName, exchange
from kairospy.application.launch import LaunchAccountBinding, LaunchAccountDirectory
from kairospy.application.launch.host.resources import TradingRuntimeResources, TradingLaunchSpec
from kairospy.application.launch.host.runtime_host import TradingSystem, TradingSystemSession
from kairospy.application.system.resources.accounts import BacktestAccountResources, LiveAccountResources, PaperAccountResources
from kairospy.application.system.resources.live_state import JsonLiveRuntimeStateStore
from kairospy.application.system.workspace import AccountRecord, KairosWorkspace
from kairospy.application.strategy import CliStrategyBase, Strategy
from kairospy.application.system.session import SystemCommandDispatcher, SystemCommandFileQueue, SystemCommandResult
from kairospy.config import LaunchAccountConfig
from kairospy.core.account import AccountBookKind, AccountCapability, AccountContext, AccountFeeSchedule, AccountIdentity, AccountRef, Environment
from kairospy.core.execution import ExecutionCoordinator
from kairospy.config import SYSTEM_LAUNCH_ID


_LAUNCH_INSTANCE_ID_ENV = "KAIROS_LAUNCH_INSTANCE_ID"
ResultT = TypeVar("ResultT")


class TradingConfigurationError(ValueError):
    pass


class TradingSystemLauncher:
    def launch_app_system(
        self,
        *,
        launch_id: str = SYSTEM_LAUNCH_ID,
        launch_directory: str | Path | None = None,
    ) -> RuntimeLaunchResult:
        if launch_id != SYSTEM_LAUNCH_ID:
            raise ValueError(f"system launch id is fixed: {SYSTEM_LAUNCH_ID}")
        directory = Path(launch_directory) if launch_directory is not None else Path(".kairos/launches") / RuntimeMode.SYSTEM.value / launch_id
        resources, authority = self._system_resources(directory, launch_id=launch_id)
        return self._launch_configured(
            launch_id=launch_id,
            mode=RuntimeMode.SYSTEM,
            strategy=CliStrategyBase(),
            launch_directory=directory,
            normalized_config={
                "launch": {"id": launch_id, "mode": RuntimeMode.SYSTEM.value, "strategy": "builtin:CliStrategyBase"},
                "system": {"builtin": True, "interactive": True},
            },
            resources=resources,
            lifecycle=_SystemTradeAuthorityLifecycle(authority),
        )

    def launch_backtest_config(self, config_path: str | Path) -> BacktestLaunchResult:
        try:
            configured = configured_backtest(Path(config_path))
        except BacktestConfigurationError as error:
            raise TradingConfigurationError(str(error)) from error
        return self.launch_configured_backtest(configured)

    def launch_configured_backtest(self, configured: ConfiguredBacktest) -> BacktestLaunchResult:
        self._configure_backtest_market_downloads(configured)
        account_resources = BacktestAccountResources.from_configured(configured)
        runtime = self._launch_configured(
            launch_id=configured.launch_id,
            mode=RuntimeMode.BACKTEST,
            strategy=configured.strategy,
            launch_directory=configured.launch_directory,
            normalized_config=configured.normalized_config,
            resources=TradingRuntimeResources(
                source=configured.data,
                data=configured.data,
                account=account_resources.account,
                trading_execution=account_resources.execution,
            ),
        )
        result = account_resources.build_result(configured, runtime)
        self._write_account_status(configured.launch_directory, result)
        self._write_artifacts(configured.launch_directory, result, configured.normalized_config)
        return result

    def _configure_backtest_market_downloads(self, configured: ConfiguredBacktest) -> None:
        if configured.market_policy.on_missing != "download":
            return

        def factory(spec: MarketDataSpec):
            return exchange(_exchange_name(spec.venue), DriverName.ccxt)

        configured.data.set_historical_client_factory(factory)

    def _system_resources(self, launch_directory: Path, *, launch_id: str) -> tuple[TradingRuntimeResources, AccountTradeAuthority]:
        workspace = KairosWorkspace.resolve(launch_directory)
        directory = _system_account_directory(workspace)
        authority = AccountTradeAuthority(
            workspace.account_locks,
            launch_id=launch_id,
            launch_instance_id=_launch_instance_id(launch_id),
            mode=RuntimeMode.SYSTEM.value,
        )
        authority.acquire_available(_tradable_contexts(directory))
        if not directory.bindings:
            return TradingRuntimeResources(source=_SystemCommandEventLine(launch_directory)), authority
        primary = directory.bindings[0].books[0]
        account = SimulatedAccount(
            str(primary.identity.account_id),
            Decimal("0"),
            cash_currency="USD",
            broker=str(primary.identity.broker),
            environment=primary.environment,
            book=primary.account.book,
        )
        coordinator = ExecutionCoordinator()
        capabilities = _system_capabilities(directory)
        fees = _system_fees(directory)
        account_service = SimulatedAccountService(account, coordinator, directory=directory, capabilities=capabilities, fees=fees)
        execution = SimulatedExecutionService(coordinator, account=primary, cash_currency="USD", price_field="close", directory=directory)
        account_port = AuthorizingAccountPort(account_service, authority)
        execution_port = AuthorizingTradingExecutionService(execution, authority)
        return (
            TradingRuntimeResources(
                source=_SystemCommandEventLine(launch_directory),
                account=account_port,
                trading_execution=execution_port,
            ),
            authority,
        )

    def launch_paper_config(
        self,
        config_path: str | Path,
        *,
        market_feed_factory: PaperMarketFeedFactory | None = None,
    ) -> PaperLaunchResult:
        try:
            path = Path(config_path)
            configured = configured_paper(path, market_feed_factory=market_feed_factory, account_resolver=self._account_resolver(path))
        except PaperConfigurationError as error:
            raise TradingConfigurationError(str(error)) from error
        return self.launch_configured_paper(configured)

    def launch_configured_paper(self, configured: ConfiguredPaper) -> PaperLaunchResult:
        def run() -> PaperLaunchResult:
            resources = PaperAccountResources.from_configured(configured)
            runtime = self._launch_configured(
                launch_id=configured.launch_id,
                mode=RuntimeMode.PAPER,
                strategy=configured.strategy,
                launch_directory=configured.launch_directory,
                normalized_config=configured.normalized_config,
                resources=TradingRuntimeResources(
                    source=configured.market_data,
                    data=configured.market_data,
                    account=resources.account,
                    trading_execution=resources.execution,
                ),
            )
            result = resources.build_result(configured, runtime)
            self._write_account_status(configured.launch_directory, result)
            self._write_artifacts(configured.launch_directory, result, configured.normalized_config)
            return result

        return self._with_account_leases(configured, RuntimeMode.PAPER, run)

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
                broker_factory=broker_factory,
                account_resolver=self._account_resolver(path),
            )
            return self.launch_configured_live(configured)
        except LiveConfigurationError as error:
            raise TradingConfigurationError(str(error)) from error

    def launch_configured_live(self, configured: ConfiguredLive) -> LiveLaunchResult:
        def run() -> LiveLaunchResult:
            account_resources = LiveAccountResources.from_configured(configured)
            runtime = self._launch_configured(
                launch_id=configured.launch_id,
                mode=RuntimeMode.LIVE,
                strategy=configured.strategy,
                launch_directory=configured.launch_directory,
                normalized_config=configured.normalized_config,
                lifecycle=_LiveConfiguredLifecycle(configured.state_path, account=account_resources.account, coordinator=account_resources.coordinator),
                resources=TradingRuntimeResources(
                    source=configured.market_data,
                    data=configured.market_data,
                    account=account_resources.account,
                    trading_execution=account_resources.execution,
                ),
            )
            result = account_resources.build_result(configured, runtime)
            self._write_account_status(configured.launch_directory, result)
            self._write_artifacts(configured.launch_directory, result, configured.normalized_config)
            return result

        return self._with_account_leases(configured, RuntimeMode.LIVE, run)

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
        return self._launch_configured(
            launch_id=launch_id,
            mode=runtime_mode,
            strategy=strategy,
            launch_directory=Path(launch_directory) if launch_directory is not None else Path(".kairos/launches") / runtime_mode.value / launch_id,
            normalized_config={
                "launch": {"id": launch_id, "mode": runtime_mode.value, "strategy": strategy_path},
                "events": {"source": str(event_path)},
            },
            resources=TradingRuntimeResources(source=RuntimeLine(self._read_event_jsonl(event_path))),
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
                resources=TradingRuntimeResources(),
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
        LaunchOutput(launch_directory).write_result(result=result, normalized_config=normalized_config)

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

    def _with_account_leases(self, configured: object, mode: RuntimeMode, run: Callable[[], ResultT]) -> ResultT:
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


class _LiveConfiguredLifecycle:
    def __init__(self, state_path: Path, *, account: LiveAccountService, coordinator: ExecutionCoordinator) -> None:
        self.account = account
        self.coordinator = coordinator
        self.state_store = JsonLiveRuntimeStateStore(state_path)

    def prepare(self) -> None:
        snapshot = self.state_store.load()
        if snapshot is not None:
            snapshot.restore_into(self.coordinator, self.account.private_stream_state)
        self.account.refresh()

    def complete(self) -> None:
        self.state_store.save(self.coordinator, self.account.private_stream_state)


class _SystemTradeAuthorityLifecycle:
    def __init__(self, authority: AccountTradeAuthority) -> None:
        self.authority = authority

    def prepare(self) -> None:
        return None

    def complete(self) -> None:
        self.authority.release()


def _configured_account_from_record(account: AccountRecord, *, workspace: KairosWorkspace | None = None) -> ConfiguredAccount:
    _ = workspace
    return ConfiguredAccount(
        account.account_id,
        _int_value(account.values.get("index", 0)),
        account.venue or account.provider,
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


def _system_account_directory(workspace: KairosWorkspace) -> LaunchAccountDirectory:
    bindings: list[LaunchAccountBinding] = []
    for index, record in enumerate(workspace.accounts.list()):
        contexts = tuple(_system_account_context(record, book) for book in record.books)
        if contexts:
            bindings.append(LaunchAccountBinding(record.account_id, index, contexts, ref=record.account_id))
    return LaunchAccountDirectory(tuple(bindings))


def _system_account_context(record: AccountRecord, book: object) -> AccountContext:
    ref = getattr(book, "to_ref")(record.identity)
    return AccountContext(ref, _environment(record.environment))


def _environment(value: object) -> Environment:
    text = str(value).strip().lower()
    aliases = {"sandbox": "testnet"}
    return Environment(aliases.get(text, text))


def _tradable_contexts(directory: LaunchAccountDirectory) -> tuple[AccountContext, ...]:
    return tuple(context for context in directory.contexts() if account_book_route(context.account, provider=str(context.account.broker)).can_trade)


def _system_capabilities(directory: LaunchAccountDirectory) -> tuple[AccountCapability, ...]:
    capabilities: list[AccountCapability] = []
    for context in directory.contexts():
        route = account_book_route(context.account, provider=str(context.account.broker))
        kind = str(context.account.book)
        can_hold_position = kind not in {AccountBookKind.FUNDING.value, AccountBookKind.EARN.value}
        can_borrow = kind in {AccountBookKind.CROSS_MARGIN.value, AccountBookKind.ISOLATED_MARGIN.value}
        capabilities.append(
            AccountCapability(
                context.account,
                can_trade=route.can_trade,
                can_hold_cash=True,
                can_hold_position=can_hold_position,
                can_borrow=can_borrow,
            )
        )
    return tuple(capabilities)


def _system_fees(directory: LaunchAccountDirectory) -> tuple[AccountFeeSchedule, ...]:
    return tuple(AccountFeeSchedule(context.account, maker=Decimal("0"), taker=Decimal("0")) for context in directory.contexts())


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
    books = launch_account.books or (_default_trade_book(configured),)
    for book in books:
        ref = AccountRef(account.venue, account.account_id, book)
        if account_book_route(ref, provider=str(ref.broker)).can_trade:
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


def _exchange_name(value: object) -> ExchangeName:
    text = str(value).strip().lower()
    if text == "okex":
        text = "okx"
    try:
        return ExchangeName(text)
    except ValueError as error:
        raise ValueError(f"unsupported market data exchange: {value}") from error


__all__ = ["TradingConfigurationError", "TradingSystemLauncher"]
