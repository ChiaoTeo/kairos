from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from trading.accounting.repository import LedgerRepository
from trading.adapters.base import Environment, OrderRequest, ReferenceDataRequest
from trading.adapters.composite import CompositeMarketDataAdapter
from trading.adapters.binance.adapter import (
    BinanceAccountAdapter, BinanceExecutionAdapter, BinanceFuturesReferenceAdapter,
    BinanceMarketDataAdapter, BinanceOptionsAccountAdapter, BinanceOptionsExecutionAdapter, BinanceOptionsReferenceAdapter,
    BinanceSigner, BinanceSpotReferenceAdapter,
    UrllibBinanceTransport,
)
from trading.adapters.ibkr.adapter import IbkrAccountAdapter, IbkrExecutionAdapter, IbkrMarketDataAdapter, IbkrReferenceAdapter, IbkrSession
from trading.adapters.ibkr.research import IbkrSpxwResearchAdapter
from trading.adapters.simulated import SimulatedExecutionAccountAdapter
from trading.adapters.massive import MassiveClient, MassiveConfig, MassiveCuratedSliceBuilder, MassiveEquityDayAggPipeline, MassiveFlatFileBatchDownloader, MassiveFlatFileClient, MassiveOptionDataPipeline, MassiveReadinessChecker, MassiveReferencePipeline, MassiveSourceArchive, OptionDayAggPipeline, OptionDayIvPipeline, SpxwDayAggPipeline
from trading.backtest.reference_scenarios import run_reference_scenario
from trading.catalog.repository import CatalogRepository
from trading.catalog.service import InstrumentCatalog
from trading.domain.capability import OrderType
from trading.domain.execution import TradeSide
from trading.domain.identity import AccountKey, AccountType, AssetId, InstrumentId, VenueId
from trading.domain.ledger import LedgerBook
from trading.domain.order import ExecutionInstructions, TimeInForce
from trading.domain.product import OptionRight, ProductType
from trading.execution.router import ExecutionRouter
from trading.orchestration.coordinator import TradingCoordinator
from trading.orchestration.event_log import PersistentEventLog
from trading.orchestration.kill_switch import KillSwitch
from trading.orchestration.reconciliation import ReconciliationService
from trading.research.report import summarize
from trading.research.service import ResearchService
from trading.research.spec import MarketDataType, ResearchSpec
from trading.storage.repository import FileResearchRepository
from trading.backtest.engine import BacktestEngine
from trading.backtest.feed import DatasetRepository
from trading.backtest.mock import MockScenario, make_mock_dataset
from trading.backtest.repository import BacktestRepository
from trading.backtest.result import BacktestConfig
from trading.backtest.service import BacktestService
from trading.risk.limits import RiskLimits
from trading.storage.codec import from_primitive, restore_primitives
from trading.strategies.bull_put_spread import BullPutSpreadConfig, BullPutSpreadStrategy
from trading.research.series import SeriesCaptureProgress, SeriesCaptureService, SeriesCaptureSpec
from trading.research.normalized_series import NormalizedSeriesCaptureService
from trading.history import BarRepository, BinanceHistoricalBarProvider
from trading.strategies.sma_cross import SmaCrossConfig, backtest_sma_cross
from trading.pricing import PricingInput, PricingModel, ValuationService, implied_volatility, price_with_volatility
from trading.risk import RevaluationPosition, Scenario, ScenarioEngine, explain_scenario
from trading.volatility import SurfaceRepository
from trading.data import CanonicalDatasetRepository, DataCatalog, materialize_catalog_capabilities
from trading.data.pipeline import BtcOptionsDataPipeline
from trading.data.btc_options_readiness import btc_options_readiness
from trading.market_data import ParquetMarketEventRepository
from trading.features import BtcIvRvFeatureBuilder, BtcTermSkewFeatureBuilder, BtcDeribitTradeSkewFeatureBuilder


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trader", description="Multi-asset research, backtest, reconciliation, and trading toolkit")
    parser.add_argument("--data-root", default="data/snapshots")
    parser.add_argument("--dataset-root", default="data/datasets")
    parser.add_argument("--backtest-root", default="data/backtests")
    parser.add_argument("--catalog-path", default="data/catalog/instruments.json")
    parser.add_argument("--ledger-path", default="data/ledger/ledger.json")
    parser.add_argument("--event-log-path", default="data/events/trading.jsonl")
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument("--surface-root", default="data/surfaces")
    parser.add_argument("--lake-root", default="data", help="source/canonical/features/studies data lake root")
    commands = parser.add_subparsers(dest="group", required=True)
    data = commands.add_parser("data", help="prepare and inspect governed market datasets")
    data_actions = data.add_subparsers(dest="action", required=True)
    prepare_btc = data_actions.add_parser("prepare-btc-options", help="download and canonicalize BTC spot and DVOL history")
    prepare_btc.add_argument("--start", required=True, help="inclusive UTC date")
    prepare_btc.add_argument("--end", required=True, help="inclusive UTC date")
    prepare_options = data_actions.add_parser("prepare-btc-option-quotes", help="download Binance hourly BTC option EOHSummary archives")
    prepare_options.add_argument("--start", required=True, help="inclusive UTC date; archive starts 2023-05-18")
    prepare_options.add_argument("--end", required=True, help="inclusive UTC date; known archive ends 2023-10-23")
    prepare_deribit = data_actions.add_parser("prepare-deribit-option-trades", help="download anonymous Deribit BTC option trade history")
    prepare_deribit.add_argument("--start", required=True, help="inclusive UTC date")
    prepare_deribit.add_argument("--end", required=True, help="inclusive UTC date")
    data_actions.add_parser("capture-deribit-option-chain", help="append an anonymous current Deribit BTC option-chain snapshot")
    inspect_data = data_actions.add_parser("inspect", help="show schema, lineage and time coverage")
    inspect_data.add_argument("--dataset", required=True)
    data_actions.add_parser("btc-options-readiness", help="evaluate long-history surface and executable-quote gates")
    data_actions.add_parser("materialize-capabilities", help="write governed research-capability contracts for prepared catalog datasets")
    massive_fetch = data_actions.add_parser("massive-fetch", help="archive a Massive REST resource through the private server")
    massive_fetch.add_argument("--resource", choices=("option-contracts", "option-quotes", "option-trades", "aggregates", "option-chain"), required=True)
    massive_fetch.add_argument("--ticker", help="option ticker for quote/trade or underlying ticker for aggregates")
    massive_fetch.add_argument("--underlying", help="underlying ticker for contracts or current option-chain snapshot")
    massive_fetch.add_argument("--start", help="inclusive start date/timestamp")
    massive_fetch.add_argument("--end", help="exclusive end date/timestamp")
    massive_fetch.add_argument("--limit", type=int, default=50000)
    massive_fetch.add_argument("--max-pages", type=int, default=100000, help="fail closed if pagination exceeds this bound")
    massive_fetch.add_argument("--multiplier", type=int, default=1)
    massive_fetch.add_argument("--timespan", default="minute")
    massive_flat = data_actions.add_parser("massive-flat-file", help="inspect or download Massive Flat Files outside NY regular hours")
    massive_flat.add_argument("--operation", choices=("usage", "status", "download"), required=True)
    massive_flat.add_argument("--key", help="Flat File key for status/download")
    massive_flat_batch = data_actions.add_parser("massive-flat-file-batch", help="plan or download a bounded, resumable range of OPRA daily aggregates")
    massive_flat_batch.add_argument("--start", required=True, help="inclusive trading date YYYY-MM-DD")
    massive_flat_batch.add_argument("--end", required=True, help="exclusive date YYYY-MM-DD")
    massive_flat_batch.add_argument("--max-files", type=int, default=5, help="maximum non-local files to inspect/download in this run")
    massive_flat_batch.add_argument("--dry-run", action="store_true", help="only inspect cache status and write a plan")
    prepare_spxw_day_aggs = data_actions.add_parser("prepare-spxw-day-aggs", help="inventory and convert downloaded OPRA day aggregates into governed SPXW Parquet")
    prepare_spxw_day_aggs.add_argument("--dataset-id", required=True)
    prepare_spxw_day_aggs.add_argument("--start", required=True, help="inclusive date YYYY-MM-DD")
    prepare_spxw_day_aggs.add_argument("--end", required=True, help="exclusive date YYYY-MM-DD")
    prepare_option_day_aggs = data_actions.add_parser("prepare-option-day-aggs", help="convert downloaded OPRA day aggregates for one OCC root")
    prepare_option_day_aggs.add_argument("--dataset-id", required=True)
    prepare_option_day_aggs.add_argument("--option-root", required=True, help="OCC root without O: prefix, for example NVDA")
    prepare_option_day_aggs.add_argument("--start", required=True)
    prepare_option_day_aggs.add_argument("--end", required=True)
    prepare_equity_day_aggs = data_actions.add_parser("prepare-massive-equity-day-aggs", help="archive and convert adjusted Massive equity daily aggregates")
    prepare_equity_day_aggs.add_argument("--dataset-id", required=True)
    prepare_equity_day_aggs.add_argument("--ticker", required=True)
    prepare_equity_day_aggs.add_argument("--start", required=True)
    prepare_equity_day_aggs.add_argument("--end", required=True)
    prepare_option_iv = data_actions.add_parser("prepare-option-day-iv", help="materialize internal close-based IV for an option Day Aggregates dataset")
    prepare_option_iv.add_argument("--dataset-id", required=True)
    prepare_option_iv.add_argument("--option-dataset", required=True)
    prepare_option_iv.add_argument("--equity-dataset", required=True)
    prepare_option_iv.add_argument("--risk-free-rate", type=Decimal, default=Decimal("0.04"))
    prepare_option_iv.add_argument("--dividend-yield", type=Decimal, default=Decimal("0.0003"))
    prepare_massive = data_actions.add_parser("prepare-massive-options", help="archive, map and canonicalize explicit Massive option contracts")
    prepare_massive.add_argument("--dataset-id", required=True)
    prepare_massive.add_argument("--underlying", required=True, help="Massive underlying ticker, for example SPX")
    prepare_massive.add_argument("--underlying-reference-ticker", help="reference ticker when different, for example I:SPX")
    prepare_massive.add_argument("--option-tickers", required=True, help="comma-separated OCC option tickers including O: prefix")
    prepare_massive.add_argument("--start", required=True, help="inclusive ISO-8601 timestamp with timezone")
    prepare_massive.add_argument("--end", required=True, help="exclusive ISO-8601 timestamp with timezone")
    compact_massive = data_actions.add_parser("compact-market-events", help="explicitly compact immutable Parquet event partitions")
    compact_massive.add_argument("--dataset", required=True)
    massive_readiness = data_actions.add_parser("massive-readiness", help="probe private-server entitlement and historical endpoint access")
    massive_readiness.add_argument("--underlying", required=True)
    massive_readiness.add_argument("--option-ticker", required=True)
    massive_readiness.add_argument("--date", required=True)
    massive_slices = data_actions.add_parser("build-massive-slices", help="build point-in-time HistoricalDataset slices from Massive canonical events")
    massive_slices.add_argument("--source-dataset", required=True)
    massive_slices.add_argument("--output-dataset", required=True)
    massive_slices.add_argument("--start", required=True)
    massive_slices.add_argument("--end", required=True)
    massive_slices.add_argument("--sampling-seconds", type=int, default=60)
    massive_slices.add_argument("--max-quote-age-seconds", type=int, default=300)
    massive_slices.add_argument("--risk-free-rate", type=Decimal, default=Decimal("0"), help="continuously compounded annual rate used for put-call parity")
    massive_slices.add_argument("--split", choices=("development", "validation", "test"), default="development")
    sync_massive_reference = data_actions.add_parser("sync-massive-reference", help="sync Massive exchanges, conditions, holidays and optional corporate actions")
    sync_massive_reference.add_argument("--ticker")
    sync_massive_reference.add_argument("--start")
    sync_massive_reference.add_argument("--end")
    data_actions.add_parser("quarantine-insecure-massive-cache", help="move incomplete or non-HTTPS Massive source requests out of Source")
    features = commands.add_parser("features", help="build reusable feature datasets")
    feature_actions = features.add_subparsers(dest="action", required=True)
    build_features = feature_actions.add_parser("build")
    build_features.add_argument("--feature-set", choices=("btc-iv-rv-v1", "btc-term-skew-v1", "btc-deribit-trade-skew-v1"), required=True)
    pricing = commands.add_parser("pricing", help="price options and solve implied volatility without a venue connection")
    pricing_actions = pricing.add_subparsers(dest="action", required=True)
    pricing_option = pricing_actions.add_parser("option")
    pricing_option.add_argument("--model", choices=[item.value for item in PricingModel], default=PricingModel.BLACK_SCHOLES.value)
    pricing_option.add_argument("--right", choices=[item.value for item in OptionRight], required=True)
    pricing_option.add_argument("--underlying", type=Decimal, required=True, help="spot for Black-Scholes or forward for Black-76")
    pricing_option.add_argument("--strike", type=Decimal, required=True)
    pricing_option.add_argument("--years", type=Decimal, required=True)
    pricing_option.add_argument("--rate", type=Decimal, default=Decimal("0"))
    pricing_option.add_argument("--dividend-yield", type=Decimal, default=Decimal("0"))
    pricing_option.add_argument("--volatility", type=Decimal, help="absolute volatility, for example 0.20")
    pricing_option.add_argument("--market-price", type=Decimal, help="solve IV from this option price")
    vol = commands.add_parser("vol", help="calibrate and inspect internal volatility surfaces")
    vol_actions = vol.add_subparsers(dest="action", required=True)
    calibrate = vol_actions.add_parser("calibrate")
    calibrate.add_argument("--dataset", required=True)
    calibrate.add_argument("--rate", type=Decimal, default=Decimal("0"))
    calibrate.add_argument("--dividend-yield", type=Decimal, default=Decimal("0"))
    risk_analytics = commands.add_parser("risk", help="run option scenario revaluation and PnL explain")
    risk_actions = risk_analytics.add_subparsers(dest="action", required=True)
    risk_scenario = risk_actions.add_parser("scenario")
    risk_scenario.add_argument("--instrument", default="option:cli")
    risk_scenario.add_argument("--model", choices=[item.value for item in PricingModel], default=PricingModel.BLACK_SCHOLES.value)
    risk_scenario.add_argument("--right", choices=[item.value for item in OptionRight], required=True)
    risk_scenario.add_argument("--underlying", type=Decimal, required=True)
    risk_scenario.add_argument("--strike", type=Decimal, required=True)
    risk_scenario.add_argument("--years", type=Decimal, required=True)
    risk_scenario.add_argument("--rate", type=Decimal, default=Decimal("0"))
    risk_scenario.add_argument("--dividend-yield", type=Decimal, default=Decimal("0"))
    risk_scenario.add_argument("--volatility", type=Decimal, required=True)
    risk_scenario.add_argument("--quantity", type=Decimal, default=Decimal("1"))
    risk_scenario.add_argument("--multiplier", type=Decimal, default=Decimal("100"))
    risk_scenario.add_argument("--spot-shock", type=Decimal, default=Decimal("0"))
    risk_scenario.add_argument("--vol-shock", type=Decimal, default=Decimal("0"))
    risk_scenario.add_argument("--skew-twist", type=Decimal, default=Decimal("0"))
    risk_scenario.add_argument("--term-twist", type=Decimal, default=Decimal("0"))
    risk_scenario.add_argument("--rate-shock", type=Decimal, default=Decimal("0"))
    risk_scenario.add_argument("--time-advance-days", type=Decimal, default=Decimal("0"))
    catalog = commands.add_parser("catalog", help="sync versioned instrument definitions and venue listings")
    catalog_actions = catalog.add_subparsers(dest="action", required=True)
    sync = catalog_actions.add_parser("sync")
    sync.add_argument("--venue", choices=("ibkr", "binance"), required=True)
    sync.add_argument("--products", required=True, help="comma-separated: equity,option,spot,perpetual,future")
    sync.add_argument("--symbols", required=True, help="comma-separated symbols or IBKR option descriptors")
    sync.add_argument("--environment", choices=("paper", "testnet", "live"), required=True)
    sync.add_argument("--inverse", action="store_true", help="use Binance coin-margined futures contracts")
    research = commands.add_parser("research", help="capture normalized snapshots and replayable market series")
    actions = research.add_subparsers(dest="action", required=True)
    capture = actions.add_parser("capture", help="capture an IBKR research snapshot")
    capture.add_argument("--config", type=Path, help="optional JSON ResearchSpec overrides")
    capture.add_argument("--host", default="127.0.0.1")
    capture.add_argument("--port", type=int, default=4001)
    capture.add_argument("--client-id", type=int, default=21)
    capture.add_argument("--expiry-count", type=int)
    capture.add_argument("--strikes-each-side", type=int)
    capture.add_argument("--market-data-type", choices=[item.value for item in MarketDataType])
    analyze = actions.add_parser("analyze", help="rebuild a report without connecting to IBKR")
    analyze.add_argument("--run-id", required=True)
    show = actions.add_parser("show", help="show a saved run")
    show.add_argument("--run-id", required=True)
    series = actions.add_parser("capture-series", help="capture fixed-frequency MarketSlice data from IBKR")
    series.add_argument("--config", type=Path)
    series.add_argument("--dataset-id", required=True)
    series.add_argument("--samples", type=int, default=60)
    series.add_argument("--interval-seconds", type=int, default=60)
    series.add_argument("--split", choices=("development", "validation", "test"), default="development")
    series.add_argument("--host", default="127.0.0.1")
    series.add_argument("--port", type=int, default=4001)
    series.add_argument("--client-id", type=int, default=31)
    series.add_argument("--venue", choices=("ibkr", "binance"), default="ibkr")
    series.add_argument("--environment", choices=("paper", "testnet", "live"), default="paper")
    series.add_argument("--instruments", help="comma-separated internal InstrumentId values from Catalog")
    series.add_argument("--inverse", action="store_true", help="use Binance coin-margined market data routes")
    series.add_argument("--append", action="store_true", help="append this capture session to an existing dataset with provenance checks")
    series.add_argument("--checkpoint-samples", type=int, default=10, help="atomically persist after this many samples")
    research_readiness = actions.add_parser("readiness", help="evaluate real-data and statistical gates for the SPXW skew study")
    research_readiness.add_argument("--dataset", required=True)
    research_readiness.add_argument("--study-config", type=Path, default=Path("research/spxw_put_skew/config.json"))
    actions.add_parser("governance-audit", help="audit governed datasets, study versions, and strategy registry artifacts")
    actions.add_parser("migrate-btc-governance", help="materialize governed versions for existing BTC studies")
    actions.add_parser("register-btc-iron-condor", help="register and promote the governed BTC iron-condor StrategySpec")
    actions.add_parser("register-builtin-strategies", help="register draft StrategySpec and ExecutionPolicy contracts for reference strategies")
    history = commands.add_parser("history", help="download and inspect notebook-friendly OHLCV data")
    history_actions = history.add_subparsers(dest="action", required=True)
    history_download = history_actions.add_parser("download", help="download public Binance historical bars")
    history_download.add_argument("--dataset-id", required=True)
    history_download.add_argument("--instrument", required=True, help="internal InstrumentId stored with every bar")
    history_download.add_argument("--symbol", required=True, help="Binance venue symbol, for example BTCUSDT")
    history_download.add_argument("--interval", default="1h", help="Binance kline interval, for example 1m, 1h, 1d")
    history_download.add_argument("--start", required=True, help="ISO-8601 timestamp; timezone is required")
    history_download.add_argument("--end", required=True, help="exclusive ISO-8601 timestamp; timezone is required")
    history_download.add_argument("--market", choices=("spot", "usdm", "coinm"), default="spot")
    history_show = history_actions.add_parser("show", help="show a saved OHLCV dataset")
    history_show.add_argument("--dataset-id", required=True)
    history_backtest = history_actions.add_parser("backtest-sma", help="run a long-only SMA crossover backtest")
    history_backtest.add_argument("--dataset-id", required=True)
    history_backtest.add_argument("--fast", type=int, default=20)
    history_backtest.add_argument("--slow", type=int, default=50)
    history_backtest.add_argument("--initial-cash", type=Decimal, default=Decimal("100000"))
    history_backtest.add_argument("--fee-bps", type=Decimal, default=Decimal("10"))
    backtest = commands.add_parser("backtest", help="run deterministic conservative/stress strategy validation")
    backtest_actions = backtest.add_subparsers(dest="action", required=True)
    mock = backtest_actions.add_parser("mock", help="create a standardized synthetic dataset")
    mock.add_argument("--scenario", choices=[item.value for item in MockScenario], default=MockScenario.PROFIT_TARGET.value)
    mock.add_argument("--split", choices=("development", "validation", "test"), default="development")
    run = backtest_actions.add_parser("run", help="run conservative and stress backtests")
    run.add_argument("--strategy", choices=("bull-put-spread", "covered-call", "spot-perp-carry"), default="bull-put-spread")
    run.add_argument("--dataset")
    run.add_argument("--config", type=Path)
    bt_show = backtest_actions.add_parser("show")
    bt_show.add_argument("--run-id", required=True)
    replay = backtest_actions.add_parser("replay")
    replay.add_argument("--run-id", required=True)
    compare = backtest_actions.add_parser("compare")
    compare.add_argument("--run-id", action="append", required=True)
    validate = backtest_actions.add_parser("validate", help="run frozen parameters over development/validation/test datasets")
    validate.add_argument("--development", required=True)
    validate.add_argument("--validation", required=True)
    validate.add_argument("--test", required=True)
    validate.add_argument("--config", type=Path)
    account = commands.add_parser("account", help="reconcile Ledger balances and positions with a venue")
    account_actions = account.add_subparsers(dest="action", required=True)
    reconcile = account_actions.add_parser("reconcile")
    reconcile.add_argument("--venue", choices=("ibkr", "binance", "simulated"), required=True)
    reconcile.add_argument("--environment", choices=("paper", "testnet", "live"), required=True)
    reconcile.add_argument("--account-id", default="default")
    reconcile.add_argument("--product", choices=("securities", "spot", "futures", "options"), default="spot")
    reconcile.add_argument("--inverse", action="store_true")
    trade = commands.add_parser("trade", help="submit guarded paper, testnet, or explicitly confirmed live orders")
    trade_actions = trade.add_subparsers(dest="action", required=True)
    trade_run = trade_actions.add_parser("run")
    trade_run.add_argument("--strategy", choices=("covered-call", "spot-perp-carry"), required=True)
    trade_run.add_argument("--venue", choices=("ibkr", "binance", "simulated"), required=True)
    trade_run.add_argument("--environment", choices=("paper", "testnet", "live"), required=True)
    trade_run.add_argument("--confirm-live", action="store_true")
    trade_run.add_argument("--account-id", default="default")
    trade_run.add_argument("--product", choices=("securities", "spot", "futures", "options"), default="spot")
    trade_run.add_argument("--instrument", required=True)
    trade_run.add_argument("--side", choices=("buy", "sell"), required=True)
    trade_run.add_argument("--quantity", type=Decimal, required=True)
    trade_run.add_argument("--order-type", choices=("market", "limit"), default="limit")
    trade_run.add_argument("--limit-price", type=Decimal)
    trade_run.add_argument("--reduce-only", action="store_true")
    trade_run.add_argument("--post-only", action="store_true")
    trade_run.add_argument("--market-data-ready", action="store_true", help="explicit operational readiness acknowledgement for non-simulated venues")
    trade_run.add_argument("--kill-switch-drill", action="store_true")
    trade_run.add_argument("--inverse", action="store_true")
    return parser


