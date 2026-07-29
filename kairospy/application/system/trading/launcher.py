from __future__ import annotations

from datetime import datetime
import importlib
import json
from pathlib import Path
from typing import Mapping

from kairospy.application.runtime import RuntimeEnvelope, RuntimeLine, RuntimeMode
from kairospy.application.runtime.run import RuntimeRunResult
from kairospy.application.service.modes.backtest import BacktestRunResult, ConfiguredBacktest, configured_backtest
from kairospy.application.service.modes.live.config import BrokerFactory, ConfiguredLive, LiveRunResult, MarketFeedFactory as LiveMarketFeedFactory, configured_live
from kairospy.application.service.modes.paper.config import ConfiguredPaper, PaperRunResult, MarketFeedFactory as PaperMarketFeedFactory, configured_paper
from kairospy.application.strategy import Strategy

from .spec import TradingRuntimeResources, TradingRunSpec
from .system import TradingSystem


class TradingSystemLauncher:
    def run_backtest_config(self, config_path: str | Path) -> BacktestRunResult:
        return self.run_configured_backtest(configured_backtest(Path(config_path)))

    def run_configured_backtest(self, configured: ConfiguredBacktest) -> BacktestRunResult:
        runtime = self._run_configured(
            run_id=configured.run_id,
            mode=RuntimeMode.BACKTEST,
            strategy=configured.strategy,
            run_directory=configured.run_directory,
            normalized_config=configured.normalized_config,
            resources=TradingRuntimeResources(
                source=configured.source,
                data=configured.data,
                account=configured.account,
                trading_execution=configured.execution,
            ),
        )
        return configured.build_result(runtime)

    def run_paper_config(
        self,
        config_path: str | Path,
        *,
        market_feed_factory: PaperMarketFeedFactory | None = None,
    ) -> PaperRunResult:
        return self.run_configured_paper(configured_paper(Path(config_path), market_feed_factory=market_feed_factory))

    def run_configured_paper(self, configured: ConfiguredPaper) -> PaperRunResult:
        runtime = self._run_configured(
            run_id=configured.run_id,
            mode=RuntimeMode.PAPER,
            strategy=configured.strategy,
            run_directory=configured.run_directory,
            normalized_config=configured.normalized_config,
            resources=TradingRuntimeResources(
                source=configured.market_data,
                data=configured.market_data,
                account=configured.account,
                trading_execution=configured.execution,
            ),
        )
        return configured.build_result(runtime)

    def run_live_config(
        self,
        config_path: str | Path,
        *,
        market_feed_factory: LiveMarketFeedFactory | None = None,
        broker_factory: BrokerFactory | None = None,
    ) -> LiveRunResult:
        return self.run_configured_live(
            configured_live(Path(config_path), market_feed_factory=market_feed_factory, broker_factory=broker_factory)
        )

    def run_configured_live(self, configured: ConfiguredLive) -> LiveRunResult:
        runtime = self._run_configured(
            run_id=configured.run_id,
            mode=RuntimeMode.LIVE,
            strategy=configured.strategy,
            run_directory=configured.run_directory,
            normalized_config=configured.normalized_config,
            lifecycle=configured,
            resources=TradingRuntimeResources(
                source=configured.market_data,
                data=configured.market_data,
                account=configured.account,
                trading_execution=configured.execution,
            ),
        )
        return configured.build_result(runtime)

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


__all__ = ["TradingSystemLauncher"]
