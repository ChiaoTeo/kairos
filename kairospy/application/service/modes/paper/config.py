from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from kairospy.config import LaunchAccountConfig
from kairospy.application.modes import RuntimeMode
from kairospy.application.ports import MarketDataSubscriptionSpec
from kairospy.application.domain.market import IterableMarketEventSource
from kairospy.application.runtime.services.market import RuntimeIterableMarketEventSource
from kairospy.application.strategy import Strategy
from kairospy.core.account import AccountContext
from kairospy.application.ports import MarketStreamGateway

from ..common import (
    AccountPerformanceMixin,
    AccountResolver,
    ConfiguredAccount,
    configured_market_feed_for_subscription as common_configured_market_feed_for_subscription,
    configured_account_ref as common_configured_account_ref,
    default_market_feed as common_default_market_feed,
    load_required_launch_config,
    load_strategy as common_load_strategy,
    parse_feeds as common_parse_feeds,
    params_table as common_params_table,
    read_jsonl as common_read_jsonl,
    resolve_path as common_resolve_path,
    strategy_params as common_strategy_params,
    table as common_table,
)
from .market import PaperMarketDataService


class PaperConfigurationError(ValueError):
    pass


MarketFeedFactory = Callable[[str], MarketStreamGateway]
MarketFeedResolverFactory = Callable[[MarketDataSubscriptionSpec], MarketStreamGateway | None]


@dataclass(frozen=True, slots=True)
class PaperLaunchResult(AccountPerformanceMixin):
    launch_id: str
    mode: RuntimeMode
    runtime: object
    views: object
    intents: object
    account: AccountContext
    account_view: object | None
    fills: tuple[object, ...] = ()
    trades: tuple[object, ...] = ()
    decision_trace: tuple[object, ...] = ()
    risk_snapshots: tuple[object, ...] = ()
    metrics: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConfiguredPaper:
    launch_id: str
    strategy: Strategy
    source: object | None
    launch_directory: Path
    normalized_config: Mapping[str, object]
    account_config: ConfiguredAccount
    launch_account_configs: Mapping[str, ConfiguredAccount]
    launch_accounts: Mapping[str, LaunchAccountConfig]
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
    launch_config = load_required_launch_config(config_path, mode=RuntimeMode.PAPER, error_type=PaperConfigurationError, strategy_ref=strategy_ref)
    paper = _table(launch_config.values.get("paper"), "paper")
    market_config = _table(launch_config.values.get("market"), "market") if launch_config.values.get("market") is not None else {}
    feeds_config = common_parse_feeds(launch_config.values.get("feeds"), error_type=PaperConfigurationError)
    timeline_config = _table(launch_config.values.get("timeline"), "timeline") if launch_config.values.get("timeline") is not None else {}
    execution_config = _table(launch_config.values.get("execution"), "execution") if launch_config.values.get("execution") is not None else {}
    account_ref = launch_config.account_ref or _primary_launch_account_ref(launch_config.launch_accounts)
    account_config = _configured_account(
        account_ref,
        account_resolver=account_resolver,
        venue=None,
    )
    launch_account_configs = _configured_launch_accounts(
        launch_config.launch_accounts,
        account_resolver=account_resolver,
        venue=None,
    )
    source: IterableMarketEventSource | None
    market_data: PaperMarketDataService
    if paper.get("events") is not None:
        source_path = _resolve_path(paper.get("events"), root=launch_config.root, source="paper.events")
        source = IterableMarketEventSource(str(paper.get("stream") or source_path.stem), _read_jsonl(source_path))
        market_data = PaperMarketDataService(RuntimeIterableMarketEventSource(source), source_name=str(paper.get("source_name") or source_path.stem))
    else:
        feed_resolver = _market_feed_resolver(market_feed_factory, feeds=feeds_config)
        source = None
        market_data = PaperMarketDataService(feed_resolver=feed_resolver, source_name=str(paper.get("source_name") or "paper"))
    return ConfiguredPaper(
        launch_id=launch_config.launch_id,
        strategy=_load_strategy(launch_config.strategy, root=launch_config.root, params=_strategy_params(launch_config.values)),
        source=source,
        launch_directory=_launch_directory(paper, root=launch_config.root, launch_id=launch_config.launch_id),
        normalized_config={
            "launch": {"id": launch_config.launch_id, "mode": RuntimeMode.PAPER.value, "strategy": launch_config.strategy},
            "strategy": {"params": dict(_strategy_params(launch_config.values))},
            "paper": dict(paper),
            "feeds": {key: dict(feed.values or {}) for key, feed in feeds_config.items()},
            **({} if not market_config else {"market": dict(market_config)}),
            "account": {"ref": account_ref, "cash": account_config.cash, "currency": account_config.currency, "fee_rate": account_config.fee_rate},
            "accounts": {key: {"ref": value.ref, "index": value.index, "books": list(value.books), "trade": value.trade} for key, value in launch_config.launch_accounts.items()},
            "execution": dict(execution_config),
            "timeline": dict(timeline_config),
        },
        account_config=account_config,
        launch_account_configs=launch_account_configs,
        launch_accounts=launch_config.launch_accounts,
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
            f"account {account.account_id!r} has environment {account.environment!r}; paper launches require a simulated account"
        )
    return account


def _configured_launch_accounts(
    accounts: Mapping[str, LaunchAccountConfig],
    *,
    account_resolver: AccountResolver | None,
    venue: str | None,
) -> Mapping[str, ConfiguredAccount]:
    return {alias: _configured_account(config.ref, account_resolver=account_resolver, venue=venue) for alias, config in accounts.items()}


def _primary_launch_account_ref(accounts: Mapping[str, object]) -> str | None:
    if not accounts:
        return None
    first = next(iter(accounts.values()))
    return str(getattr(first, "ref", "") or "") or None


def _default_market_feed(venue: str) -> MarketStreamGateway:
    return common_default_market_feed(venue, mode_label="paper", error_type=PaperConfigurationError)


def _market_feed_resolver(factory: MarketFeedFactory | None, *, feeds: Mapping[str, object]) -> MarketFeedResolverFactory:
    if factory is not None:
        return lambda spec: factory(str(spec.market.venue))
    return lambda spec: common_configured_market_feed_for_subscription(spec, feeds=feeds, mode_label="paper", error_type=PaperConfigurationError)


def _params_table(value: object, *, default: Mapping[str, object] | None = None) -> Mapping[str, object]:
    return common_params_table(value, default=default, source="paper", error_type=PaperConfigurationError)


def _launch_directory(paper: Mapping[str, object], *, root: Path, launch_id: str) -> Path:
    launches_root = Path(".kairos/launches").resolve() if paper.get("launches_root") is None else _resolve_path(paper["launches_root"], root=root, source="paper.launches_root")
    return launches_root / RuntimeMode.PAPER.value / launch_id


def _table(value: object, name: str) -> Mapping[str, object]:
    return common_table(value, name, PaperConfigurationError)


def _resolve_path(value: object, *, root: Path, source: str) -> Path:
    return common_resolve_path(value, root=root, source=source, error_type=PaperConfigurationError)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return common_read_jsonl(path, PaperConfigurationError)


__all__ = ["ConfiguredPaper", "PaperConfigurationError", "PaperLaunchResult", "configured_paper"]