def _spec(args: argparse.Namespace) -> ResearchSpec:
    values: dict[str, Any] = {}
    if args.config:
        values = json.loads(args.config.read_text(encoding="utf-8"))
    for name in ("expiry_count", "strikes_each_side", "market_data_type"):
        value = getattr(args, name, None)
        if value is not None:
            values[name] = value
    if "market_data_type" in values:
        values["market_data_type"] = MarketDataType(values["market_data_type"])
    if "rights" in values:
        values["rights"] = tuple(OptionRight(value) for value in values["rights"])
    return ResearchSpec(**values)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.group == "catalog":
        return _catalog(args)
    if args.group == "account":
        return _account(args)
    if args.group == "trade":
        return _trade(args)
    if args.group == "data":
        return _data(args)
    if args.group == "features":
        return _features(args)
    if args.group == "history":
        return _history(args)
    if args.group == "pricing":
        return _pricing(args)
    if args.group == "vol":
        return _vol(args)
    if args.group == "risk":
        return _risk_analytics(args)
    repository = FileResearchRepository(args.data_root)
    service = ResearchService(repository)
    if args.group == "backtest":
        return _backtest(args)
    if args.action == "governance-audit":
        from trading.research.validation import audit_governance
        result=audit_governance(args.lake_root)
        print(json.dumps({"passed":result.passed,"checked_datasets":result.checked_datasets,
            "checked_studies":result.checked_studies,"checked_strategies":result.checked_strategies,
            "violations":result.violations},ensure_ascii=False,indent=2))
        return 0 if result.passed else 2
    if args.action == "migrate-btc-governance":
        from research.btc_study_governance import migrate
        paths=migrate(args.lake_root);print(json.dumps({"count":len(paths),"paths":[str(path) for path in paths]},indent=2));return 0
    if args.action == "register-btc-iron-condor":
        from research.register_btc_iron_condor import register
        directory,spec=register(args.lake_root);print(f"{directory}: {spec.lifecycle.value} {spec.spec_hash}");return 0
    if args.action == "register-builtin-strategies":
        from trading.strategies.specs import register_builtin_strategies
        paths=register_builtin_strategies(Path(args.lake_root)/"strategies")
        print(json.dumps({"count":len(paths),"paths":[str(path) for path in paths]},indent=2));return 0
    if args.action == "readiness":
        return _research_readiness(args)
    if args.action == "capture-series":
        if args.instruments:
            return _capture_normalized_series(args)
        spec = _spec(args)
        provider = IbkrSpxwResearchAdapter(spec, host=args.host, port=args.port, client_id=args.client_id)
        series_spec = SeriesCaptureSpec(args.dataset_id, args.samples, args.interval_seconds, args.split, args.checkpoint_samples)
        def report_progress(progress: SeriesCaptureProgress) -> None:
            checkpoint = " saved" if progress.checkpoint_saved else ""
            print(
                f"Sample {progress.completed_samples}/{progress.total_samples} "
                f"requested={progress.requested_contracts} qualified={progress.qualified_contracts} "
                f"quotes={progress.quoted_contracts} checkpoint={checkpoint or 'pending'} "
                f"at={progress.timestamp.isoformat()}",
                flush=True,
            )

        dataset = SeriesCaptureService(
            DatasetRepository(args.dataset_root), on_progress=report_progress,
        ).capture(provider, spec, series_spec, append=args.append)
        print(f"Dataset: {dataset.manifest.dataset_id}")
        print(f"Slices: {dataset.manifest.slice_count}")
        print(f"Hash: {dataset.manifest.content_hash}")
        return 0
    if args.action == "capture":
        spec = _spec(args)
        provider = IbkrSpxwResearchAdapter(spec, host=args.host, port=args.port, client_id=args.client_id)
        snapshot, result = service.capture(provider, spec)
        print(summarize(result))
        print(f"Directory: {repository.run_dir(snapshot.run_id)}")
        return 0
    if args.action == "analyze":
        result = service.analyze_offline(args.run_id)
        print(summarize(result))
        print(f"Report: {repository.run_dir(args.run_id) / 'report.csv'}")
        return 0
    manifest = repository.load_manifest(args.run_id)
    print(f"Run: {args.run_id}")
    print(f"Status: {manifest['status']}")
    print(f"Events: {manifest['collected_event_count']}")
    print(f"Contracts: {manifest['selected_contract_count']}")
    print(f"Quality issues: {manifest['quality_issue_count']}")
    print(f"Offline analyzable: {manifest['offline_analyzable']}")
    print(f"Directory: {repository.run_dir(args.run_id)}")
    report = repository.run_dir(args.run_id) / "report.csv"
    print(f"Report: {report if report.exists() else 'not generated'}")
    if manifest.get("error_message"):
        print(f"Error ({manifest.get('error_stage')}): {manifest['error_message']}")
    return 0


