from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Mapping

from kairospy.application.runtime import MergedRuntimeEventLine, RuntimeEnvelope, RuntimeEventLine, RuntimeMode
from kairospy.application.runtime.ports import AccountPort, MarketDataPort, ReferencePort, TradingExecutionPort
from kairospy.application.runtime.sources import RuntimeDataSource
from kairospy.application.strategy import Strategy
from kairospy.application.system.artifacts.logging import RunOutputLog
from kairospy.application.system.artifacts.logging import write_run_log_section
from kairospy.application.system.artifacts.writer import RunArtifactWriter
from kairospy.application.system.host.resources import TradingRuntimeResources, TradingRunSpec
from kairospy.application.system.host.runtime_host import TradingSystem


class RunBuilder:
    def __init__(self, env: object) -> None:
        self.env = env
        self._strategy: Strategy | None = None
        self._sources: list[RuntimeEventLine] = []
        self._data: MarketDataPort | None = None
        self._account: AccountPort | None = None
        self._reference: ReferencePort | None = None
        self._trading_execution: TradingExecutionPort | None = None
        self._echo = False

    def strategy(self, strategy: Strategy) -> "RunBuilder":
        self._strategy = strategy
        return self

    def source(self, source: RuntimeEventLine | RuntimeDataSource) -> "RunBuilder":
        self._sources.append(source)
        return self

    def clock(self, source: RuntimeEventLine | RuntimeDataSource) -> "RunBuilder":
        return self.source(source)

    def clocks(self, sources: Iterable[RuntimeEventLine | RuntimeDataSource]) -> "RunBuilder":
        for source in sources:
            self.clock(source)
        return self

    def sources(self, sources: Iterable[RuntimeEventLine | RuntimeDataSource]) -> "RunBuilder":
        for source in sources:
            self.source(source)
        return self

    def data(self, data: MarketDataPort) -> "RunBuilder":
        self._data = data
        return self

    def account(self, account: AccountPort) -> "RunBuilder":
        self._account = account
        return self

    def reference(self, reference: ReferencePort) -> "RunBuilder":
        self._reference = reference
        return self

    def trading_execution(self, trading_execution: TradingExecutionPort) -> "RunBuilder":
        self._trading_execution = trading_execution
        return self

    def echo(self, enabled: bool = True) -> "RunBuilder":
        self._echo = enabled
        return self

    def run(self):
        strategy = self._require_strategy()
        run_id = str(getattr(self.env, "run_id"))
        mode = RuntimeMode(str(getattr(self.env, "mode")))
        run_directory = Path(getattr(self.env, "instance_dir"))
        self._validate_instance_directory(run_directory)
        normalized_config = getattr(self.env, "normalized_config")
        if not isinstance(normalized_config, Mapping):
            normalized_config = {}
        self._write_current(run_directory, run_id=run_id, mode=mode)
        stdout = sys.stdout if self._echo else None
        write_run_log_section(
            run_directory,
            "Run Environment",
            {
                "run_id": run_id,
                "mode": mode.value,
                "run_instance_id": getattr(self.env, "run_instance_id", None),
                "run_directory": run_directory,
                "strategy_id": getattr(strategy, "strategy_id", None),
                "timezone": getattr(self.env, "timezone_name", None),
                "language": getattr(self.env, "language", None),
            },
            stdout=stdout,
        )
        write_run_log_section(run_directory, "System Status", {"phase": "starting"}, stdout=stdout)
        source = _source_line(self._sources)
        resources = TradingRuntimeResources(
            source=source,
            data=self._data,
            account=self._account,
            reference=self._reference,
            trading_execution=self._trading_execution,
        )
        with RunOutputLog(run_directory, stdout=sys.stdout if self._echo else None, stderr=sys.stderr if self._echo else None):
            result = TradingSystem(
                TradingRunSpec(
                    run_id=run_id,
                    mode=mode,
                    strategy=strategy,
                    run_directory=run_directory,
                    normalized_config=normalized_config,
                    resources=resources,
                )
            ).run()
        write_run_log_section(
            run_directory,
            "System Status",
            {
                "phase": "stopped",
                "events": getattr(result.runtime, "event_count", None),
                "intents": getattr(result.runtime, "intent_count", None),
            },
            stdout=stdout,
        )
        RunArtifactWriter(run_directory).write(result=result, normalized_config=normalized_config)
        return result

    def _require_strategy(self) -> Strategy:
        if self._strategy is None:
            raise ValueError("run builder requires a strategy")
        return self._strategy

    def _write_current(self, run_directory: Path, *, run_id: str, mode: RuntimeMode) -> None:
        group_directory = getattr(self.env, "run_group_dir", None)
        if group_directory is None:
            return
        group = Path(group_directory)
        group.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": run_id,
            "mode": mode.value,
            "run_instance_id": getattr(self.env, "run_instance_id", None),
            "directory": str(run_directory),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        (group / "current.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _validate_instance_directory(self, run_directory: Path) -> None:
        group_directory = getattr(self.env, "run_group_dir", None)
        if group_directory is None:
            return
        group = Path(group_directory).resolve()
        directory = run_directory.resolve()
        if directory == group:
            raise ValueError("run artifacts cannot be written directly to the run group directory")
        if directory.parent != group / "instances":
            raise ValueError("run artifacts must be written under <run-group>/instances/<run-instance-id>")


class OrderedEventLine:
    def __init__(self, lines: Iterable[RuntimeEventLine]) -> None:
        self.lines = tuple(lines)

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        events: list[RuntimeEnvelope] = []
        for line in self.lines:
            async for event in line.events():
                events.append(event)
        for sequence, event in enumerate(sorted(events, key=lambda item: (item.time, item.sequence)), start=1):
            yield RuntimeEnvelope(event.domain, event.kind, event.time, sequence, event.payload)


def _source_line(sources: Iterable[RuntimeEventLine]) -> RuntimeEventLine | None:
    lines = tuple(sources)
    if not lines:
        return None
    if len(lines) == 1:
        return lines[0]
    if all(getattr(line, "is_finite", False) for line in lines):
        return OrderedEventLine(lines)
    return MergedRuntimeEventLine(lines)


__all__ = ["OrderedEventLine", "RunBuilder"]
