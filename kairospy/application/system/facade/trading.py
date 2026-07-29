from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import importlib
import json
from pathlib import Path
from typing import Mapping

from kairospy.application.runtime import RuntimeEnvelope, RuntimeLine, RuntimeMode
from kairospy.application.runtime.run import RuntimeRunResult
from kairospy.application.service.modes.backtest import BacktestConfigurationError, BacktestRunResult, ConfiguredBacktest, configured_backtest
from kairospy.application.service.modes.live.account import LiveAccountService
from kairospy.application.service.modes.live.config import BrokerFactory, ConfiguredLive, LiveConfigurationError, LiveRunResult, MarketFeedFactory as LiveMarketFeedFactory, configured_live
from kairospy.application.service.modes.paper.config import ConfiguredPaper, PaperConfigurationError, PaperRunResult, MarketFeedFactory as PaperMarketFeedFactory, configured_paper
from kairospy.application.service.modes.common import ConfiguredAccount
from kairospy.application.system.artifacts.logging import RunOutputLog
from kairospy.application.system.artifacts.writer import RunArtifactWriter
from kairospy.application.system.host.resources import TradingRuntimeResources, TradingRunSpec
from kairospy.application.system.host.runtime_host import TradingSystem
from kairospy.application.system.resources.accounts import BacktestAccountResources, LiveAccountResources, PaperAccountResources
from kairospy.application.system.resources.live_state import JsonLiveRuntimeStateStore
from kairospy.application.system.workspace import AccountRecord, KairosWorkspace
from kairospy.application.strategy import Strategy
from kairospy.core.execution import ExecutionCoordinator


class TradingConfigurationError(ValueError):
    pass


class TradingSystemLauncher:
    def run_backtest_config(self, config_path: str | Path) -> BacktestRunResult:
        try:
            configured = configured_backtest(Path(config_path))
        except BacktestConfigurationError as error:
            raise TradingConfigurationError(str(error)) from error
        return self.run_configured_backtest(configured)

    def run_configured_backtest(self, configured: ConfiguredBacktest) -> BacktestRunResult:
        account_resources = BacktestAccountResources.from_configured(configured)
        runtime = self._run_configured(
            run_id=configured.run_id,
            mode=RuntimeMode.BACKTEST,
            strategy=configured.strategy,
            run_directory=configured.run_directory,
            normalized_config=configured.normalized_config,
            resources=TradingRuntimeResources(
                source=configured.source,
                data=configured.data,
                account=account_resources.account,
                trading_execution=account_resources.execution,
            ),
        )
        result = account_resources.build_result(configured, runtime)
        self._write_artifacts(configured.run_directory, result, configured.normalized_config)
        return result

    def run_paper_config(
        self,
        config_path: str | Path,
        *,
        market_feed_factory: PaperMarketFeedFactory | None = None,
    ) -> PaperRunResult:
        try:
            path = Path(config_path)
            configured = configured_paper(path, market_feed_factory=market_feed_factory, account_resolver=self._account_resolver(path))
        except PaperConfigurationError as error:
            raise TradingConfigurationError(str(error)) from error
        return self.run_configured_paper(configured)

    def run_configured_paper(self, configured: ConfiguredPaper) -> PaperRunResult:
        resources = PaperAccountResources.from_configured(configured)
        runtime = self._run_configured(
            run_id=configured.run_id,
            mode=RuntimeMode.PAPER,
            strategy=configured.strategy,
            run_directory=configured.run_directory,
            normalized_config=configured.normalized_config,
            resources=TradingRuntimeResources(
                source=configured.market_data,
                data=configured.market_data,
                account=resources.account,
                trading_execution=resources.execution,
            ),
        )
        result = resources.build_result(configured, runtime)
        self._write_artifacts(configured.run_directory, result, configured.normalized_config)
        return result

    def run_live_config(
        self,
        config_path: str | Path,
        *,
        market_feed_factory: LiveMarketFeedFactory | None = None,
        broker_factory: BrokerFactory | None = None,
    ) -> LiveRunResult:
        try:
            path = Path(config_path)
            configured = configured_live(
                path,
                market_feed_factory=market_feed_factory,
                broker_factory=broker_factory,
                account_resolver=self._account_resolver(path),
            )
            return self.run_configured_live(configured)
        except LiveConfigurationError as error:
            raise TradingConfigurationError(str(error)) from error

    def run_configured_live(self, configured: ConfiguredLive) -> LiveRunResult:
        account_resources = LiveAccountResources.from_configured(configured)
        runtime = self._run_configured(
            run_id=configured.run_id,
            mode=RuntimeMode.LIVE,
            strategy=configured.strategy,
            run_directory=configured.run_directory,
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
        self._write_artifacts(configured.run_directory, result, configured.normalized_config)
        return result

    def run_events(
        self,
        *,
        strategy_path: str,
        events_path: str | Path,
        run_id: str = "kairos-run",
        mode: RuntimeMode | str = RuntimeMode.BACKTEST,
        run_directory: str | Path | None = None,
    ) -> RuntimeRunResult:
        runtime_mode = mode if isinstance(mode, RuntimeMode) else RuntimeMode(str(mode))
        strategy = self._load_strategy(strategy_path)
        event_path = Path(events_path)
        return self._run_configured(
            run_id=run_id,
            mode=runtime_mode,
            strategy=strategy,
            run_directory=Path(run_directory) if run_directory is not None else Path(".kairos/runs") / runtime_mode.value / run_id,
            normalized_config={
                "run": {"id": run_id, "mode": runtime_mode.value, "strategy": strategy_path},
                "events": {"source": str(event_path)},
            },
            resources=TradingRuntimeResources(source=RuntimeLine(self._read_event_jsonl(event_path))),
        )

    def _run_configured(
        self,
        *,
        run_id: str,
        mode: RuntimeMode,
        strategy: Strategy,
        run_directory: Path,
        normalized_config: Mapping[str, object],
        resources: TradingRuntimeResources,
        lifecycle: object | None = None,
    ) -> RuntimeRunResult:
        with RunOutputLog(run_directory):
            return TradingSystem(
                TradingRunSpec(
                    run_id=run_id,
                    mode=mode,
                    strategy=strategy,
                    run_directory=run_directory,
                    normalized_config=normalized_config,
                    resources=resources,
                    lifecycle=lifecycle,
                )
            ).run()

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

    def _write_artifacts(self, run_directory: Path, result: object, normalized_config: Mapping[str, object]) -> None:
        RunArtifactWriter(run_directory).write(result=result, normalized_config=normalized_config)

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
            return _configured_account_from_record(workspace.accounts.get(account_ref))

        return resolve

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


def _configured_account_from_record(account: AccountRecord) -> ConfiguredAccount:
    return ConfiguredAccount(
        account.account_id,
        _int_value(account.values.get("index", 0)),
        account.venue or account.provider,
        _decimal_value(account.values.get("cash", "100000")),
        str(account.values.get("currency", "USD")),
        fee_rate=_decimal_value(account.values.get("fee_rate", "0")),
        credential=account.credential,
    )


def _decimal_value(value: object) -> Decimal:
    return Decimal(str(value))


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("account index must be an integer")
    return int(value)


__all__ = ["TradingConfigurationError", "TradingSystemLauncher"]
