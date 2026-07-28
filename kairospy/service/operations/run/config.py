from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import importlib
import json
from pathlib import Path
import sys
from typing import Callable, Mapping

from kairospy.config import load_run_config
from kairospy.context import DataContext, StrategyContext
from kairospy.core.market import MarketSubscription, STREAM_TICKER
from kairospy.core.reference import MarketResolver
from kairospy.data import DataStore
from kairospy.modes.backtest import BacktestEngine, SimulatedAccount
from kairospy.modes.paper import PaperAccountConfig, PaperEngine, run_streaming_paper
from kairospy.runtime import AccountRegistry, AsyncIterableEventSource, IterableEventSource
from kairospy.runtime.account_journal import RunAccountJournal
from kairospy.runtime.daemon import RunDaemonTarget, RunExecutionContext
from kairospy.runtime.line import RuntimeMode


ExchangeFactory = Callable[[str], object]


class RunConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ConfiguredEventMode:
    run_id: str
    engine: BacktestEngine | PaperEngine
    source: IterableEventSource


@dataclass(frozen=True, slots=True)
class _ConfiguredAccount:
    account_id: str
    venue: str
    cash: Decimal
    currency: str
    fee_rate: Decimal


@dataclass(frozen=True, slots=True)
class _StreamingPaperDaemonTarget:
    strategy: object
    account: PaperAccountConfig
    market_resolver: MarketResolver
    run_id: str
    exchange_factory: ExchangeFactory

    def run(self, context: RunExecutionContext) -> dict[str, object]:
        from kairospy.modes.paper import paper_result_summary

        context.heartbeat(metrics={"mode_run_status": "running", "source": "streaming"})
        controller = _ConfiguredPaperSourceController(context, self.exchange_factory)
        journal = RunAccountJournal(
            context.control.directory,
            run_id=self.run_id,
            mode=context.mode.value,
        )
        result = asyncio.run(
            run_streaming_paper(
                self.strategy,
                controller,
                account=self.account,
                market_resolver=self.market_resolver,
                account_journal=journal,
            )
        )
        journal.record_backtest_result(
            result,
            run_id=self.run_id,
            mode=context.mode.value,
        )
        summary = paper_result_summary(result)
        return {"run_id": self.run_id, **summary}


class _ConfiguredPaperSourceController:
    def __init__(self, context: RunExecutionContext, exchange_factory: ExchangeFactory) -> None:
        self.context = context
        self.exchange_factory = exchange_factory
        self.subscription: MarketSubscription | None = None

    def update_subscriptions(
        self,
        subscriptions: tuple[MarketSubscription, ...],
        context: StrategyContext,
        hook: str,
    ) -> None:
        ticker_subscriptions = tuple(
            subscription
            for subscription in subscriptions
            if any(plan.channel == STREAM_TICKER for plan in subscription.stream_plans)
        )
        if ticker_subscriptions:
            self.subscription = ticker_subscriptions[0]
        self.context.heartbeat(
            metrics={
                "mode_run_status": "subscribed",
                "subscriptions": len(subscriptions),
                "hook": hook,
            }
        )

    def source(self) -> AsyncIterableEventSource:
        if self.subscription is None:
            raise RuntimeError("strategy did not subscribe to ticker market data")
        subscription = self.subscription
        venue = subscription.venue
        if not venue:
            raise RuntimeError("market subscription has no venue")
        source_symbol = subscription.source_symbol
        if not source_symbol:
            raise RuntimeError("market subscription has no source symbol")
        exchange_client = self.exchange_factory(venue)
        return AsyncIterableEventSource(
            subscription.stream,
            self._stoppable_rows(exchange_client.watch_ticker(source_symbol, params={"require_ws": True})),
        )

    async def _stoppable_rows(self, rows):
        async for row in rows:
            if self.context.stop_requested:
                break
            yield _paper_ticker_price_fallback(row)
            if self.context.stop_requested:
                break


def configured_streaming_paper_target(config_path: Path, *, exchange_factory: ExchangeFactory) -> RunDaemonTarget:
    run_config = load_run_config(config_path)
    run_config.require_mode(RuntimeMode.PAPER.value)
    strategy = _load_strategy(run_config.strategy, root=run_config.root)
    account_config = _configured_account(run_config, RuntimeMode.PAPER)
    mode_config = _table(run_config.values.get(RuntimeMode.PAPER.value), RuntimeMode.PAPER.value)
    market_resolver = MarketResolver(
        default_venue=str(mode_config.get("venue", account_config.venue)),
        default_market=str(mode_config.get("market", "spot")),
    )
    return _StreamingPaperDaemonTarget(
        strategy,
        PaperAccountConfig(
            account_id=account_config.account_id,
            cash=account_config.cash,
            cash_currency=account_config.currency,
            broker=account_config.venue,
            fee_rate=account_config.fee_rate,
            price_field=str(mode_config.get("price_field", "ask")),
        ),
        market_resolver,
        run_config.run_id,
        exchange_factory,
    )


