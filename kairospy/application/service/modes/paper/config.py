from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from kairospy.application.runtime import RuntimeMode
from kairospy.application.runtime.ports import MarketDataSubscriptionSpec
from kairospy.application.service.domain.market import IterableMarketEventSource
from kairospy.application.strategy import Strategy
from kairospy.core.account import AccountContext
from kairospy.core.market import Quote
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.integrations.protocols import LiveMarketDataFeed

from ..common import (
    AccountPerformanceMixin,
    AccountResolver,
    ConfiguredAccount,
    configured_account_ref as common_configured_account_ref,
    default_market_feed as common_default_market_feed,
    load_required_run_config,
    load_strategy as common_load_strategy,
    params_table as common_params_table,
    read_jsonl as common_read_jsonl,
    required_text as common_required_text,
    resolve_path as common_resolve_path,
    strategy_params as common_strategy_params,
    table as common_table,
)
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
    decision_trace: tuple[object, ...] = ()
    risk_snapshots: tuple[object, ...] = ()
    metrics: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConfiguredPaper:
    run_id: str
    strategy: Strategy
    source: object | None
    source_value: str
    run_directory: Path
    normalized_config: Mapping[str, object]
    account_config: ConfiguredAccount
    paper_config: Mapping[str, object]
    execution_config: Mapping[str, object]
    market_data: PaperMarketDataService


def configured_paper(
    config_path: Path,
    *,
    market_feed_factory: MarketFeedFactory | None = None,
    account_resolver: AccountResolver | None = None,
    strategy_ref: str | None = None,
) -> ConfiguredPaper:
    run_config = load_required_run_config(config_path, mode=RuntimeMode.PAPER, error_type=PaperConfigurationError, strategy_ref=strategy_ref)
    paper = _table(run_config.values.get("paper"), "paper")
    market_config = _table(run_config.values.get("market"), "market") if run_config.values.get("market") is not None else {}
    timeline_config = _table(run_config.values.get("timeline"), "timeline") if run_config.values.get("timeline") is not None else {}
    execution_config = _table(run_config.values.get("execution"), "execution") if run_config.values.get("execution") is not None else {}
    account_config = _configured_account(
        run_config.account_ref,
        account_resolver=account_resolver,
        venue=None if (market_config.get("venue") or paper.get("venue")) is None else str(market_config.get("venue") or paper.get("venue")),
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
        venue = _required_text(market_config.get("venue") or paper.get("venue"), "market.venue")
        market = str(market_config.get("market") or paper.get("market") or "spot")
        symbol = market_config.get("symbol") or paper.get("symbol")
        feed = (market_feed_factory or _default_market_feed)(venue)
        source = None
        source_value = f"{venue}:{market}" if symbol is None else f"{venue}:{market}:{symbol}"
        source_config = {"source": source_value, "venue": venue, "market": market}
        if symbol is not None:
            market_ref = MarketResolver(default_venue=venue, default_market=market).resolve(symbol)
            source_config = {**source_config, "symbol": symbol}
        else:
            market_ref = None
        market_data = PaperMarketDataService(feed=feed, source_name=str(paper.get("source_name") or f"{venue}-paper"))
        if market_ref is not None:
            market_data.subscribe(MarketDataSubscriptionSpec(market_ref, (Quote,), params=_params_table(paper.get("stream"), default={"type": market})))
    return ConfiguredPaper(
        run_id=run_config.run_id,
        strategy=_load_strategy(run_config.strategy, root=run_config.root, params=_strategy_params(run_config.values)),
        source=source,
        source_value=source_value,
        run_directory=_run_directory(paper, root=run_config.root, run_id=run_config.run_id),
        normalized_config={
            "run": {"id": run_config.run_id, "mode": RuntimeMode.PAPER.value, "strategy": run_config.strategy},
            "strategy": {"params": dict(_strategy_params(run_config.values))},
            "paper": dict(paper),
            "market": {**dict(market_config), **source_config},
            "account": {"cash": account_config.cash, "currency": account_config.currency, "fee_rate": account_config.fee_rate},
            "execution": dict(execution_config),
            "timeline": dict(timeline_config),
        },
        account_config=account_config,
        paper_config=paper,
        execution_config=execution_config,
        market_data=market_data,
    )


def _load_strategy(ref: str, *, root: Path, params: Mapping[str, object]) -> Strategy:
    return common_load_strategy(ref, root=root, params=params, error_type=PaperConfigurationError)


def _strategy_params(values: Mapping[str, object]) -> Mapping[str, object]:
    return common_strategy_params(values, PaperConfigurationError)


def _configured_account(account_ref: str | None, *, account_resolver: AccountResolver | None, venue: str | None) -> ConfiguredAccount:
    account = common_configured_account_ref(
        account_ref,
        account_resolver=account_resolver,
        venue=venue,
        mode_label="paper",
        error_type=PaperConfigurationError,
    )
    if account.environment and account.environment not in {"paper", "sandbox", "simulation", "testnet"}:
        raise PaperConfigurationError(
            f"account {account.account_id!r} has environment {account.environment!r}; paper runs require a simulated account"
        )
    return account


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
