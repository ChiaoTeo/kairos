from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
import importlib
import json
from pathlib import Path
import sys
from typing import Mapping

from kairospy.application.runtime import RuntimeMode, RuntimeRunSpec, RuntimeRunner
from kairospy.application.runtime.services import MarketDataSubscriptionSpec
from kairospy.application.runtime.services.account import account_current_view_key
from kairospy.application.service.domain.account import SimulatedAccount
from kairospy.application.service.domain.execution import BasisPointSlippageModel, ImmediateFillModel, PercentageCommissionModel
from kairospy.application.service.domain.market import IterableMarketEventSource
from kairospy.application.service.system.run.accounts import AccountRegistry, RuntimeAccount
from kairospy.application.strategy import Strategy
from kairospy.config import ConfigError, load_run_config
from kairospy.core.account import AccountContext, Environment
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.market import Quote
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.integrations import BinanceMarketDataConnector, CcxtDriver, OkxMarketDataConnector
from kairospy.infrastructure.integrations.protocols import LiveMarketDataFeed

from .account import PaperAccountService
from .execution import PaperExecutionService
from .market import PaperMarketDataService


class PaperConfigurationError(ValueError):
    pass


MarketFeedFactory = Callable[[str], LiveMarketDataFeed]


@dataclass(frozen=True, slots=True)
class PaperRunResult:
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

    @property
    def initial_equity(self) -> Decimal:
        value = getattr(self.account_view, "initial_equity", None)
        if value is not None:
            return Decimal(str(value))
        value = getattr(self.account_view, "cash", None)
        return Decimal("0") if value is None else Decimal(str(value))

    @property
    def final_equity(self) -> Decimal:
        value = getattr(self.account_view, "equity", None)
        return Decimal("0") if value is None else Decimal(str(value))

    @property
    def net_profit(self) -> Decimal:
        value = getattr(self.account_view, "net_profit", None)
        return self.final_equity - self.initial_equity if value is None else Decimal(str(value))

    @property
    def total_return(self) -> Decimal:
        value = getattr(self.account_view, "total_return", None)
        if value is not None:
            return Decimal(str(value))
        return Decimal("0") if self.initial_equity == 0 else self.net_profit / self.initial_equity


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

    def run(self) -> PaperRunResult:
        runtime = RuntimeRunner.run_sync(
            RuntimeRunSpec(
                run_id=self.run_id,
                mode=RuntimeMode.PAPER,
                strategy=self.strategy,
                source=self.market_data,
                data=self.market_data,
                account=self.account,
                execution=self.coordinator,
                providers=(self.execution,),
            )
        )
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
    try:
        run_config = load_run_config(config_path)
        run_config.require_mode(RuntimeMode.PAPER.value)
    except ConfigError as error:
        raise PaperConfigurationError(str(error)) from error
    if run_config.strategy is None:
        raise PaperConfigurationError("run.strategy is required")
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
    if ":" not in ref:
        raise PaperConfigurationError("run.strategy must be module:callable")
    module_name, attr_name = ref.split(":", 1)
    inserted = False
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
        inserted = True
    if inserted:
        sys.modules.pop(module_name, None)
    try:
        module = importlib.import_module(module_name)
    finally:
        if inserted:
            try:
                sys.path.remove(str(root))
            except ValueError:
                pass
    strategy = getattr(module, attr_name)(**dict(params))
    if not hasattr(strategy, "strategy_id"):
        raise PaperConfigurationError(f"strategy factory did not return a Strategy: {ref}")
    return strategy


def _strategy_params(values: Mapping[str, object]) -> Mapping[str, object]:
    strategy = _table(values.get("strategy"), "strategy") if values.get("strategy") is not None else {}
    params = strategy.get("params", {})
    if not isinstance(params, Mapping):
        raise PaperConfigurationError("[strategy.params] must be a table")
    return params


def _slippage_model(execution: Mapping[str, object]) -> BasisPointSlippageModel | None:
    bps = execution.get("slippage_bps")
    return None if bps is None else BasisPointSlippageModel(Decimal(str(bps)))


def _configured_account(accounts: object, *, mode_config: Mapping[str, object], default_venue: str) -> RuntimeAccount:
    registry = AccountRegistry.from_config(accounts)  # type: ignore[arg-type]
    if registry.accounts:
        try:
            return registry.resolve(
                venue=default_venue,
                account=_account_selector(mode_config.get("account"), "paper.account"),
                account_id=_optional_text(mode_config.get("account_id"), "paper.account_id"),
                account_index=_optional_int(mode_config.get("account_index"), "paper.account_index"),
            )
        except ValueError as error:
            raise PaperConfigurationError(str(error)) from error
    raise PaperConfigurationError("[accounts] table is required for paper runs")


def _default_market_feed(venue: str) -> LiveMarketDataFeed:
    normalized = venue.strip().lower()
    if normalized == "binance":
        return BinanceMarketDataConnector(CcxtDriver())
    if normalized in {"okx", "okex"}:
        return OkxMarketDataConnector()
    raise PaperConfigurationError(f"unsupported paper market data venue: {venue}")


def _params_table(value: object, *, default: Mapping[str, object] | None = None) -> Mapping[str, object]:
    values = dict(default or {})
    if value is None:
        return values
    if not isinstance(value, Mapping):
        raise PaperConfigurationError("paper params must be a table")
    values.update(value)
    return values


def _run_directory(paper: Mapping[str, object], *, root: Path, run_id: str) -> Path:
    runs_root = _resolve_path(paper.get("runs_root", ".kairos/runs"), root=root, source="paper.runs_root")
    return runs_root / RuntimeMode.PAPER.value / run_id


def _table(value: object, name: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PaperConfigurationError(f"[{name}] must be a table")
    return value


def _resolve_path(value: object, *, root: Path, source: str) -> Path:
    if value is None:
        raise PaperConfigurationError(f"{source} is required")
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _required_text(value: object, source: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PaperConfigurationError(f"{source} is required")
    return text


def _optional_text(value: object, source: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, source)


def _optional_int(value: object, source: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise PaperConfigurationError(f"{source} must be an integer")
    try:
        parsed = int(value)
    except Exception as error:
        raise PaperConfigurationError(f"{source} must be an integer") from error
    if parsed < 0:
        raise PaperConfigurationError(f"{source} cannot be negative")
    return parsed


def _account_selector(value: object, source: str) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise PaperConfigurationError(f"{source} must be an account id or integer account index")
    if isinstance(value, int):
        return value
    return _required_text(value, source)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise PaperConfigurationError(f"event row must be a JSON object: {path}")
                rows.append(value)
    if not rows:
        raise PaperConfigurationError(f"event file has no rows: {path}")
    return rows


__all__ = ["ConfiguredPaper", "PaperConfigurationError", "PaperRunResult", "configured_paper"]
