from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from kairospy.application.runtime import RuntimeMode
from kairospy.application.runtime.ports import MarketDataSubscriptionSpec
from kairospy.application.runtime.processors.account import account_current_view_key
from kairospy.application.runtime.run import RuntimeRunResult
from kairospy.application.service.domain.account import SimulatedAccount
from kairospy.application.service.domain.execution import BasisPointSlippageModel, ImmediateFillModel, PercentageCommissionModel
from kairospy.application.service.domain.market import IterableMarketEventSource
from kairospy.application.system.accounts import SystemAccount
from kairospy.application.strategy import Strategy
from kairospy.core.account import AccountContext, Environment
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.market import Quote
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.integrations.protocols import LiveMarketDataFeed

from .account import PaperAccountService
from ..common import (
    AccountPerformanceMixin,
    configured_account as common_configured_account,
    default_market_feed as common_default_market_feed,
    load_required_run_config,
    load_strategy as common_load_strategy,
    params_table as common_params_table,
    read_jsonl as common_read_jsonl,
    required_text as common_required_text,
    resolve_path as common_resolve_path,
    slippage_model as common_slippage_model,
    strategy_params as common_strategy_params,
    table as common_table,
)
from .execution import PaperExecutionService
from .market import PaperMarketDataService


class PaperConfigurationError(ValueError):
    pass


MarketFeedFactory = Callable[[str], LiveMarketDataFeed]


@dataclass(frozen=True, slots=True)
class PaperRunResult(AccountPerformanceMixin):
    run_id: str
    mode: RuntimeMode
    runtime: object
    views: object
    intents: object
    controls: object
    account: AccountContext
    account_view: object | None
    fills: tuple[object, ...] = ()
    trades: tuple[object, ...] = ()
    metrics: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConfiguredPaper:
    run_id: str
    strategy: Strategy
    source: object | None
    source_value: str
    run_directory: Path
    normalized_config: Mapping[str, object]
    market_data: PaperMarketDataService
    account: PaperAccountService
    execution: PaperExecutionService
    coordinator: ExecutionCoordinator

    def build_result(self, runtime: RuntimeRunResult) -> PaperRunResult:
        account_view = runtime.views.get(account_current_view_key(self.account.account.context), None)
        return PaperRunResult(
            run_id=self.run_id,
            mode=RuntimeMode.PAPER,
            runtime=runtime.runtime,
            views=runtime.views,
            intents=runtime.intents,
            controls=runtime.controls,
            account=self.account.account.context,
            account_view=account_view,
            fills=self.execution.fills,
            trades=(),
            metrics={},
        )