def _data(args: argparse.Namespace) -> int:
    if args.action == "quarantine-insecure-massive-cache":
        moved = MassiveSourceArchive.quarantine_non_https(args.lake_root)
        print(json.dumps({"quarantined": len(moved), "paths": [str(item) for item in moved]}, ensure_ascii=False, indent=2)); return 0
    if args.action == "sync-massive-reference":
        pipeline = MassiveReferencePipeline(args.lake_root, MassiveClient(MassiveConfig.from_env()))
        result: dict[str, object] = {"code_tables": pipeline.sync_code_tables()}
        if args.ticker:
            if not args.start or not args.end:
                raise SystemExit("--start and --end are required with --ticker")
            result["corporate_actions"] = pipeline.sync_corporate_actions(args.ticker, datetime.fromisoformat(args.start), datetime.fromisoformat(args.end))
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    if args.action == "build-massive-slices":
        dataset = MassiveCuratedSliceBuilder(args.lake_root, catalog_path=args.catalog_path, dataset_root=args.dataset_root).build(
            args.source_dataset, args.output_dataset, datetime.fromisoformat(args.start), datetime.fromisoformat(args.end),
            sampling_seconds=args.sampling_seconds, max_quote_age_seconds=args.max_quote_age_seconds,
            split=args.split, risk_free_rate=args.risk_free_rate)
        print(f"{dataset.manifest.dataset_id}: slices={dataset.manifest.slice_count} hash={dataset.manifest.content_hash}")
        return 0
    if args.action == "massive-readiness":
        report = MassiveReadinessChecker(MassiveClient(MassiveConfig.from_env())).check(
            underlying=args.underlying, option_ticker=args.option_ticker, date=args.date)
        print(json.dumps({
            "ready": report.ready,
            "api_host": report.api_host,
            "official_underlying_history": report.official_underlying_history,
            "valuation_reference_mode": report.valuation_reference_mode,
            "checks": report.checks,
        }, ensure_ascii=False, indent=2))
        return 0 if report.ready else 2
    if args.action == "compact-market-events":
        result = ParquetMarketEventRepository(Path(args.lake_root) / "canonical" / "market").compact(args.dataset)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    if args.action == "prepare-massive-options":
        pipeline = MassiveOptionDataPipeline(args.lake_root, MassiveClient(MassiveConfig.from_env()), catalog_path=args.catalog_path)
        manifest = pipeline.prepare_options(
            dataset_id=args.dataset_id, underlying=args.underlying,
            option_tickers=tuple(value.strip() for value in args.option_tickers.split(",") if value.strip()),
            start=datetime.fromisoformat(args.start), end=datetime.fromisoformat(args.end), underlying_reference_ticker=args.underlying_reference_ticker,
        )
        print(f"{manifest['dataset_id']}: rows={manifest['rows']} hash={manifest['dataset_sha256']}")
        return 0
    if args.action == "massive-fetch":
        client = MassiveClient(MassiveConfig.from_env())
        archive = MassiveSourceArchive(args.lake_root, client)
        resource, params = _massive_request(args)
        result = archive.fetch_pages(resource, params, max_pages=args.max_pages)
        print(json.dumps({"fingerprint": result.fingerprint, "directory": str(result.directory), "receipt": result.receipt}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "massive-flat-file":
        client = MassiveClient(MassiveConfig.from_env())
        flat = MassiveFlatFileClient(args.lake_root, client)
        if args.operation == "usage":
            print(json.dumps(flat.usage(), ensure_ascii=False, indent=2)); return 0
        if not args.key:
            raise SystemExit("--key is required for Massive Flat File status/download")
        if args.operation == "status":
            print(json.dumps(flat.cache_status(args.key), ensure_ascii=False, indent=2)); return 0
        print(flat.download(args.key)); return 0
    if args.action == "massive-flat-file-batch":
        flat = MassiveFlatFileClient(args.lake_root, MassiveClient(MassiveConfig.from_env()))
        report = MassiveFlatFileBatchDownloader(flat).download_range(
            date.fromisoformat(args.start), date.fromisoformat(args.end), max_files=args.max_files, dry_run=args.dry_run,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2)); return 0
    if args.action == "prepare-spxw-day-aggs":
        manifest = SpxwDayAggPipeline(args.lake_root).prepare(
            args.dataset_id, date.fromisoformat(args.start), date.fromisoformat(args.end),
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    if args.action == "prepare-option-day-aggs":
        manifest = OptionDayAggPipeline(args.lake_root, args.option_root).prepare(
            args.dataset_id, date.fromisoformat(args.start), date.fromisoformat(args.end),
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    if args.action == "prepare-massive-equity-day-aggs":
        manifest = MassiveEquityDayAggPipeline(
            args.lake_root, MassiveClient(MassiveConfig.from_env()),
        ).prepare(args.dataset_id, args.ticker, date.fromisoformat(args.start), date.fromisoformat(args.end))
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    if args.action == "prepare-option-day-iv":
        manifest = OptionDayIvPipeline(args.lake_root).prepare(
            args.dataset_id, args.option_dataset, args.equity_dataset,
            risk_free_rate=args.risk_free_rate, dividend_yield=args.dividend_yield,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    if args.action == "prepare-btc-options":
        manifests = BtcOptionsDataPipeline(args.lake_root).prepare(date.fromisoformat(args.start), date.fromisoformat(args.end))
        for manifest in manifests:
            print(f"{manifest['dataset_id']}: rows={manifest['rows']} hash={manifest['dataset_sha256']}")
        return 0
    if args.action == "prepare-btc-option-quotes":
        manifest = BtcOptionsDataPipeline(args.lake_root).prepare_option_quotes(date.fromisoformat(args.start), date.fromisoformat(args.end))
        print(f"{manifest['dataset_id']}: rows={manifest['rows']} hash={manifest['dataset_sha256']}")
        return 0
    if args.action == "prepare-deribit-option-trades":
        manifest = BtcOptionsDataPipeline(args.lake_root).prepare_deribit_option_trades(date.fromisoformat(args.start), date.fromisoformat(args.end))
        print(f"{manifest['dataset_id']}: rows={manifest['rows']} hash={manifest['dataset_sha256']}")
        return 0
    if args.action == "capture-deribit-option-chain":
        manifest=BtcOptionsDataPipeline(args.lake_root).capture_deribit_option_chain()
        print(f"{manifest['dataset_id']}: snapshots={manifest['snapshot_count']} rows={manifest['rows']} hash={manifest['dataset_sha256']}")
        return 0
    if args.action == "btc-options-readiness":
        result = btc_options_readiness(args.lake_root); print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["signal_research_ready"] else 2
    if args.action == "materialize-capabilities":
        paths = materialize_catalog_capabilities(args.lake_root)
        print(json.dumps({"written": [str(path) for path in paths], "count": len(paths)}, ensure_ascii=False, indent=2))
        return 0
    try:
        metadata = CanonicalDatasetRepository(args.lake_root).metadata(args.dataset)
    except KeyError:
        metadata = ParquetMarketEventRepository(Path(args.lake_root) / "canonical" / "market").metadata(args.dataset)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


def _massive_request(args: argparse.Namespace) -> tuple[str, dict[str, object]]:
    if args.resource == "option-contracts":
        if not args.underlying:
            raise SystemExit("--underlying is required for option-contracts")
        return "/v3/reference/options/contracts", {"underlying_ticker": args.underlying, "as_of": args.start, "limit": args.limit, "sort": "ticker", "order": "asc"}
    if args.resource in {"option-quotes", "option-trades"}:
        if not args.ticker or not args.start or not args.end:
            raise SystemExit("--ticker, --start and --end are required for historical option quotes/trades")
        kind = "quotes" if args.resource == "option-quotes" else "trades"
        return f"/v3/{kind}/{args.ticker}", {"timestamp.gte": args.start, "timestamp.lt": args.end, "limit": args.limit, "sort": "timestamp", "order": "asc"}
    if args.resource == "aggregates":
        if not args.ticker or not args.start or not args.end:
            raise SystemExit("--ticker, --start and --end are required for aggregates")
        return f"/v2/aggs/ticker/{args.ticker}/range/{args.multiplier}/{args.timespan}/{args.start}/{args.end}", {"adjusted": True, "sort": "asc", "limit": args.limit}
    if not args.underlying:
        raise SystemExit("--underlying is required for option-chain")
    return f"/v3/snapshot/options/{args.underlying}", {"limit": args.limit}


def _features(args: argparse.Namespace) -> int:
    builders = {"btc-iv-rv-v1": BtcIvRvFeatureBuilder, "btc-term-skew-v1": BtcTermSkewFeatureBuilder,
                "btc-deribit-trade-skew-v1": BtcDeribitTradeSkewFeatureBuilder}
    manifest = builders[args.feature_set](args.lake_root).build()
    print(f"{manifest['dataset_id']}: rows={manifest['rows']} hash={manifest['dataset_sha256']}")
    return 0


def _pricing(args: argparse.Namespace) -> int:
    model = PricingModel(args.model)
    if model is PricingModel.BLACK_76 and args.dividend_yield != 0:
        raise SystemExit("Black-76 requires --dividend-yield 0")
    if args.volatility is None and args.market_price is None:
        raise SystemExit("provide --volatility or --market-price")
    initial_vol = args.volatility if args.volatility is not None else Decimal("0.20")
    inputs = PricingInput(
        args.underlying, args.strike, args.years, args.rate, initial_vol,
        OptionRight(args.right), args.dividend_yield,
    )
    if args.market_price is not None:
        solved = implied_volatility(args.market_price, inputs, model)
        print(f"Solver: {solved.status.value}")
        if solved.volatility is None:
            print(f"Bounds: {solved.lower_price_bound} to {solved.upper_price_bound}")
            return 2
        inputs = PricingInput(
            inputs.underlying, inputs.strike, inputs.time_to_expiry, inputs.risk_free_rate,
            solved.volatility, inputs.right, inputs.dividend_yield,
        )
        print(f"Implied volatility: {solved.volatility}")
    result = price_with_volatility(inputs, inputs.volatility, model)
    print(f"Model: {result.model.value}")
    print(f"Price: {result.price}")
    print(f"Delta: {result.delta}")
    print(f"Gamma: {result.gamma}")
    print(f"Theta/year: {result.theta}")
    print(f"Vega: {result.vega}")
    print(f"Rho: {result.rho}")
    return 0


def _research_readiness(args: argparse.Namespace) -> int:
    try:
        from research.spxw_put_skew.study import ResearchConfig, execute_research
    except ImportError as error:
        raise SystemExit("research readiness requires: pip install -e '.[notebook]'") from error
    from trading.research.data_store import ResearchDatasetStore
    raw = json.loads(args.study_config.read_text(encoding="utf-8"))
    raw.pop("dataset_id", None)
    decimal_fields = {
        "target_short_delta", "target_long_delta", "high_skew_percentile", "minimum_quote_coverage",
        "maximum_stale_rate", "minimum_surface_calibration_rate", "profit_target",
        "stop_loss_multiple", "commission_per_contract",
    }
    config = ResearchConfig(**{key: Decimal(str(value)) if key in decimal_fields else value for key, value in raw.items()})
    repository = DatasetRepository(args.dataset_root)
    dataset = repository.load(args.dataset)
    collection = ResearchDatasetStore(repository).load_collection(args.dataset)
    panel, readiness, conclusion = execute_research(dataset, config, collection)
    print(f"Dataset: {dataset.manifest.dataset_id}")
    print(f"Ready: {readiness.ready}")
    print(f"Conclusion status: {conclusion.status}")
    print(f"Eligible panel rows: {len(panel)}")
    for key, value in readiness.metrics.items():
        print(f"{key}: {value}")
    for reason in readiness.reasons:
        print(f"FAIL: {reason}")
    return 0 if readiness.ready else 2


def _vol(args: argparse.Namespace) -> int:
    dataset = DatasetRepository(args.dataset_root).load(args.dataset)
    catalog = InstrumentCatalog()
    for definition in dataset.definitions:
        catalog.add(definition)
    service = ValuationService(catalog, risk_free_rate=args.rate, dividend_yield=args.dividend_yield)
    repository = SurfaceRepository(args.surface_root)
    surfaces, failures = [], []
    for market in dataset.slices:
        _, valuation = service.value(market)
        failures.extend(valuation.failures)
        if valuation.surface is not None:
            surfaces.append(valuation.surface)
            repository.save(valuation.surface)
    calibrated = sum(any(smile.parameters is not None for smile in item.smiles) for item in surfaces)
    arbitrage_passed = sum(item.diagnostics.passed for item in surfaces)
    print(f"Dataset: {dataset.manifest.dataset_id}")
    print(f"Surfaces: {len(surfaces)}")
    print(f"Calibrated: {calibrated}")
    print(f"Arbitrage checks passed: {arbitrage_passed}")
    print(f"Valuation failures: {len(failures)}")
    if surfaces:
        print(f"Last surface: {surfaces[-1].surface_id}")
        print(f"Last input hash: {surfaces[-1].input_hash}")
        print(f"Surface directory: {repository.root}")
    return 0 if surfaces else 2


def _risk_analytics(args: argparse.Namespace) -> int:
    model = PricingModel(args.model)
    if model is PricingModel.BLACK_76 and args.dividend_yield != 0:
        raise SystemExit("Black-76 requires --dividend-yield 0")
    position = RevaluationPosition(
        InstrumentId(args.instrument), args.quantity, args.multiplier,
        PricingInput(
            args.underlying, args.strike, args.years, args.rate, args.volatility,
            OptionRight(args.right), args.dividend_yield,
        ),
        model,
    )
    scenario = Scenario(
        "cli", args.spot_shock, args.vol_shock, args.skew_twist, args.term_twist,
        args.rate_shock, args.time_advance_days,
    )
    result = ScenarioEngine().evaluate((position,), scenario)
    explain = explain_scenario(position, scenario, result)
    print(f"Base value: {result.base_value}")
    print(f"Scenario value: {result.scenario_value}")
    print(f"PnL: {result.pnl}")
    print(f"Delta PnL: {explain.delta}")
    print(f"Gamma PnL: {explain.gamma}")
    print(f"Theta PnL: {explain.theta}")
    print(f"Vega PnL: {explain.vega}")
    print(f"Rho PnL: {explain.rho}")
    print(f"Residual: {explain.residual}")
    return 0


def _history(args: argparse.Namespace) -> int:
    repository = BarRepository(args.history_root)
    if args.action == "download":
        start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
        dataset = repository.download(
            BinanceHistoricalBarProvider(args.market), dataset_id=args.dataset_id,
            instrument_id=InstrumentId(args.instrument), symbol=args.symbol, interval=args.interval,
            start=start, end=end, source=f"binance.{args.market}",
        )
        print(f"Dataset: {dataset.metadata.dataset_id}")
        print(f"Bars: {dataset.metadata.bar_count}")
        print(f"Range: {dataset.bars[0].start.isoformat()} to {dataset.bars[-1].end.isoformat()}")
        print(f"Directory: {repository.root / dataset.metadata.dataset_id}")
        return 0
    if args.action == "backtest-sma":
        result = backtest_sma_cross(
            repository.load(args.dataset_id),
            SmaCrossConfig(args.fast, args.slow, args.initial_cash, args.fee_bps),
        )
        metrics = result.metrics
        print(f"Strategy: SMA({args.fast}, {args.slow}) long-only")
        print(f"Dataset: {result.dataset_id}")
        print(f"Return: {metrics['total_return']:.2%}")
        print(f"Buy and hold: {metrics['buy_and_hold_return']:.2%}")
        print(f"Annualized: {metrics['annualized_return']:.2%}")
        print(f"Max drawdown: {metrics['max_drawdown']:.2%}")
        print(f"Sharpe: {metrics['sharpe']:.3f}")
        print(f"Trades: {metrics['trade_count']}")
        print(f"Commissions: {metrics['commissions']:.2f}")
        print(f"Final equity: {metrics['final_equity']:.2f}")
        return 0
    dataset = repository.load(args.dataset_id)
    print(f"Dataset: {dataset.metadata.dataset_id}")
    print(f"Instrument: {dataset.metadata.instrument_id}")
    print(f"Symbol: {dataset.metadata.symbol}")
    print(f"Interval: {dataset.metadata.interval}")
    print(f"Bars: {dataset.metadata.bar_count}")
    print(f"Requested range: {dataset.metadata.start.isoformat()} to {dataset.metadata.end.isoformat()}")
    print(f"Directory: {repository.root / dataset.metadata.dataset_id}")
    return 0


def _backtest(args: argparse.Namespace) -> int:
    datasets = DatasetRepository(args.dataset_root)
    backtests = BacktestRepository(args.backtest_root)
    if args.action == "mock":
        dataset = make_mock_dataset(MockScenario(args.scenario), split=args.split)
        directory = datasets.save(dataset)
        print(f"Dataset: {dataset.manifest.dataset_id}")
        print(f"Hash: {dataset.manifest.content_hash}")
        print(f"Directory: {directory}")
        print("Synthetic data validates mechanics only; it is not evidence of strategy effectiveness.")
        return 0
    if args.action == "run":
        if args.strategy in {"covered-call", "spot-perp-carry"}:
            results = tuple(run_reference_scenario(args.strategy, model) for model in ("conservative", "stress"))
            directory = Path(args.backtest_root) / "reference" / args.strategy
            directory.mkdir(parents=True, exist_ok=True)
            for result in results:
                target = directory / f"{result.model}-{result.audit_hash}.json"
                target.write_text(json.dumps({
                    "strategy": result.strategy, "model": result.model, "final_cash": str(result.final_cash),
                    "ledger_transactions": result.ledger_transactions, "audit_hash": result.audit_hash,
                    "strategy_spec_hash": result.strategy_spec_hash, "execution_policy_id": result.execution_policy_id,
                }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                print(f"{result.model}: cash={result.final_cash} hash={result.audit_hash}")
            return 0
        if not args.dataset:
            raise SystemExit("--dataset is required for bull-put-spread")
        dataset = datasets.load(args.dataset)
        values = json.loads(args.config.read_text()) if args.config else {}
        strategy_config = BullPutSpreadConfig(**_coerce_decimal_fields(values.get("strategy", {}), BullPutSpreadConfig))
        risk_limits = RiskLimits(**_coerce_decimal_fields(values.get("risk", {}), RiskLimits))
        backtest_values = _coerce_decimal_fields(values.get("backtest", {}), BacktestConfig)
        backtest_values.pop("start", None)
        backtest_values.pop("end", None)
        config = BacktestConfig(dataset.manifest.start, dataset.manifest.end, **backtest_values)
        conservative, stress = BacktestService(backtests).run_suite(dataset, config, strategy_config, risk_limits)
        for result in (conservative, stress):
            print(f"{result.config.fill_model}: run={result.run_id} status={result.status.value} return={result.metrics['total_return']}")
        return 0
    if args.action == "validate":
        selected = tuple(datasets.load(value) for value in (args.development, args.validation, args.test))
        values = json.loads(args.config.read_text()) if args.config else {}
        strategy_config = BullPutSpreadConfig(**_coerce_decimal_fields(values.get("strategy", {}), BullPutSpreadConfig))
        risk_limits = RiskLimits(**_coerce_decimal_fields(values.get("risk", {}), RiskLimits))
        bt_values = _coerce_decimal_fields(values.get("backtest", {}), BacktestConfig)
        bt_values.pop("start", None)
        bt_values.pop("end", None)
        config = BacktestConfig(selected[0].manifest.start, selected[0].manifest.end, **bt_values)
        directory = BacktestService(backtests).validate_splits(selected, config, strategy_config, risk_limits)
        print(f"Validation: {directory}")
        print("Parameters were frozen across development, validation, and test splits.")
        return 0
    if args.action == "show":
        manifest = backtests.load_manifest(args.run_id)
        metrics = restore_primitives(backtests.load_metrics(args.run_id))
        print(f"Run: {args.run_id}")
        print(f"Status: {manifest['status']}")
        print(f"Model: {manifest['fill_model']}")
        print(f"Split: {manifest['sample_split']}")
        print(f"Synthetic: {manifest['synthetic_dataset']}")
        print(f"Return: {metrics.get('total_return')}")
        print(f"Max drawdown: {metrics.get('max_drawdown')}")
        print(f"Directory: {backtests.run_dir(args.run_id)}")
        return 0
    if args.action == "compare":
        if len(args.run_id) < 2:
            raise SystemExit("compare requires at least two --run-id values")
        for run_id in args.run_id:
            manifest = backtests.load_manifest(run_id)
            metrics = restore_primitives(backtests.load_metrics(run_id))
            print(f"{run_id} model={manifest['fill_model']} split={manifest['sample_split']} return={metrics.get('total_return')} drawdown={metrics.get('max_drawdown')} fees={metrics.get('commissions')} slippage={metrics.get('slippage')}")
        return 0
    manifest = backtests.load_manifest(args.run_id)
    config, raw_strategy, raw_risk = backtests.load_config(args.run_id)
    strategy_config = from_primitive(raw_strategy, BullPutSpreadConfig)
    risk_limits = from_primitive(raw_risk, RiskLimits)
    dataset = datasets.load(manifest["dataset_id"])
    replayed = BacktestEngine(dataset, config, BullPutSpreadStrategy(strategy_config), risk_limits).run()
    replayed.metrics["dataset_hash"] = dataset.manifest.content_hash
    replayed.metrics["code_version"] = dataset.manifest.code_version
    from trading.strategies.specs import bull_put_strategy_spec
    replay_spec,replay_policy=bull_put_strategy_spec(strategy_config)
    replayed.metrics["strategy_spec_hash"] = replay_spec.spec_hash
    replayed.metrics["execution_policy_id"] = replay_policy.policy_id
    replayed.metrics["execution_policy_version"] = replay_policy.version
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        candidate = BacktestRepository(directory)
        candidate.save(replayed, strategy_config=strategy_config, risk_limits=risk_limits)
        actual = candidate.audit_hash(candidate.run_dir(replayed.run_id))
    expected = manifest["audit_hash"]
    print(f"Replay: {'MATCH' if actual == expected else 'MISMATCH'}")
    print(f"Expected: {expected}")
    print(f"Actual:   {actual}")
    return 0 if actual == expected else 2


def _capture_normalized_series(args: argparse.Namespace) -> int:
    environment = Environment(args.environment)
    repository = CatalogRepository(args.catalog_path)
    if not repository.path.exists():
        raise SystemExit("catalog is missing; run 'trader catalog sync' first")
    catalog = repository.load()
    now = datetime.now(timezone.utc)
    definitions = tuple(catalog.get(InstrumentId(value.strip()), now) for value in args.instruments.split(",") if value.strip())
    session = None
    if args.venue == "ibkr":
        if environment not in {Environment.PAPER, Environment.LIVE}:
            raise SystemExit("IBKR normalized capture requires paper or live environment")
        session = _ibkr_session(readonly=True)
        reference = IbkrReferenceAdapter(session)
        for definition in definitions:
            reference.bind_definition(definition, catalog)
        provider = IbkrMarketDataAdapter(session)
    else:
        if environment not in {Environment.TESTNET, Environment.LIVE}:
            raise SystemExit("Binance normalized capture requires testnet or live environment")
        spot_base = "https://testnet.binance.vision" if environment is Environment.TESTNET else "https://api.binance.com"
        futures_base = "https://testnet.binancefuture.com" if environment is Environment.TESTNET else "https://dapi.binance.com" if args.inverse else "https://fapi.binance.com"
        futures_path = "/dapi/v1/ticker/bookTicker" if args.inverse else "/fapi/v1/ticker/bookTicker"
        routes = {
            ProductType.CRYPTO_SPOT: BinanceMarketDataAdapter(UrllibBinanceTransport(spot_base)),
            ProductType.PERPETUAL: BinanceMarketDataAdapter(UrllibBinanceTransport(futures_base), ProductType.PERPETUAL, path=futures_path),
            ProductType.FUTURE: BinanceMarketDataAdapter(UrllibBinanceTransport(futures_base), ProductType.FUTURE, path=futures_path),
        }
        if environment is Environment.LIVE:
            routes[ProductType.CRYPTO_OPTION] = BinanceMarketDataAdapter(UrllibBinanceTransport("https://eapi.binance.com"), ProductType.CRYPTO_OPTION)
        provider = CompositeMarketDataAdapter(routes)
    series_spec = SeriesCaptureSpec(args.dataset_id, args.samples, args.interval_seconds, args.split)
    try:
        dataset = NormalizedSeriesCaptureService(DatasetRepository(args.dataset_root)).capture(
            provider, definitions, series_spec, source=f"{args.venue}.normalized-series", market_data_type=environment.value,
        )
    finally:
        if session is not None:
            session.disconnect()
    print(f"Dataset: {dataset.manifest.dataset_id}")
    print(f"Products: {','.join(sorted({item.product_type.value for item in definitions}))}")
    print(f"Slices: {dataset.manifest.slice_count}")
    print(f"Hash: {dataset.manifest.content_hash}")
    return 0


def _catalog(args: argparse.Namespace) -> int:
    environment = Environment(args.environment)
    products = {item.strip() for item in args.products.split(",") if item.strip()}
    symbols = tuple(item.strip() for item in args.symbols.split(",") if item.strip())
    repository = CatalogRepository(args.catalog_path)
    catalog = repository.load() if repository.path.exists() else InstrumentCatalog()
    definitions = []
    if args.venue == "ibkr":
        if environment not in {Environment.PAPER, Environment.LIVE}:
            raise SystemExit("IBKR catalog sync requires paper or live environment")
        session = _ibkr_session(readonly=True)
        adapter = IbkrReferenceAdapter(session)
        try:
            if "equity" in products:
                definitions.extend(adapter.sync(ReferenceDataRequest(ProductType.EQUITY, tuple(item for item in symbols if ":" not in item))))
            if "option" in products:
                definitions.extend(adapter.sync(ReferenceDataRequest(ProductType.LISTED_OPTION, tuple(item for item in symbols if ":" in item))))
        finally:
            session.disconnect()
    else:
        if environment not in {Environment.TESTNET, Environment.LIVE}:
            raise SystemExit("Binance catalog sync requires testnet or live environment")
        if "spot" in products:
            transport = UrllibBinanceTransport("https://testnet.binance.vision" if environment is Environment.TESTNET else "https://api.binance.com")
            definitions.extend(BinanceSpotReferenceAdapter(transport).sync(ReferenceDataRequest(ProductType.CRYPTO_SPOT, symbols)))
        if "perpetual" in products:
            transport = UrllibBinanceTransport("https://testnet.binancefuture.com" if environment is Environment.TESTNET else "https://dapi.binance.com" if args.inverse else "https://fapi.binance.com")
            definitions.extend(BinanceFuturesReferenceAdapter(transport, inverse=args.inverse).sync(ReferenceDataRequest(ProductType.PERPETUAL, symbols)))
        if "future" in products:
            transport = UrllibBinanceTransport("https://testnet.binancefuture.com" if environment is Environment.TESTNET else "https://dapi.binance.com" if args.inverse else "https://fapi.binance.com")
            definitions.extend(BinanceFuturesReferenceAdapter(transport, inverse=args.inverse).sync(ReferenceDataRequest(ProductType.FUTURE, symbols)))
        if "option" in products:
            if environment is Environment.TESTNET:
                raise SystemExit("Binance options do not provide the same public testnet contract; use live public reference data only")
            definitions.extend(BinanceOptionsReferenceAdapter(UrllibBinanceTransport("https://eapi.binance.com")).sync(ReferenceDataRequest(ProductType.CRYPTO_OPTION, symbols)))
    for definition in definitions:
        try:
            catalog.add(definition)
        except ValueError:
            catalog.supersede(definition, definition.effective_from)
    repository.save(catalog)
    print(f"Catalog: {repository.path}")
    print(f"Synced: {len(definitions)} instruments from {args.venue} ({environment.value})")
    return 0


def _account(args: argparse.Namespace) -> int:
    environment = Environment(args.environment)
    if args.venue == "binance" and args.product == "options" and environment is not Environment.LIVE:
        raise SystemExit("Binance options account is live-only; no equivalent options testnet is available")
    ledger = LedgerRepository(args.ledger_path).load()
    catalog_repository = CatalogRepository(args.catalog_path)
    catalog = catalog_repository.load() if catalog_repository.path.exists() else InstrumentCatalog()
    account = _account_key(args.venue, args.account_id, args.product)
    adapter = _account_adapter(args.venue, environment, account, ledger, args.product, catalog, args.inverse)
    report = ReconciliationService(ledger, adapter).reconcile(account)
    print(f"Environment: {environment.value.upper()}")
    print(f"Account: {account.value}")
    print(f"Matched: {report.matched}")
    for difference in report.differences:
        print(f"{difference.kind} {difference.key}: local={difference.local} venue={difference.venue}")
    return 0 if report.matched else 2


def _trade(args: argparse.Namespace) -> int:
    environment = Environment(args.environment)
    if environment is Environment.LIVE and not args.confirm_live:
        raise SystemExit("live trading requires --confirm-live")
    if args.venue == "ibkr" and environment is Environment.TESTNET:
        raise SystemExit("IBKR uses paper rather than testnet")
    if args.venue == "binance" and environment is Environment.PAPER:
        raise SystemExit("Binance uses testnet rather than paper")
    if args.venue == "binance" and args.product == "options" and environment is not Environment.LIVE:
        raise SystemExit("Binance options execution is live-only; no equivalent options testnet is available")
    from trading.strategies.deployment import StrategyDeploymentGate
    strategy_id={"covered-call":"covered-call-v1","spot-perp-carry":"spot-perpetual-carry-v1"}.get(args.strategy,args.strategy)
    deployment=StrategyDeploymentGate(Path(args.lake_root)/"strategies").evaluate(strategy_id,environment,simulated_venue=args.venue=="simulated")
    if not deployment.allowed:raise SystemExit(f"strategy deployment rejected: {deployment.reason}")
    print(f"Strategy lifecycle: {deployment.lifecycle.value} ({deployment.strategy_directory})")
    catalog_repository = CatalogRepository(args.catalog_path)
    if not catalog_repository.path.exists():
        raise SystemExit("catalog is missing; run 'trader catalog sync' first")
    catalog = catalog_repository.load()
    definition = catalog.get(InstrumentId(args.instrument), datetime.now(timezone.utc))
    ledger = LedgerRepository(args.ledger_path).load()
    venue = definition.listings[0].venue_id if args.venue == "simulated" else VenueId(args.venue)
    account = AccountKey(venue, args.account_id, _account_type(args.product))
    if args.venue == "simulated":
        balances, positions = _local_state(ledger, account)
        adapter = SimulatedExecutionAccountAdapter(venue, account, balances, positions, environment)
        market_ready = True
    else:
        adapter = _execution_account_adapter(args.venue, environment, args.product, definition, catalog, args.inverse)
        market_ready = args.market_data_ready
    reconciliation = ReconciliationService(ledger, adapter)
    event_log = PersistentEventLog(args.event_log_path)
    kill_switch = KillSwitch((adapter,))
    coordinator = TradingCoordinator(ExecutionRouter(catalog, (adapter,)), {account: reconciliation}, kill_switch, event_log)
    print(f"Environment: {environment.value.upper()}")
    coordinator.start((account,), catalog_ready=True, market_data_ready=market_ready, execution_ready=True)
    order_type = OrderType(args.order_type)
    if order_type is OrderType.LIMIT and args.limit_price is None:
        raise SystemExit("limit orders require --limit-price")
    correlation = str(uuid5(NAMESPACE_URL, f"cli:{strategy_id}:{args.instrument}:{datetime.now(timezone.utc).date()}"))
    request = OrderRequest(
        f"internal-{correlation}", f"client-{correlation}", strategy_id, f"intent-{correlation}", correlation,
        account, definition.instrument_id, TradeSide(args.side), args.quantity,
        ExecutionInstructions(order_type, TimeInForce.DAY, args.limit_price, post_only=args.post_only, reduce_only=args.reduce_only),
    )
    ack = coordinator.submit(request, datetime.now(timezone.utc))
    print(f"Accepted: client={ack.client_order_id} venue_order={ack.venue_order_id} intent={ack.intent_id}")
    if args.kill_switch_drill:
        result = kill_switch.trigger((account,), "CLI drill")
        print(f"Kill switch: cancelled={len(result.cancelled_orders)} failures={len(result.failures)} reduce_only={kill_switch.reduce_only}")
    return 0


def _ibkr_session(*, readonly: bool) -> IbkrSession:
    return IbkrSession(
        os.getenv("IBKR_HOST", "127.0.0.1"), int(os.getenv("IBKR_PORT", "4001")),
        int(os.getenv("IBKR_CLIENT_ID", "51")), readonly,
    )


def _credentials(environment: Environment) -> tuple[str, str]:
    prefix = "BINANCE_TESTNET" if environment is Environment.TESTNET else "BINANCE_LIVE"
    key, secret = os.getenv(f"{prefix}_API_KEY"), os.getenv(f"{prefix}_API_SECRET")
    if not key or not secret:
        raise SystemExit(f"missing {prefix}_API_KEY/{prefix}_API_SECRET environment variables")
    return key, secret


def _account_adapter(venue: str, environment: Environment, account: AccountKey, ledger, product: str, catalog, inverse: bool):
    if venue == "simulated":
        balances, positions = _local_state(ledger, account)
        return SimulatedExecutionAccountAdapter(account.venue_id, account, balances, positions, environment)
    if venue == "ibkr":
        session = _ibkr_session(readonly=True)
        reference = IbkrReferenceAdapter(session)
        for definition in catalog.definitions(datetime.now(timezone.utc)):
            if definition.product_type.value in {"equity", "etf", "listed_option"}:
                reference.bind_definition(definition, catalog)
        return IbkrAccountAdapter(session, environment)
    key, secret = _credentials(environment)
    if product == "options":
        lookup = {
            listing.symbol: definition.instrument_id
            for definition in catalog.definitions(datetime.now(timezone.utc))
            for listing in definition.listings if listing.venue_id == VenueId("binance")
        }
        return BinanceOptionsAccountAdapter(
            UrllibBinanceTransport("https://eapi.binance.com"), BinanceSigner(key, secret),
            environment, instrument_lookup=lookup,
        )
    base = "https://testnet.binancefuture.com" if product == "futures" and environment is Environment.TESTNET else "https://dapi.binance.com" if product == "futures" and inverse else "https://fapi.binance.com" if product == "futures" else "https://testnet.binance.vision" if environment is Environment.TESTNET else "https://api.binance.com"
    lookup = {
        listing.symbol: definition.instrument_id
        for definition in catalog.definitions(datetime.now(timezone.utc))
        for listing in definition.listings if listing.venue_id == VenueId("binance")
    }
    return BinanceAccountAdapter(UrllibBinanceTransport(base), BinanceSigner(key, secret), environment, futures=product == "futures", inverse=inverse, instrument_lookup=lookup)


def _execution_account_adapter(venue: str, environment: Environment, product: str, definition, catalog, inverse: bool):
    if venue == "ibkr":
        session = _ibkr_session(readonly=False)
        IbkrReferenceAdapter(session).bind_definition(definition, catalog)
        return _CombinedExecutionAccount(IbkrExecutionAdapter(session, environment), IbkrAccountAdapter(session, environment))
    key, secret = _credentials(environment)
    if product == "options":
        transport, signer = UrllibBinanceTransport("https://eapi.binance.com"), BinanceSigner(key, secret)
        lookup = {
            listing.symbol: item.instrument_id
            for item in catalog.definitions(datetime.now(timezone.utc))
            for listing in item.listings if listing.venue_id == VenueId("binance")
        }
        return _CombinedExecutionAccount(
            BinanceOptionsExecutionAdapter(transport, signer, environment, instrument_symbols={definition.instrument_id: definition.listing(VenueId("binance")).symbol}),
            BinanceOptionsAccountAdapter(transport, signer, environment, instrument_lookup=lookup),
        )
    base = "https://testnet.binancefuture.com" if product == "futures" and environment is Environment.TESTNET else "https://dapi.binance.com" if product == "futures" and inverse else "https://fapi.binance.com" if product == "futures" else "https://testnet.binance.vision" if environment is Environment.TESTNET else "https://api.binance.com"
    transport, signer = UrllibBinanceTransport(base), BinanceSigner(key, secret)
    execution = BinanceExecutionAdapter(
        transport, signer, environment, futures=product == "futures", inverse=inverse,
        instrument_symbols={definition.instrument_id: definition.listing(VenueId("binance")).symbol},
    )
    lookup = {
        listing.symbol: item.instrument_id
        for item in catalog.definitions(datetime.now(timezone.utc))
        for listing in item.listings if listing.venue_id == VenueId("binance")
    }
    account = BinanceAccountAdapter(transport, signer, environment, futures=product == "futures", inverse=inverse, instrument_lookup=lookup)
    return _CombinedExecutionAccount(execution, account)


class _CombinedExecutionAccount:
    def __init__(self, execution, account) -> None:
        self.execution, self.account = execution, account
        self.venue_id, self.environment, self.capabilities = execution.venue_id, execution.environment, execution.capabilities
    def place_order(self, request): return self.execution.place_order(request)
    def cancel_order(self, account, venue_order_id): return self.execution.cancel_order(account, venue_order_id)
    def open_orders(self, account): return self.execution.open_orders(account)
    def account_state(self, account): return self.account.account_state(account)


def _account_key(venue: str, account_id: str, product: str) -> AccountKey:
    return AccountKey(VenueId(venue), account_id, _account_type(product))


def _account_type(product: str) -> AccountType:
    return {
        "securities": AccountType.SECURITIES_MARGIN,
        "spot": AccountType.CRYPTO_SPOT,
        "futures": AccountType.DERIVATIVES,
        "options": AccountType.DERIVATIVES,
    }[product]


def _local_state(ledger, account):
    balances, positions = {}, {}
    owned = {LedgerBook.CASH, LedgerBook.AVAILABLE, LedgerBook.LOCKED, LedgerBook.MARGIN, LedgerBook.COLLATERAL, LedgerBook.BORROWED}
    for entry in ledger.entries:
        if entry.account != account:
            continue
        if entry.book in owned:
            balances[entry.asset] = balances.get(entry.asset, Decimal("0")) + entry.amount
        elif entry.book is LedgerBook.POSITION and entry.instrument_id is not None:
            positions[entry.instrument_id] = positions.get(entry.instrument_id, Decimal("0")) + entry.amount
    return tuple(balances.items()), tuple(positions.items())


def _coerce_decimal_fields(values: dict[str, Any], cls) -> dict[str, Any]:
    from decimal import Decimal
    from datetime import time
    from typing import get_type_hints

    hints = get_type_hints(cls)
    result = {}
    for key, value in values.items():
        if hints.get(key) is Decimal:
            result[key] = Decimal(str(value))
        elif hints.get(key) is time and isinstance(value, str):
            result[key] = time.fromisoformat(value)
        else:
            result[key] = value
    return result


if __name__ == "__main__":
    raise SystemExit(main())