def configured_event_mode(mode: RuntimeMode, config_path: Path, *, events: Path | None = None) -> ConfiguredEventMode:
    run_config = load_run_config(config_path)
    run_config.require_mode(mode.value)
    strategy = _load_strategy(run_config.strategy, root=run_config.root)
    mode_config = _table(run_config.values.get(mode.value), mode.value)
    event_path = _resolve_path(events or mode_config.get("events"), root=run_config.root, source=f"{mode.value}.events")
    rows = _read_jsonl(event_path)
    account_config = _configured_account(run_config, mode)
    account = SimulatedAccount(
        account_config.account_id,
        account_config.cash,
        cash_currency=account_config.currency,
        broker=account_config.venue,
        fee_rate=account_config.fee_rate,
    )
    market_resolver = MarketResolver(
        default_venue=str(mode_config.get("venue", "simulated")),
        default_market=str(mode_config.get("market", "spot")),
    )
    data = DataContext(DataStore(":unused:", storage_format="jsonl"))
    engine = (
        PaperEngine(strategy, data, account, market_resolver=market_resolver)
        if mode is RuntimeMode.PAPER
        else BacktestEngine(strategy, data, account, market_resolver=market_resolver)
    )
    source = IterableEventSource(str(mode_config.get("stream", event_path.stem)), rows)
    return ConfiguredEventMode(run_config.run_id, engine, source)


def _configured_account(run_config, mode: RuntimeMode):
    accounts = run_config.accounts
    if accounts:
        registry = AccountRegistry.from_config(accounts.values())
        configured_accounts = registry.accounts
        if mode is RuntimeMode.PAPER and len(configured_accounts) != 1:
            raise RunConfigurationError("configured paper event runs require exactly one [accounts.*] account")
        return configured_accounts[0]
    defaults = run_config.account_defaults
    return _ConfiguredAccount(
        run_config.run_id,
        "simulated",
        defaults.cash,
        defaults.currency,
        defaults.fee_rate,
    )


def _paper_ticker_price_fallback(row):
    if not isinstance(row, Mapping) or str(row.get("kind") or "") != "ticker":
        return row
    fallback = row.get("last")
    if fallback is None:
        return row
    updated = dict(row)
    if updated.get("bid1") is None and updated.get("bid") is None:
        updated["bid1"] = fallback
    if updated.get("ask1") is None and updated.get("ask") is None:
        updated["ask1"] = fallback
    return updated


def _load_strategy(ref: str | None, *, root: Path) -> object:
    if ref is None or ":" not in ref:
        raise RunConfigurationError("run.strategy must be module:callable")
    module_name, attr_name = ref.split(":", 1)
    project_root = _project_root(root)
    inserted = False
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
        inserted = True
    try:
        module = importlib.import_module(module_name)
    finally:
        if inserted:
            try:
                sys.path.remove(str(project_root))
            except ValueError:
                pass
    factory = getattr(module, attr_name)
    strategy = factory()
    if not hasattr(strategy, "on_market"):
        raise RunConfigurationError(f"strategy factory did not return a Strategy: {ref}")
    return strategy


def _project_root(root: Path) -> Path:
    for directory in (root, *root.parents):
        if (directory / "pyproject.toml").exists() or (directory / "kairos.toml").exists():
            return directory
    return root


def _table(value: object, name: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RunConfigurationError(f"[{name}] must be a table")
    return value


def _resolve_path(value: object, *, root: Path, source: str) -> Path:
    if value is None:
        raise RunConfigurationError(f"{source} is required")
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if not isinstance(value, dict):
                raise RunConfigurationError(f"event row must be a JSON object: {path}")
            rows.append(value)
    if not rows:
        raise RunConfigurationError(f"event file has no rows: {path}")
    return rows


__all__ = [
    "ConfiguredEventMode",
    "ExchangeFactory",
    "RunConfigurationError",
    "configured_event_mode",
    "configured_streaming_paper_target",
]