def configured_paper(config_path: Path, *, market_feed_factory: MarketFeedFactory | None = None) -> ConfiguredPaper:
    run_config = load_required_run_config(config_path, mode=RuntimeMode.PAPER, error_type=PaperConfigurationError)
    paper = _table(run_config.values.get("paper"), "paper")
    execution_config = _table(run_config.values.get("execution"), "execution") if run_config.values.get("execution") is not None else {}
    account_config = _configured_account(run_config.accounts.values(), mode_config=paper, default_venue=str(paper.get("venue", "paper")))
    account_config = SimulatedAccount(
        account_config.account_id,
        account_config.cash,
        cash_currency=account_config.currency,
        broker=str(paper.get("venue", "paper")),
        environment=Environment.PAPER,
        fee_rate=account_config.fee_rate,
        price_field=str(paper.get("price_field", "ask")),
    )
    source: IterableMarketEventSource | None
    source_value: str
    source_config: Mapping[str, object]
    market_data: PaperMarketDataService
    if paper.get("events") is not None:
        source_path = _resolve_path(paper.get("events"), root=run_config.root, source="paper.events")
        source = IterableMarketEventSource(str(paper.get("stream") or source_path.stem), _read_jsonl(source_path))
        source_value = str(source_path)
        source_config = {"source": source_value}
        market_data = PaperMarketDataService(source, source_name=str(paper.get("source_name") or source_path.stem))
    else:
        venue = _required_text(paper.get("venue"), "paper.venue")
        market = str(paper.get("market", "spot"))
        symbol = _required_text(paper.get("symbol"), "paper.symbol")
        market_ref = MarketResolver(default_venue=venue, default_market=market).resolve(symbol)
        feed = (market_feed_factory or _default_market_feed)(venue)
        source = None
        source_value = f"{venue}:{market}:{symbol}"
        source_config = {"source": source_value, "venue": venue, "market": market, "symbol": symbol}
        market_data = PaperMarketDataService(feed=feed, source_name=str(paper.get("source_name") or f"{venue}-paper"))
        market_data.subscribe(MarketDataSubscriptionSpec(market_ref, (Quote,), params=_params_table(paper.get("stream"), default={"type": market})))
    coordinator = ExecutionCoordinator()
    account = PaperAccountService(account_config, coordinator)
    execution = PaperExecutionService(
        coordinator,
        account=account_config.context,
        cash_currency=account_config.cash_currency,
        price_field=account_config.price_field,
        fill_model=ImmediateFillModel(volume_field=None if paper.get("volume_field") is None else str(paper["volume_field"])),
        slippage_model=_slippage_model(execution_config),
        commission_model=PercentageCommissionModel(account_config.fee_rate),
    )
    return ConfiguredPaper(
        run_id=run_config.run_id,
        strategy=_load_strategy(run_config.strategy, root=run_config.root, params=_strategy_params(run_config.values)),
        source=source,
        source_value=source_value,
        run_directory=_run_directory(paper, root=run_config.root, run_id=run_config.run_id),
        normalized_config={
            "run": {"id": run_config.run_id, "mode": RuntimeMode.PAPER.value, "strategy": run_config.strategy},
            "strategy": {"params": dict(_strategy_params(run_config.values))},
            "paper": {**dict(paper), **source_config},
            "account": {"cash": account_config.initial_cash, "currency": account_config.cash_currency},
            "execution": dict(execution_config),
        },
        market_data=market_data,
        account=account,
        execution=execution,
        coordinator=coordinator,
    )


def _load_strategy(ref: str, *, root: Path, params: Mapping[str, object]) -> Strategy:
    return common_load_strategy(ref, root=root, params=params, error_type=PaperConfigurationError)


def _strategy_params(values: Mapping[str, object]) -> Mapping[str, object]:
    return common_strategy_params(values, PaperConfigurationError)


def _slippage_model(execution: Mapping[str, object]) -> BasisPointSlippageModel | None:
    return common_slippage_model(execution)


def _configured_account(accounts: object, *, mode_config: Mapping[str, object], default_venue: str) -> SystemAccount:
    return common_configured_account(
        accounts,
        venue=default_venue,
        mode_config=mode_config,
        mode_label="paper",
        error_type=PaperConfigurationError,
    )


def _default_market_feed(venue: str) -> LiveMarketDataFeed:
    return common_default_market_feed(venue, mode_label="paper", error_type=PaperConfigurationError)


def _params_table(value: object, *, default: Mapping[str, object] | None = None) -> Mapping[str, object]:
    return common_params_table(value, default=default, source="paper", error_type=PaperConfigurationError)


def _run_directory(paper: Mapping[str, object], *, root: Path, run_id: str) -> Path:
    runs_root = Path(".kairos/runs").resolve() if paper.get("runs_root") is None else _resolve_path(paper["runs_root"], root=root, source="paper.runs_root")
    return runs_root / RuntimeMode.PAPER.value / run_id


def _table(value: object, name: str) -> Mapping[str, object]:
    return common_table(value, name, PaperConfigurationError)


def _resolve_path(value: object, *, root: Path, source: str) -> Path:
    return common_resolve_path(value, root=root, source=source, error_type=PaperConfigurationError)


def _required_text(value: object, source: str) -> str:
    return common_required_text(value, source, PaperConfigurationError)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return common_read_jsonl(path, PaperConfigurationError)


__all__ = ["ConfiguredPaper", "PaperConfigurationError", "PaperRunResult", "configured_paper"]
