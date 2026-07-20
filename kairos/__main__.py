from __future__ import annotations

from kairos.domain.identity import InstitutionId

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import importlib
import json
import os
import sys
import warnings
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from kairos import __version__

from kairos.accounting.ledger import LedgerService
from kairos.ports import Environment, OrderRequest, ReferenceDataRequest
from kairos.connectors.market_data_router import CompositeMarketDataClient
from kairos.connectors.binance.account_gateway import (
    BinanceAccountGateway,
    BinanceOptionsAccountGateway,
)
from kairos.connectors.binance.execution_gateway import (
    BinanceExecutionGateway,
    BinanceOptionsExecutionGateway,
)
from kairos.connectors.binance.market_data_client import BinanceMarketDataClient
from kairos.connectors.binance.reference_data import (
    BinanceFuturesReferenceDataClient,
    BinanceOptionsReferenceDataClient,
    BinanceSpotReferenceDataClient,
)
from kairos.connectors.binance.request_signing import BinanceSigner
from kairos.connectors.binance.rest_transport import UrllibBinanceTransport
from kairos.connectors.ibkr.account_gateway import IbkrAccountGateway
from kairos.connectors.ibkr.execution_gateway import IbkrExecutionGateway
from kairos.connectors.ibkr.market_data_client import IbkrMarketDataClient
from kairos.connectors.ibkr.reference_data import IbkrReferenceDataClient
from kairos.connectors.ibkr.research import IbkrSpxwResearchProvider
from kairos.connectors.ibkr.session import IbkrSession
from kairos.connectors.simulated import SimulatedExecutionAccountGateway
from kairos.connectors.massive import MassiveClient, MassiveConfig, MassiveMarketSnapshotBuilder, MassiveEntitlementDiagnostics, MassiveEquityDailyOhlcvPipeline, MassiveEquityIdentityResolver, MassiveFlatFileBatchDownloader, MassiveFlatFileClient, MassiveReferencePipeline, MassiveVendorArchiveClient, OptionCloseImpliedVolatilityPipeline, OptionDailyOhlcvPipeline, SpxwDailyOhlcvPipeline
from kairos.backtest.reference_scenarios import run_reference_scenario
from kairos.reference import ReferenceCatalog, ReferenceCatalogRepository
from kairos.reference.access import settlement_asset
from kairos.domain.capability import OrderType
from kairos.domain.execution import TradeSide
from kairos.domain.identity import AccountKey, AccountType, AssetId, InstrumentId, VenueId
from kairos.domain.ledger import LedgerBook
from kairos.domain.order import ExecutionInstructions, TimeInForce
from kairos.domain.product import OptionRight, ProductType
from kairos.execution.router import ExecutionRouter
from kairos.orchestration.coordinator import ExecutionCoordinator
from kairos.orchestration.event_log import PersistentEventLog
from kairos.orchestration.kill_switch import KillSwitch
from kairos.orchestration.reconciliation import ReconciliationService
from kairos.research.report import summarize
from kairos.research.option_capture import OptionResearchCaptureService
from kairos.research.spec import MarketDataType, OptionChainCaptureSpec
from kairos.storage.repository import FileResearchRepository
from kairos.backtest.engine import BacktestEngine
from kairos.data.market_snapshot_storage import MarketSnapshotStorageDriver
from kairos.backtest.synthetic_scenarios import SyntheticScenario, build_synthetic_backtest_dataset
from kairos.backtest.repository import BacktestRepository
from kairos.backtest.result import BacktestConfig
from kairos.backtest.experiment_runner import BacktestExperimentRunner
from kairos.risk.limits import RiskLimits
from kairos.storage.codec import from_primitive, restore_primitives, to_primitive
from kairos.storage.data_lake import write_json
from kairos.strategies.bull_put_spread import BullPutSpreadConfig, BullPutSpreadStrategy
from kairos.research.series import SeriesCaptureProgress, SeriesCaptureService, SeriesCaptureSpec
from kairos.research.normalized_series import NormalizedSeriesCaptureService
from kairos.strategies.sma_cross_research_backtest import BarSeries, SmaCrossConfig, backtest_sma_cross
from kairos.pricing import PricingInput, PricingModel, OptionValuationService, implied_volatility, price_with_volatility
from kairos.risk import RevaluationPosition, Scenario, ScenarioEngine, explain_scenario
from kairos.data import (
    DataCatalog, DatasetKey, DatasetLayer, DataProductDefinition, DatasetQualityService, DatasetRelease,
    DatasetStatus, DatasetStorageKind, OutputFormat, QualityLevel, ResearchDataClient, RunMode,
    register_market_replay_dataset,
)
from kairos.data.bootstrap import default_provider_registry, register_configured_products, register_default_products
from kairos.market_data import ParquetMarketEventRepository
from kairos.features import BtcIvRvFeatureBuilder, BtcTermSkewFeatureBuilder, BtcDeribitTradeSkewFeatureBuilder
from kairos.features.us_equity_momentum import UsEquityMomentumDatasetBuilder, UsEquityMomentumPolicy


def _program_name() -> str:
    executable = Path(sys.argv[0]).name
    if executable in {"kairos"}:
        return executable
    return "kairos"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=_program_name(), description="Multi-asset research, backtest, reconciliation, and execution toolkit")
    parser.add_argument("--data-root", default="data/snapshots")
    parser.add_argument("--dataset-root", default="data/curated")
    parser.add_argument("--backtest-root", default="data/backtests")
    parser.add_argument("--catalog-path", default="data/catalog/instruments.json")
    parser.add_argument("--reference-catalog-path", default="data/reference/catalog.json")
    parser.add_argument("--event-log-path", default="data/events/kairos.jsonl")
    parser.add_argument("--runtime-db", help="transactional runtime database; defaults beside --event-log-path")
    parser.add_argument(
        "--lake-root",
        default=os.environ.get("KAIROS_LAKE_ROOT", "data"),
        help="data lake root; defaults to KAIROS_LAKE_ROOT or data",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text",
                        help="output format; human-readable text is the default")
    parser.add_argument("--lang", choices=("zh-CN", "en-US"), help="display language; defaults from the system locale")
    parser.add_argument("--quiet", action="store_true", help="suppress successful product command output")
    commands = parser.add_subparsers(dest="group", required=True)
    init = commands.add_parser("init", help="create a Kairos project in any local directory")
    init.add_argument("--target", type=Path, default=Path("."), help="project directory; defaults to the current directory")
    init.add_argument("--name", help="project name; defaults to the target directory name")
    init.add_argument("--force", action="store_true", help="overwrite existing scaffold files")
    data = commands.add_parser("data", help="prepare and inspect governed market datasets")
    data_actions = data.add_subparsers(dest="action", required=True)
    data_download = data_actions.add_parser("download", help="download a registered Data Product by key")
    data_download.add_argument("key", help="registered Data Product key, for example tutorial-sma-data")
    data_register_download = data_actions.add_parser(
        "register-download", help="register a reusable Data Product download entry",
    )
    data_register_download.add_argument("--key", required=True, help="stable Data Product key")
    data_register_download.add_argument("--spec", type=Path, required=True, help="JSON Data Product download spec")
    data_write = data_actions.add_parser("write", help="write external data into the Data Contract")
    data_write.add_argument("--file", type=Path, help="CSV file to import as a historical time series")
    data_write.add_argument("--live", action="store_true", help="register a live data view instead of importing a file")
    data_write.add_argument("--connector", type=Path, help="live connector code file used with --live")
    data_write.add_argument("--adapter", type=Path, help=argparse.SUPPRESS)
    data_write.add_argument("--as", dest="as_dataset", required=True, help="logical dataset identity to publish")
    data_write.add_argument("--contract", type=Path, required=True, help="JSON Data Contract")
    live_binance = data_actions.add_parser(
        "live-binance", help="capture public Binance WebSocket events into the canonical runtime contract",
    )
    live_binance.add_argument("--symbol", required=True, help="Binance venue symbol, for example BTCUSDT")
    live_binance.add_argument("--channel", choices=("bookTicker", "trade", "aggTrade", "depth"),
                              default="bookTicker")
    live_binance.add_argument("--messages", type=int, default=10)
    live_binance.add_argument("--futures", action="store_true")
    live_binance.add_argument("--instrument", help="stable internal InstrumentId; defaults from symbol and product line")
    live_binance.add_argument("--journal", type=Path, help="raw JSONL capture path")
    soak_binance = data_actions.add_parser(
        "soak-binance", help="run an audited public Binance market-data stability soak",
    )
    soak_binance.add_argument("--symbol", required=True)
    soak_binance.add_argument("--channel", choices=("bookTicker", "trade", "aggTrade", "depth"),
                              default="bookTicker")
    soak_binance.add_argument("--duration-seconds", type=float, default=60.0)
    soak_binance.add_argument("--minimum-events", type=int, default=100)
    soak_binance.add_argument("--maximum-silence-seconds", type=float, default=5.0)
    soak_binance.add_argument("--maximum-channel-utilization", type=float, default=0.9)
    soak_binance.add_argument("--capture-segment-events", type=int, default=100000)
    soak_binance.add_argument("--capture-segment-bytes", type=int, default=256 * 1024 * 1024)
    soak_binance.add_argument("--capture-total-bytes", type=int, default=20 * 1024 * 1024 * 1024)
    soak_binance.add_argument(
        "--restart-interval-seconds", type=float, default=0,
        help="actively restart the WebSocket session at this interval and write a campaign artifact",
    )
    soak_binance.add_argument("--instrument")
    soak_binance.add_argument("--journal", type=Path)
    soak_binance.add_argument("--artifact", type=Path)
    inspect_data = data_actions.add_parser("inspect", help="show schema, lineage and time coverage")
    inspect_data.add_argument("--dataset", required=True)
    search_data = data_actions.add_parser("search", help="discover products by structured dimensions")
    search_data.add_argument("--dimension", action="append", default=[], help="key=value dimension; repeatable")
    describe_data = data_actions.add_parser("describe", help="show product semantics, sources and releases")
    describe_data.add_argument("--dataset", required=True)
    doctor_data = data_actions.add_parser("doctor", help="diagnose one product and suggest the next action")
    doctor_data.add_argument("--dataset", required=True)
    diagnostics_data = data_actions.add_parser("diagnostics", help="audit all Catalog products and releases")
    diagnostics_data.add_argument("--strict", action="store_true", help="return non-zero when errors exist")
    us_equity_diagnostics = data_actions.add_parser("us-equity-momentum-diagnostics", help="audit the local US equity momentum data package")
    us_equity_diagnostics.add_argument("--study-id", default="us-equity-momentum")
    us_equity_diagnostics.add_argument("--version", default="1.0.0")
    us_equity_diagnostics.add_argument("--strict", action="store_true", help="return non-zero when diagnostics errors exist")
    validate_data = data_actions.add_parser("validate", help="run the typed Quality Profile for a release")
    validate_data.add_argument("--release", required=True)
    prepare_data = data_actions.add_parser("prepare", help="plan, acquire, validate and optionally promote a product")
    prepare_data.add_argument("--dataset", required=True)
    prepare_data.add_argument("--start", required=True)
    prepare_data.add_argument("--end", required=True)
    prepare_data.add_argument("--quality", choices=tuple(item.value for item in QualityLevel), default=QualityLevel.RESEARCH.value)
    prepare_data.add_argument("--provider")
    prepare_data.add_argument("--venue")
    prepare_data.add_argument("--connector-config", type=Path)
    prepare_data.add_argument("--acquire-missing", action="store_true")
    prepare_data.add_argument("--promote", action="store_true", help="explicitly approve promotion after quality passes")
    prepare_data.add_argument("--actor", default="data-prepare")
    prepare_data.add_argument("--reason", default="explicit data preparation")
    prepare_us_equity_momentum = data_actions.add_parser(
        "prepare-us-equity-momentum",
        help="one-command bounded US equity momentum data, feature, study and diagnostics workflow",
    )
    prepare_us_equity_momentum.add_argument(
        "--raw-dataset", action="append", required=True,
        help="configured Massive raw equity OHLCV product; repeat for a bounded multi-stock basket",
    )
    prepare_us_equity_momentum.add_argument("--start", required=True, help="inclusive ISO-8601 timestamp with timezone")
    prepare_us_equity_momentum.add_argument("--end", required=True, help="exclusive ISO-8601 timestamp with timezone")
    prepare_us_equity_momentum.add_argument("--connector-config", type=Path)
    prepare_us_equity_momentum.add_argument("--provider", default="massive")
    prepare_us_equity_momentum.add_argument("--venue", default="us-securities")
    prepare_us_equity_momentum.add_argument("--dataset-id", default="us-equity-momentum.bounded.v1")
    prepare_us_equity_momentum.add_argument("--study-id", default="us-equity-momentum")
    prepare_us_equity_momentum.add_argument("--version", default="1.0.0")
    prepare_us_equity_momentum.add_argument(
        "--hypothesis",
        default="US equities with stronger point-in-time cross-sectional momentum may outperform weaker eligible equities over subsequent holding windows",
    )
    prepare_us_equity_momentum.add_argument("--corporate-actions-directory")
    prepare_us_equity_momentum.add_argument(
        "--sync-corporate-actions", action="store_true",
        help="archive Massive split/dividend events for the prepared bounded tickers and feed them into the feature build",
    )
    prepare_us_equity_momentum.add_argument("--reference-directory")
    prepare_us_equity_momentum.add_argument("--minimum-price", type=Decimal, default=Decimal("5"))
    prepare_us_equity_momentum.add_argument("--minimum-adv20", type=Decimal, default=Decimal("10000000"))
    prepare_us_equity_momentum.add_argument("--minimum-history", type=int, default=252)
    query_data = data_actions.add_parser("query", help="query a governed product or frozen release")
    query_data.add_argument("--dataset", required=True)
    query_data.add_argument("--start")
    query_data.add_argument("--end")
    query_data.add_argument("--field", action="append", default=[])
    query_data.add_argument("--limit", type=int, default=100)
    freeze_data = data_actions.add_parser("freeze", help="freeze one or more dataset inputs for a study")
    freeze_data.add_argument("--study-id", required=True)
    freeze_data.add_argument("--dataset", action="append", required=True)
    freeze_data.add_argument("--output", type=Path, required=True)
    freeze_data.add_argument("--code-version", default=__version__)
    compare_data = data_actions.add_parser("compare", help="compare two immutable dataset releases")
    compare_data.add_argument("--first", required=True)
    compare_data.add_argument("--second", required=True)
    audit_artifact = data_actions.add_parser("audit-artifact", help="verify an artifact consumes frozen Q3/Q4 releases")
    audit_artifact.add_argument("--artifact", type=Path, required=True)
    alias_data = data_actions.add_parser("alias", help="promote an audited floating alias to an approved release")
    alias_data.add_argument("--alias", required=True)
    alias_data.add_argument("--release", required=True)
    alias_data.add_argument("--actor", required=True)
    alias_data.add_argument("--reason", required=True)
    alias_data.add_argument("--quality-report-hash", required=True)
    data_actions.add_parser("btc-options-readiness", help=argparse.SUPPRESS)
    catalog_data = data_actions.add_parser("catalog", help="list governed logical datasets, versions, aliases and formats")
    catalog_data.add_argument("--refresh", action="store_true", help="discover and persist existing governed datasets")
    for action, help_text in (("plan", "show local coverage and missing-data acquisition plan"),
                              ("acquire", "acquire missing data and publish an immutable release")):
        command = data_actions.add_parser(action, help=help_text)
        command.add_argument("--dataset", required=True)
        command.add_argument("--start", required=True, help="inclusive ISO-8601 timestamp with timezone")
        command.add_argument("--end", required=True, help="exclusive ISO-8601 timestamp with timezone")
        command.add_argument("--provider")
        command.add_argument("--venue")
        command.add_argument("--connector-config", type=Path,
                             help="explicit JSON configuration for additional provider connectors")
        if action == "acquire":
            command.add_argument("--refresh", action="store_true")
    promote_data = data_actions.add_parser("promote", help="audit and promote a frozen dataset release")
    promote_data.add_argument("--release", required=True)
    promote_data.add_argument("--status", required=True, choices=(
        DatasetStatus.APPROVED_FOR_RESEARCH.value, DatasetStatus.APPROVED_FOR_BACKTEST.value,
        DatasetStatus.APPROVED_FOR_PRODUCTION.value,
    ))
    promote_data.add_argument("--actor", required=True)
    promote_data.add_argument("--reason", required=True)
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
    prepare_spxw_daily_ohlcv = data_actions.add_parser("prepare-spxw-daily-ohlcv", help="inventory and convert downloaded OPRA daily OHLCV into governed SPXW Parquet")
    prepare_spxw_daily_ohlcv.add_argument("--dataset-id", required=True)
    prepare_spxw_daily_ohlcv.add_argument("--start", required=True, help="inclusive date YYYY-MM-DD")
    prepare_spxw_daily_ohlcv.add_argument("--end", required=True, help="exclusive date YYYY-MM-DD")
    prepare_option_daily_ohlcv = data_actions.add_parser("prepare-option-daily-ohlcv", help="convert downloaded OPRA daily OHLCV for one OCC root")
    prepare_option_daily_ohlcv.add_argument("--dataset-id", required=True)
    prepare_option_daily_ohlcv.add_argument("--option-root", required=True, help="OCC root without O: prefix, for example NVDA")
    prepare_option_daily_ohlcv.add_argument("--start", required=True)
    prepare_option_daily_ohlcv.add_argument("--end", required=True)
    prepare_equity_daily_ohlcv = data_actions.add_parser("prepare-equity-daily-ohlcv", help="archive and convert provider equity daily OHLCV")
    prepare_equity_daily_ohlcv.add_argument("--provider", choices=("massive",), default="massive")
    prepare_equity_daily_ohlcv.add_argument("--dataset-id", required=True)
    prepare_equity_daily_ohlcv.add_argument("--ticker", required=True)
    prepare_equity_daily_ohlcv.add_argument("--start", required=True)
    prepare_equity_daily_ohlcv.add_argument("--end", required=True)
    prepare_equity_daily_ohlcv.add_argument("--view", choices=("raw", "vendor_adjusted"), default="vendor_adjusted")
    prepare_option_close_iv = data_actions.add_parser("prepare-option-close-implied-volatility", help="materialize close-based implied volatility for an option daily OHLCV dataset")
    prepare_option_close_iv.add_argument("--dataset-id", required=True)
    prepare_option_close_iv.add_argument("--option-dataset", required=True)
    prepare_option_close_iv.add_argument("--equity-dataset", required=True)
    prepare_option_close_iv.add_argument("--risk-free-rate", type=Decimal, default=Decimal("0.04"))
    prepare_option_close_iv.add_argument("--dividend-yield", type=Decimal, default=Decimal("0.0003"))
    compact_massive = data_actions.add_parser("compact-market-events", help="explicitly compact immutable Parquet event partitions")
    compact_massive.add_argument("--dataset", required=True)
    massive_entitlement = data_actions.add_parser("massive-entitlement-diagnostics", help="probe private-server entitlement and historical endpoint access")
    massive_entitlement.add_argument("--underlying", required=True)
    massive_entitlement.add_argument("--option-ticker", required=True)
    massive_entitlement.add_argument("--date", required=True)
    massive_slices = data_actions.add_parser("build-massive-slices", help="build point-in-time MarketReplayDataset slices from Massive canonical events")
    massive_slices.add_argument("--source-dataset", required=True)
    massive_slices.add_argument("--output-dataset", required=True)
    massive_slices.add_argument("--start", required=True)
    massive_slices.add_argument("--end", required=True)
    massive_slices.add_argument("--sampling-seconds", type=int, default=60)
    massive_slices.add_argument("--max-quote-age-seconds", type=int, default=300)
    massive_slices.add_argument("--risk-free-rate", type=Decimal, default=Decimal("0"), help="continuously compounded annual rate used for put-call parity")
    massive_slices.add_argument("--split", choices=("development", "validation", "test"), default="development")
    sync_massive_reference = data_actions.add_parser("sync-massive-reference", help="sync Massive exchanges, conditions, holidays, equity tickers and optional corporate actions")
    sync_massive_reference.add_argument("--equity-tickers", action="store_true", help="sync active and inactive US common stock ticker reference")
    sync_massive_reference.add_argument("--active-only", action="store_true", help="only sync currently active equity tickers")
    sync_massive_reference.add_argument("--ticker")
    sync_massive_reference.add_argument("--start")
    sync_massive_reference.add_argument("--end")
    build_equity_identity = data_actions.add_parser("build-massive-equity-identity", help="build point-in-time Massive equity symbol mappings from reference rows")
    build_equity_identity.add_argument("--reference-rows", type=Path, required=True)
    build_equity_identity.add_argument("--ticker-events", type=Path)
    data_actions.add_parser("quarantine-insecure-massive-cache", help="move incomplete or non-HTTPS Massive source requests out of Source")
    features = commands.add_parser("features", help="build reusable feature datasets")
    feature_actions = features.add_subparsers(dest="action", required=True)
    build_features = feature_actions.add_parser("build")
    build_features.add_argument(
        "--feature-set",
        choices=("btc-iv-rv-v1", "btc-term-skew-v1", "btc-deribit-trade-skew-v1", "us-equity-momentum-v1"),
        required=True,
    )
    build_features.add_argument("--source-directory", help="lake-relative or absolute OHLCV parquet directory for US equity momentum")
    build_features.add_argument("--dataset-id", help="output dataset id for US equity derived datasets")
    build_features.add_argument("--corporate-actions-directory", help="lake-relative or absolute Massive corporate action events directory")
    build_features.add_argument("--reference-directory", help="lake-relative or absolute Massive equity identity/reference directory")
    build_features.add_argument("--minimum-price", type=Decimal, default=Decimal("5"))
    build_features.add_argument("--minimum-adv20", type=Decimal, default=Decimal("10000000"))
    build_features.add_argument("--minimum-history", type=int, default=252)
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
    capture.add_argument("--config", type=Path, help="optional JSON OptionChainCaptureSpec overrides")
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
    series = actions.add_parser("capture-series", help="capture fixed-frequency MarketSnapshot data from IBKR")
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
    research_readiness = actions.add_parser("readiness", help=argparse.SUPPRESS)
    research_readiness.add_argument("--dataset", required=True)
    research_readiness.add_argument("--study-config", type=Path, default=Path("studies/spxw_put_skew/config.json"))
    actions.add_parser("governance-audit", help="audit governed datasets, study versions, and strategy registry artifacts")
    actions.add_parser("register-btc-iron-condor", help=argparse.SUPPRESS)
    actions.add_parser("register-builtin-strategies", help="register draft StrategySpec and ExecutionPolicy contracts for reference strategies")
    backtest = commands.add_parser("backtest", help="run deterministic conservative/stress strategy validation")
    backtest_actions = backtest.add_subparsers(dest="action", required=True)
    synthetic = backtest_actions.add_parser("synthetic-scenario", help="create a standardized synthetic backtest dataset")
    synthetic.add_argument("--scenario", choices=[item.value for item in SyntheticScenario], default=SyntheticScenario.PROFIT_TARGET.value)
    synthetic.add_argument("--split", choices=("development", "validation", "test"), default="development")
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
    sma = backtest_actions.add_parser("sma", help="run SMA crossover on a frozen Q3/Q4 OHLCV release")
    sma.add_argument("--dataset", required=True, help="logical product, alias, or immutable release ID")
    sma.add_argument("--start")
    sma.add_argument("--end")
    sma.add_argument("--fast", type=int, default=20)
    sma.add_argument("--slow", type=int, default=50)
    sma.add_argument("--initial-cash", type=Decimal, default=Decimal("100000"))
    sma.add_argument("--fee-bps", type=Decimal, default=Decimal("10"))
    reference_spxw = backtest_actions.add_parser("spxw-reference-scenario", help="run the governed Massive SPXW reference pipeline")
    reference_spxw.add_argument("--event-release", required=True)
    reference_spxw.add_argument("--source-slices", required=True)
    reference_spxw.add_argument("--curated-slices", required=True)
    golden_spxw = backtest_actions.add_parser("golden-spxw", help=argparse.SUPPRESS)
    golden_spxw.add_argument("--event-release", required=True)
    golden_spxw.add_argument("--source-slices", required=True)
    golden_spxw.add_argument("--curated-slices", required=True)
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
    trade_run.add_argument("--soak-seconds", type=int, default=0, help="run the supervised runtime for this many wall-clock seconds")
    trade_run.add_argument("--cycle-seconds", type=float, default=5.0, help="supervisor heartbeat/reconciliation interval")
    trade_run.add_argument("--restart-drill", action="store_true", help="restart and recover the Application after the soak")
    trade_run.add_argument("--soak-artifact", type=Path, help="explicit L4 soak manifest path")
    trade_run.add_argument("--inverse", action="store_true")
    trade_run.set_defaults(manual_order=False)
    order = commands.add_parser("order", help="submit an explicitly audited manual operations order")
    order_actions=order.add_subparsers(dest="action",required=True);order_submit=order_actions.add_parser("submit")
    order_submit.add_argument("--venue",choices=("ibkr","binance","simulated"),required=True)
    order_submit.add_argument("--environment",choices=("paper","testnet","live"),required=True)
    order_submit.add_argument("--confirm-live",action="store_true");order_submit.add_argument("--account-id",default="default")
    order_submit.add_argument("--product",choices=("securities","spot","futures","options"),default="spot")
    order_submit.add_argument("--instrument",required=True);order_submit.add_argument("--side",choices=("buy","sell"),required=True)
    order_submit.add_argument("--quantity",type=Decimal,required=True);order_submit.add_argument("--order-type",choices=("market","limit"),default="limit")
    order_submit.add_argument("--limit-price",type=Decimal);order_submit.add_argument("--reduce-only",action="store_true")
    order_submit.add_argument("--post-only",action="store_true");order_submit.add_argument("--market-data-ready",action="store_true")
    order_submit.add_argument("--actor",required=True);order_submit.add_argument("--reason",required=True)
    order_submit.add_argument("--inverse",action="store_true");order_submit.set_defaults(strategy="manual-operations",manual_order=True,
        kill_switch_drill=False,soak_seconds=0,cycle_seconds=5.0,restart_drill=False,soak_artifact=None)
    runtime = commands.add_parser("runtime", help="operate and verify the durable execution runtime")
    runtime_actions = runtime.add_subparsers(dest="action", required=True)
    runtime_reference = runtime_actions.add_parser(
        "reference-artifact", help="run the deterministic L2 order/fill/restart/reconciliation reference artifact",
    )
    runtime_reference.add_argument("--root", type=Path, required=True, help="isolated output root for runtime state and audit artifacts")
    runtime_reference_alias = runtime_actions.add_parser(
        "golden", help=argparse.SUPPRESS,
    )
    runtime_reference_alias.add_argument("--root", type=Path, required=True, help="isolated output root for runtime state and audit artifacts")
    runtime_failure_policy = runtime_actions.add_parser(
        "failure-policy", help="run deterministic L3 crash-window and restart acceptance drills",
    )
    runtime_failure_policy.add_argument("--root", type=Path, required=True, help="isolated output root for drill state and audit artifacts")
    runtime_failure_policy_alias = runtime_actions.add_parser(
        "failure-matrix", help=argparse.SUPPRESS,
    )
    runtime_failure_policy_alias.add_argument("--root", type=Path, required=True, help="isolated output root for drill state and audit artifacts")
    runtime_orders = runtime_actions.add_parser("orders", help="inspect or explicitly resolve durable unresolved orders")
    runtime_orders.add_argument("--db", type=Path, required=True, help="SQLite Runtime Store path")
    runtime_orders.add_argument("--client-order-id")
    runtime_orders.add_argument("--target", choices=("rejected", "cancelled", "expired"))
    runtime_orders.add_argument("--actor")
    runtime_orders.add_argument("--reason")
    runtime_orders.add_argument("--evidence")
    runtime_calibration = runtime_actions.add_parser(
        "calibrate-execution", help="build an ExecutionCalibrationRelease from durable runtime fills",
    )
    runtime_calibration.add_argument("--db", type=Path, required=True)
    runtime_calibration.add_argument("--output-root", type=Path, required=True)
    runtime_calibration.add_argument("--venue", required=True)
    runtime_calibration.add_argument("--environment", choices=("paper", "testnet", "live"), required=True)
    runtime_calibration.add_argument("--strategy")
    runtime_calibration.add_argument("--calibration-id", default="execution-calibration-v1")
    l4_preflight = runtime_actions.add_parser("l4-preflight", help="check external Paper/Testnet soak prerequisites without exposing credentials")
    l4_preflight.add_argument("--venue", choices=("binance", "ibkr"), required=True)
    l4_preflight.add_argument("--environment", choices=("testnet", "paper"), required=True)
    l4_preflight.add_argument("--strategy", required=True)
    l4_preflight.add_argument("--instrument", required=True)
    l4_preflight.add_argument("--evidence-artifact", type=Path,
                              help="write a promotion-ready Paper/Testnet readiness evidence artifact")

    study = commands.add_parser("study", help="manage flexible research workspaces and frozen candidates")
    study_actions = study.add_subparsers(dest="action", required=True)
    study_open = study_actions.add_parser("open", help="open or create a Study workspace")
    study_open.add_argument("study_id")
    study_open.add_argument("--version", default="1.0.0")
    study_open.add_argument("--hypothesis", default="")
    study_add_data = study_actions.add_parser("add-data", help="bind a Data Product release into a Study workspace")
    study_add_data.add_argument("--workspace", dest="workspace", help="Study workspace id")
    study_add_data.add_argument("--ws", dest="workspace", help=argparse.SUPPRESS)
    study_add_data.add_argument("--name", required=True, help="workspace-local data name")
    study_add_data.add_argument("--dataset", required=True, help="logical dataset, alias, or release id")
    study_add_factor = study_actions.add_parser("add-factor", help="bind user factor code into a Study workspace")
    study_add_factor.add_argument("--workspace", dest="workspace", help="Study workspace id")
    study_add_factor.add_argument("--ws", dest="workspace", help=argparse.SUPPRESS)
    study_add_factor.add_argument("--name", required=True, help="workspace-local factor name")
    study_add_factor.add_argument("--file", required=True, help="factor code file")
    study_create = study_actions.add_parser("create")
    study_create.add_argument("study_id"); study_create.add_argument("--version", default="1.0.0")
    study_create.add_argument("--hypothesis", required=True)
    study_create.add_argument("--dataset", help="Dataset Release or alias; infers release hash, time semantics and coverage")
    study_create.add_argument("--input-release", help="advanced/CI override")
    study_create.add_argument("--input-hash", help="advanced/CI override")
    study_create.add_argument("--primary-time", help="advanced/CI override")
    study_create.add_argument("--start", help="optional range override"); study_create.add_argument("--end", help="optional range override")
    study_start = study_actions.add_parser(
        "start", help="acquire governed data, create a bound Study, and scaffold research in one command",
    )
    study_start.add_argument("study_id")
    study_start.add_argument("--version", default="1.0.0")
    study_start.add_argument(
        "--dataset", default="market.ohlcv.crypto.binance.usdm-perpetual.1h",
    )
    study_start.add_argument("--start", required=True, help="inclusive ISO-8601 timestamp with timezone")
    study_start.add_argument("--end", required=True, help="exclusive ISO-8601 timestamp with timezone")
    study_start.add_argument("--symbol", action="append", default=[],
                             help="optional Binance symbol for a bounded run; omit for full-market discovery")
    study_start.add_argument(
        "--hypothesis",
        default=("At each hour, idiosyncratic moves are concentrated in a minority of crypto perpetuals, "
                 "and activated cross-sectional momentum persists over subsequent hours"),
    )
    study_plan = study_actions.add_parser(
        "plan", help="show the full-market symbol-by-month acquisition matrix without downloading bars",
    )
    study_plan.add_argument("study_id")
    study_plan.add_argument("--dataset", default="market.ohlcv.crypto.binance.usdm-perpetual.1h")
    study_plan.add_argument("--start", required=True, help="inclusive ISO-8601 timestamp with timezone")
    study_plan.add_argument("--end", required=True, help="exclusive ISO-8601 timestamp with timezone")
    study_plan.add_argument("--symbol", action="append", default=[])
    study_freeze = study_actions.add_parser("freeze")
    study_freeze.add_argument("study_id"); study_freeze.add_argument("--version", default="1.0.0")
    study_inspect = study_actions.add_parser("inspect", help="inspect a Study and its bound Dataset Release")
    study_inspect.add_argument("study_id"); study_inspect.add_argument("--version", default="1.0.0")
    study_data = study_actions.add_parser("data", help="preview rows from the Study input without storage plumbing")
    study_data.add_argument("study_id"); study_data.add_argument("--version", default="1.0.0")
    study_data.add_argument("--head", type=int, default=10); study_data.add_argument("--column", action="append")
    study_profile = study_actions.add_parser("profile", help="run basic point-in-time and OHLCV data checks")
    study_profile.add_argument("study_id"); study_profile.add_argument("--version", default="1.0.0")
    study_scaffold = study_actions.add_parser("scaffold", help="generate a minimal DataFrame research script")
    study_scaffold.add_argument("study_id"); study_scaffold.add_argument("--version", default="1.0.0")

    factor = commands.add_parser("factor", help="register and verify governed factor releases")
    factor_actions = factor.add_subparsers(dest="action", required=True)
    factor_register = factor_actions.add_parser("register-sma")
    factor_register.add_argument("--input-identity", required=True); factor_register.add_argument("--fast", type=int, default=20)
    factor_register.add_argument("--slow", type=int, default=50); factor_register.add_argument("--factor-id", default="sma-spread")
    factor_register.add_argument("--version", default="1.0.0")
    factor_verify = factor_actions.add_parser("verify-sma")
    _add_sma_input_arguments(factor_verify); factor_verify.add_argument("--fast", type=int, default=20)
    factor_verify.add_argument("--slow", type=int, default=50)

    strategy_product = commands.add_parser("strategy", help="register governed runnable strategy releases")
    strategy_actions = strategy_product.add_subparsers(dest="action", required=True)
    strategy_open = strategy_actions.add_parser("open", help="open a Strategy workspace from a frozen Study")
    strategy_open.add_argument("strategy_id")
    strategy_open.add_argument("--from-study", required=True, help="Study snapshot reference, for example my-study@1.0.0")
    strategy_bind_factor = strategy_actions.add_parser("bind-factor", help="reuse a Study factor in a Strategy workspace")
    strategy_bind_factor.add_argument("--workspace", dest="workspace", help="Strategy workspace id")
    strategy_bind_factor.add_argument("--ws", dest="workspace", help=argparse.SUPPRESS)
    strategy_bind_factor.add_argument("--name", required=True, help="strategy-local input name")
    strategy_bind_factor.add_argument("--study-factor", required=True, help="factor name from the Study Lock")
    strategy_set_risk = strategy_actions.add_parser("set-risk", help="bind strategy risk code or configuration")
    strategy_set_risk.add_argument("strategy_id")
    strategy_set_risk.add_argument("risk_file")
    strategy_freeze = strategy_actions.add_parser("freeze", help="freeze a Strategy workspace snapshot")
    strategy_freeze.add_argument("strategy_id")
    strategy_freeze.add_argument("--version", default="1.0.0")
    strategy_register = strategy_actions.add_parser("register-sma")
    strategy_register.add_argument("--input-identity", required=True); strategy_register.add_argument("--fast", type=int, default=20)
    strategy_register.add_argument("--slow", type=int, default=50); strategy_register.add_argument("--fee-bps", type=Decimal, default=Decimal("10"))
    strategy_register.add_argument("--version", default="1.2.0"); strategy_register.add_argument("--factor-id", default="sma-spread")
    strategy_register.add_argument("--factor-version", default="1.0.0")
    strategy_actions.add_parser("register-builtins")
    iron_register=strategy_actions.add_parser("register-btc-iron-condor");iron_register.add_argument("--research-spec-hash",required=True)
    strategy_inspect=strategy_actions.add_parser("inspect");strategy_inspect.add_argument("strategy_id")
    strategy_inspect.add_argument("--version")
    strategy_status=strategy_actions.add_parser("status");strategy_status.add_argument("strategy_id");strategy_status.add_argument("--version",required=True)
    strategy_activate=strategy_actions.add_parser("activate");strategy_activate.add_argument("strategy_id");strategy_activate.add_argument("--version",required=True)
    strategy_activate.add_argument("--actor",required=True);strategy_activate.add_argument("--reason",required=True)
    strategy_rollback=strategy_actions.add_parser("rollback");strategy_rollback.add_argument("strategy_id")
    strategy_rollback.add_argument("--actor",required=True);strategy_rollback.add_argument("--reason",required=True)
    strategy_check=strategy_actions.add_parser("check-promotion", help="check promotion evidence without changing strategy lifecycle")
    strategy_check.add_argument("strategy_id"); strategy_check.add_argument("--version", required=True)
    strategy_check.add_argument("--to", required=True, choices=(
        "RESEARCH_VALIDATED", "TRADE_PROXY_VALIDATED", "EXECUTABLE_BACKTEST_VALIDATED",
        "ROBUSTNESS_VALIDATED", "PAPER_APPROVED", "LIVE_LIMITED", "LIVE_APPROVED",
    ))
    strategy_check.add_argument("--evidence", action="append", required=True, help="research, readiness or soak JSON evidence; repeatable")
    strategy_promote=strategy_actions.add_parser("promote", help="promote a Strategy Release with hashed evidence")
    strategy_promote.add_argument("strategy_id"); strategy_promote.add_argument("--version", required=True)
    strategy_promote.add_argument("--to", required=True, choices=(
        "RESEARCH_VALIDATED", "TRADE_PROXY_VALIDATED", "EXECUTABLE_BACKTEST_VALIDATED",
        "ROBUSTNESS_VALIDATED", "PAPER_APPROVED", "LIVE_LIMITED", "LIVE_APPROVED",
    ))
    strategy_promote.add_argument("--evidence", action="append", required=True, help="research or run result JSON evidence; repeatable")
    strategy_promote.add_argument("--actor", required=True); strategy_promote.add_argument("--capital-limit", type=Decimal, required=True)
    strategy_promote.add_argument("--rollback-condition", required=True)

    run_product = commands.add_parser("run", help="run one Strategy Release across backtest, simulation, shadow or paper")
    run_actions = run_product.add_subparsers(dest="action", required=True)
    run_start = run_actions.add_parser("start", help="start a Run workspace from a Study or Strategy snapshot")
    run_target = run_start.add_mutually_exclusive_group(required=True)
    run_target.add_argument("--study", help="Study workspace id for research-mode execution")
    run_target.add_argument("--snapshot", help="Strategy snapshot reference, for example my-strategy@1.0.0")
    run_start.add_argument("--mode", required=True, choices=("research", "backtest", "historical-simulation", "paper", "live"))
    run_backtest_generic = run_actions.add_parser("backtest", help="run a Strategy Release through the unified backtest entry")
    run_backtest_generic.add_argument("--strategy", default="sma-cross-v1@1.2.0")
    _add_sma_input_arguments(run_backtest_generic); _add_sma_run_arguments(run_backtest_generic)
    run_backtest_generic.add_argument("--artifact-root", type=Path)
    run_backtest_generic.add_argument("--execution-calibration", type=Path,
                                      help="ExecutionCalibrationRelease manifest to bind into the backtest artifact")
    run_backtest = run_actions.add_parser("backtest-sma"); _add_sma_input_arguments(run_backtest)
    _add_sma_run_arguments(run_backtest); run_backtest.add_argument("--artifact-root",type=Path)
    run_backtest.add_argument("--execution-calibration", type=Path,
                              help="ExecutionCalibrationRelease manifest to bind into the backtest artifact")
    run_simulate_generic = run_actions.add_parser("simulate", help="run a Strategy Release through historical simulation")
    run_simulate_generic.add_argument("--strategy", default="sma-cross-v1@1.2.0")
    _add_sma_input_arguments(run_simulate_generic); _add_sma_run_arguments(run_simulate_generic)
    run_simulate_generic.add_argument("--run-root", type=Path, required=True)
    run_simulate_generic.add_argument("--artifact-root", type=Path)
    run_simulate_generic.add_argument("--account-id", default="sma-simulation")
    run_simulate_generic.add_argument("--base-asset", default="BTC")
    run_simulate_generic.add_argument("--quote-asset", default="USDT")
    run_simulate = run_actions.add_parser("simulate-sma"); _add_sma_input_arguments(run_simulate)
    _add_sma_run_arguments(run_simulate); run_simulate.add_argument("--run-root", type=Path, required=True)
    run_simulate.add_argument("--artifact-root",type=Path)
    run_simulate.add_argument("--account-id", default="sma-simulation"); run_simulate.add_argument("--base-asset", default="BTC")
    run_simulate.add_argument("--quote-asset", default="USDT")
    run_paper_generic = run_actions.add_parser("paper", help="run a Strategy Release in live-market simulated execution")
    run_paper_generic.add_argument("--strategy", default="sma-cross-v1@1.2.0")
    run_paper_generic.add_argument("--capture", type=Path)
    run_paper_generic.add_argument("--fixture", action="store_true")
    _add_live_binance_bar_arguments(run_paper_generic)
    _add_sma_run_arguments(run_paper_generic)
    run_paper_generic.add_argument("--run-root", type=Path, required=True)
    run_paper_generic.add_argument("--artifact-root", type=Path)
    run_paper_generic.add_argument("--account-id", default="sma-paper")
    run_paper_generic.add_argument("--base-asset", default="BTC")
    run_paper_generic.add_argument("--quote-asset", default="USDT")
    run_paper=run_actions.add_parser("paper-sma");run_paper.add_argument("--capture",type=Path)
    run_paper.add_argument("--fixture",action="store_true");_add_live_binance_bar_arguments(run_paper);_add_sma_run_arguments(run_paper)
    run_paper.add_argument("--run-root",type=Path,required=True);run_paper.add_argument("--artifact-root",type=Path)
    run_paper.add_argument("--account-id",default="sma-paper");run_paper.add_argument("--base-asset",default="BTC")
    run_paper.add_argument("--quote-asset",default="USDT")
    run_shadow_generic = run_actions.add_parser("shadow", help="run a Strategy Release on a capture without submitting orders")
    run_shadow_generic.add_argument("--strategy", default="sma-cross-v1@1.2.0")
    run_shadow_generic.add_argument("--capture", type=Path)
    run_shadow_generic.add_argument("--fixture", action="store_true")
    _add_sma_run_arguments(run_shadow_generic)
    run_shadow_generic.add_argument("--run-root", type=Path, required=True)
    run_shadow_generic.add_argument("--artifact-root", type=Path)
    run_shadow=run_actions.add_parser("shadow-sma", help="run SMA on a capture without submitting orders")
    run_shadow.add_argument("--capture",type=Path);run_shadow.add_argument("--fixture",action="store_true")
    _add_sma_run_arguments(run_shadow);run_shadow.add_argument("--run-root",type=Path,required=True)
    run_shadow.add_argument("--artifact-root",type=Path)
    run_inspect = run_actions.add_parser("inspect"); run_inspect.add_argument("--db", type=Path)
    run_inspect.add_argument("--artifact",type=Path);run_inspect.add_argument("--at")
    run_inspect.add_argument("--run-id")
    target_run_replay = run_actions.add_parser("replay", help="replay a Run workspace from its snapshot")
    target_run_replay.add_argument("--run-id", required=True)
    target_run_compare = run_actions.add_parser("compare", help="compare two Run workspaces")
    target_run_compare.add_argument("--first", required=True)
    target_run_compare.add_argument("--second", required=True)
    run_replay=run_actions.add_parser("replay-sma");run_replay.add_argument("--artifact",type=Path,required=True)
    _add_sma_input_arguments(run_replay)
    replay_capture=run_actions.add_parser("replay-sma-capture");replay_capture.add_argument("--artifact",type=Path,required=True)
    replay_capture.add_argument("--capture",type=Path,required=True)
    run_reference=run_actions.add_parser("reference");run_reference.add_argument("--strategy",choices=("covered-call","spot-perp-carry"),required=True)

    tutorial = commands.add_parser("tutorial", help="guided, credential-free first-use workflows")
    tutorial_actions = tutorial.add_subparsers(dest="action", required=True)
    tutorial_sma = tutorial_actions.add_parser("sma", help="start the deterministic SMA research tutorial")
    tutorial_sma.add_argument("--output-root", type=Path, default=Path("example-output/first-research"))
    tutorial_sma.add_argument("--study-id", default="btc-sma-first")
    return parser


def _add_sma_input_arguments(parser):
    parser.add_argument("--dataset"); parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--start"); parser.add_argument("--end")


def _add_sma_run_arguments(parser):
    parser.add_argument("--fast", type=int, default=20); parser.add_argument("--slow", type=int, default=50)
    parser.add_argument("--initial-cash", type=Decimal, default=Decimal("100000"))
    parser.add_argument("--fee-bps", type=Decimal, default=Decimal("10"))


def _add_live_binance_bar_arguments(parser):
    parser.add_argument("--live-binance-symbol", help="use public Binance spot klines as live-market paper input, e.g. BTCUSDT")
    parser.add_argument("--live-binance-interval", default="1m", help="Binance kline interval for live-market paper input")
    parser.add_argument("--live-binance-limit", type=int, default=120, help="number of recent Binance klines to capture")
    parser.add_argument("--live-binance-base-url", default="https://data-api.binance.vision", help=argparse.SUPPRESS)


def _spec(args: argparse.Namespace) -> OptionChainCaptureSpec:
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
    return OptionChainCaptureSpec(**values)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.group == "catalog":
        return _catalog(args)
    if args.group == "account":
        return _account(args)
    if args.group == "trade":
        return _trade(args)
    if args.group == "order":
        return _trade(args)
    if args.group == "runtime":
        if args.action in {"reference-artifact", "golden"}:
            from kairos.application.runtime_reference_artifact import run_runtime_reference_artifact
            result = run_runtime_reference_artifact(args.root)
            payload = {
                "scenario_id": result.scenario_id,
                "audit_hash": result.audit_hash,
                "artifact": str(result.artifact),
            }
        elif args.action in {"failure-policy", "failure-matrix"}:
            from kairos.application.runtime_failure_policy import run_runtime_failure_policy
            result = run_runtime_failure_policy(args.root)
            payload = {
                "policy_id": result["policy_id"],
                "passed": result["passed"],
                "audit_hash": result["audit_hash"],
                "artifact": result["artifact"],
            }
        elif args.action == "orders":
            from kairos.execution.order_state import DurableOrderStatus
            from kairos.orchestration.runtime_store import SQLiteRuntimeStore
            store = SQLiteRuntimeStore(args.db)
            supplied = (args.client_order_id, args.target, args.actor, args.reason, args.evidence)
            if any(value is not None for value in supplied):
                if not all(value is not None for value in supplied):
                    raise SystemExit("manual resolution requires --client-order-id, --target, --actor, --reason, and --evidence")
                resolution = store.resolve_unresolved_order(
                    args.client_order_id, DurableOrderStatus(args.target), datetime.now(timezone.utc),
                    actor=args.actor, reason=args.reason, evidence=args.evidence,
                )
                payload = {"resolution": to_primitive(resolution)}
            else:
                payload = {
                    "unresolved_orders": [to_primitive(item) for item in store.unresolved_orders()],
                    "manual_resolutions": [to_primitive(item) for item in store.manual_order_resolutions()],
                }
        elif args.action == "calibrate-execution":
            from kairos.execution import build_execution_calibration_release
            release = build_execution_calibration_release(
                args.db, args.output_root, venue=args.venue, environment=args.environment,
                strategy_id=args.strategy, calibration_id=args.calibration_id,
            )
            payload = {
                "release_id": release.release_id,
                "release_hash": release.release_hash,
                "manifest": str(release.manifest_path),
                "sample_count": release.manifest["sample_count"],
                "summary": release.manifest["summary"],
                "limitations": release.manifest["limitations"],
            }
        else:
            payload = _runtime_l4_preflight(args)
        print(json.dumps(payload, indent=2))
        return 0 if payload.get("ready", True) else 2
    if args.group == "init":
        from kairos.project import initialize_project, render_project_init
        result = initialize_project(args.target, name=args.name, force=args.force)
        if args.format == "json":
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        elif not args.quiet:
            print(render_project_init(result))
        return 0
    if args.group in {"study", "factor", "strategy", "run", "tutorial"}:
        return _product_command(args)
    if args.group == "data":
        return _data(args)
    if args.group == "features":
        return _features(args)
    if args.group == "pricing":
        return _pricing(args)
    if args.group == "vol":
        return _vol(args)
    if args.group == "risk":
        return _risk_analytics(args)
    repository = FileResearchRepository(args.data_root)
    service = OptionResearchCaptureService(repository)
    if args.group == "backtest":
        return _backtest(args)
    if args.action == "governance-audit":
        from kairos.research.validation import audit_governance
        result=audit_governance(args.lake_root)
        print(json.dumps({"passed":result.passed,"checked_datasets":result.checked_datasets,
            "checked_studies":result.checked_studies,"checked_strategies":result.checked_strategies,
            "violations":result.violations},ensure_ascii=False,indent=2))
        return 0 if result.passed else 2
    if args.action == "register-btc-iron-condor":
        module = _workspace_research_module("studies.register_btc_iron_condor")
        directory,spec=module.register(args.lake_root);print(f"{directory}: {spec.lifecycle.value} {spec.spec_hash}");return 0
    if args.action == "register-builtin-strategies":
        from kairos.strategies.specs import register_builtin_strategies
        paths=register_builtin_strategies(Path(args.lake_root)/"strategies")
        print(json.dumps({"count":len(paths),"paths":[str(path) for path in paths]},indent=2));return 0
    if args.action == "readiness":
        return _research_readiness(args)
    if args.action == "capture-series":
        if args.instruments:
            return _capture_normalized_series(args)
        spec = _spec(args)
        provider = IbkrSpxwResearchProvider(spec, host=args.host, port=args.port, client_id=args.client_id)
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
            MarketSnapshotStorageDriver(args.dataset_root), on_progress=report_progress,
        ).capture(provider, spec, series_spec, append=args.append)
        print(f"Dataset: {dataset.manifest.dataset_id}")
        print(f"Slices: {dataset.manifest.slice_count}")
        print(f"Hash: {dataset.manifest.content_hash}")
        return 0
    if args.action == "capture":
        spec = _spec(args)
        provider = IbkrSpxwResearchProvider(spec, host=args.host, port=args.port, client_id=args.client_id)
        snapshot, result = service.capture_snapshot(provider, spec)
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


def _product_command(args: argparse.Namespace) -> int:
    import sys
    from kairos.cli_output import render_error, render_product_result, resolve_language
    from kairos.product_workflow import (
        activate_strategy_release,create_study, freeze_study, inspect_run, inspect_strategy_release, inspect_study,
        check_strategy_promotion,
        promote_strategy_release,register_btc_iron_condor_candidate,register_builtin_strategy_releases,rollback_strategy_release,strategy_release_status,
        register_sma_factor, register_sma_strategy,
        replay_sma_capture, replay_sma_run, run_sma_backtest_workflow, run_sma_paper_workflow, run_sma_shadow_workflow,
        run_strategy_backtest_workflow,
        preview_study_data, profile_study, run_reference_strategy_workflow, run_sma_simulation_workflow,
        plan_governed_study, scaffold_study, start_governed_study, start_sma_tutorial, verify_sma_factor,
    )
    from kairos import product_surface
    from kairos.connectors.binance.historical_archive import GracefulShutdown

    def _study_freeze_dispatch(command_args: argparse.Namespace):
        if product_surface.study_exists(command_args.lake_root, command_args.study_id):
            return product_surface.study_freeze(command_args)
        return freeze_study(command_args)

    def _study_inspect_dispatch(command_args: argparse.Namespace):
        if product_surface.study_exists(command_args.lake_root, command_args.study_id):
            return product_surface.study_inspect(command_args)
        return inspect_study(command_args)

    def _strategy_inspect_dispatch(command_args: argparse.Namespace):
        if product_surface.strategy_exists(command_args.lake_root, command_args.strategy_id):
            return product_surface.strategy_inspect(command_args)
        if not getattr(command_args, "version", None):
            raise ValueError("legacy strategy inspect requires --version")
        return inspect_strategy_release(command_args)

    def _run_inspect_dispatch(command_args: argparse.Namespace):
        if getattr(command_args, "run_id", None):
            return product_surface.run_inspect(command_args)
        return inspect_run(command_args)

    handlers = {
        ("study", "open"): product_surface.study_open,
        ("study", "add-data"): product_surface.study_add_data,
        ("study", "add-factor"): product_surface.study_add_factor,
        ("study", "create"): create_study, ("study", "plan"): plan_governed_study,
        ("study", "start"): start_governed_study,
        ("study", "freeze"): _study_freeze_dispatch,
        ("study", "inspect"): _study_inspect_dispatch, ("study", "data"): preview_study_data,
        ("study", "profile"): profile_study, ("study", "scaffold"): scaffold_study,
        ("tutorial", "sma"): start_sma_tutorial,
        ("factor", "register-sma"): register_sma_factor, ("factor", "verify-sma"): verify_sma_factor,
        ("strategy", "open"): product_surface.strategy_open,
        ("strategy", "bind-factor"): product_surface.strategy_bind_factor,
        ("strategy", "set-risk"): product_surface.strategy_set_risk,
        ("strategy", "freeze"): product_surface.strategy_freeze,
        ("strategy", "register-sma"): register_sma_strategy,
        ("strategy","register-builtins"):register_builtin_strategy_releases,("strategy","inspect"):_strategy_inspect_dispatch,
        ("strategy","register-btc-iron-condor"):register_btc_iron_condor_candidate,
        ("strategy","status"):strategy_release_status,("strategy","activate"):activate_strategy_release,
        ("strategy","rollback"):rollback_strategy_release,("strategy","promote"):promote_strategy_release,
        ("strategy","check-promotion"):check_strategy_promotion,
        ("run", "start"): product_surface.run_start,
        ("run", "backtest"): run_strategy_backtest_workflow,
        ("run", "backtest-sma"): run_sma_backtest_workflow,
        ("run", "simulate"): run_sma_simulation_workflow,
        ("run", "simulate-sma"): run_sma_simulation_workflow, ("run", "inspect"): _run_inspect_dispatch,
        ("run","replay"): product_surface.run_replay,
        ("run","compare"): product_surface.run_compare,
        ("run","replay-sma"):replay_sma_run,
        ("run","paper"):run_sma_paper_workflow,
        ("run","paper-sma"):run_sma_paper_workflow,("run","replay-sma-capture"):replay_sma_capture,
        ("run","shadow"):run_sma_shadow_workflow,
        ("run","shadow-sma"):run_sma_shadow_workflow,
        ("run","reference"):run_reference_strategy_workflow,
    }
    try:
        _validate_strategy_scoped_run(args)
        payload = handlers[(args.group, args.action)](args)
    except GracefulShutdown as error:
        print(f"Stopped cleanly: {error}", file=sys.stderr)
        return 130
    except (KeyError, LookupError, PermissionError, ValueError, FileNotFoundError) as error:
        language = resolve_language(args.lang)
        print(render_error(error, language, json_output=args.format == "json"), file=sys.stderr)
        return 2
    if args.quiet:
        return 0
    if args.format == "json":
        print(json.dumps(to_primitive(payload), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_product_result(args.group, args.action, payload, resolve_language(args.lang)))
    return 0


def _validate_strategy_scoped_run(args: argparse.Namespace) -> None:
    if args.group != "run" or args.action not in {"simulate", "paper", "shadow"}:
        return
    strategy = getattr(args, "strategy", "sma-cross-v1")
    strategy_id = str(strategy).split("@", 1)[0]
    if strategy_id != "sma-cross-v1":
        raise ValueError(f"{args.action} currently supports sma-cross-v1 Strategy Releases, got {strategy!r}")


def _data(args: argparse.Namespace) -> int:
    if args.action in {"download", "register-download", "write"}:
        from kairos import product_surface
        handlers = {
            "download": product_surface.data_download,
            "register-download": product_surface.data_register_download,
            "write": product_surface.data_write,
        }
        payload = handlers[args.action](args)
        print(json.dumps(to_primitive(payload), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.action == "soak-binance":
        if args.duration_seconds <= 0 or args.minimum_events <= 0 or args.maximum_silence_seconds <= 0:
            raise SystemExit("soak duration, minimum events and maximum silence must be positive")
        import asyncio
        from kairos.connectors.binance.market_stream import BinanceStreamSession, WebSocketClientConnector, websocket_url
        from kairos.connectors.binance.stream import BinanceCanonicalStreamService
        from kairos.market_data import (
            BoundedEventChannel, RotatingCanonicalCaptureWriter,
            run_binance_market_restart_campaign, run_binance_market_soak,
        )

        symbol = args.symbol.upper()
        stream = f"{symbol.lower()}@{args.channel}"
        instrument = InstrumentId(args.instrument or f"crypto:binance:spot:{symbol}")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        journal = args.journal or (
            Path(args.lake_root) / "source" / "live" / "binance" / f"{symbol.lower()}-{args.channel}-{stamp}.jsonl"
        )
        artifact = args.artifact or journal.with_suffix(".soak.json")

        async def soak():
            def build(index: int, *, campaign: bool):
                leg_journal = journal.with_name(
                    f"{journal.stem}.leg-{index:03d}{journal.suffix}",
                ) if campaign else journal
                leg_canonical = leg_journal.with_suffix(".canonical.jsonl")
                output = BoundedEventChannel(max(4096, args.minimum_events * 2))
                service = BinanceCanonicalStreamService(
                    BinanceStreamSession(
                        WebSocketClientConnector(), websocket_url(
                            Environment.LIVE, stream, public_only=True,
                        ), journal=leg_journal,
                    ),
                    {symbol: instrument}, output, source_instance="kairos-soak", stream_id=stream,
                    canonical_capture=RotatingCanonicalCaptureWriter(
                        leg_canonical, session_id=leg_journal.stem, source="binance",
                        maximum_segment_events=args.capture_segment_events,
                        maximum_segment_bytes=args.capture_segment_bytes,
                        maximum_total_bytes=args.capture_total_bytes,
                    ),
                )
                return service, output
            if args.restart_interval_seconds:
                return await run_binance_market_restart_campaign(
                    lambda index: build(index, campaign=True), stream_id=stream,
                    duration_seconds=args.duration_seconds,
                    restart_interval_seconds=args.restart_interval_seconds,
                    minimum_events=args.minimum_events,
                    maximum_silence_seconds=args.maximum_silence_seconds,
                    artifact_path=artifact,
                    maximum_channel_utilization=args.maximum_channel_utilization,
                )
            service, output = build(1, campaign=False)
            return await run_binance_market_soak(
                service, output, duration_seconds=args.duration_seconds,
                minimum_events=args.minimum_events,
                maximum_silence_seconds=args.maximum_silence_seconds,
                artifact_path=artifact,
                maximum_channel_utilization=args.maximum_channel_utilization,
            )

        result = asyncio.run(soak())
        print(json.dumps(to_primitive(result), ensure_ascii=False, indent=2))
        return 0 if result.passed else 2
    if args.action == "live-binance":
        if args.messages <= 0:
            raise SystemExit("--messages must be positive")
        import asyncio
        from kairos.connectors.binance.market_stream import BinanceStreamSession, WebSocketClientConnector, websocket_url
        from kairos.connectors.binance.stream import BinanceCanonicalStreamService
        from kairos.market_data import BoundedEventChannel
        from kairos.market_data import CanonicalCaptureWriter

        symbol = args.symbol.upper()
        stream = f"{symbol.lower()}@{args.channel}"
        instrument = InstrumentId(args.instrument or (
            f"crypto:binance:{'futures' if args.futures else 'spot'}:{symbol}"
        ))
        journal = args.journal or (
            Path(args.lake_root) / "source" / "live" / "binance"
            / f"{symbol.lower()}-{args.channel}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
        )
        canonical_path = journal.with_suffix(".canonical.jsonl")

        async def capture():
            output = BoundedEventChannel(max(16, args.messages * 2))
            service = BinanceCanonicalStreamService(
                BinanceStreamSession(
                    WebSocketClientConnector(), websocket_url(
                        Environment.LIVE, stream, futures=args.futures, public_only=not args.futures,
                    ),
                    journal=journal,
                ),
                {symbol: instrument}, output,
                source_instance="kairos-cli", stream_id=stream,
                canonical_capture=CanonicalCaptureWriter(
                    canonical_path, session_id=journal.stem, source="binance",
                ),
            )
            producer = asyncio.create_task(service.run(message_limit=args.messages))
            events = [event async for event in output.events()]
            await producer
            return service, events

        service, events = asyncio.run(capture())
        print(json.dumps({
            "provider": "binance", "stream": stream, "instrument_id": instrument.value,
            "raw_messages": service.raw_messages, "canonical_events": service.canonical_events,
            "reconnects": service.reconnects, "raw_journal": str(journal),
            "canonical_journal": str(canonical_path),
            "events": to_primitive(events),
        }, ensure_ascii=False, indent=2))
        return 0
    if args.action == "search":
        dimensions = {}
        for item in args.dimension:
            if "=" not in item:
                raise SystemExit("--dimension must use key=value")
            key, value = item.split("=", 1)
            if not key.strip() or not value.strip():
                raise SystemExit("--dimension key and value cannot be empty")
            dimensions[key.strip()] = value.strip()
        products = ResearchDataClient(args.lake_root).search(**dimensions)
        print(json.dumps({"products": [ResearchDataClient(args.lake_root).describe(item) for item in products]},
                         ensure_ascii=False, indent=2)); return 0
    if args.action == "describe":
        print(json.dumps(ResearchDataClient(args.lake_root).describe(args.dataset), ensure_ascii=False, indent=2)); return 0
    if args.action in {"doctor", "diagnostics"}:
        from kairos.data.diagnostics import DataDiagnosticsService
        service = DataDiagnosticsService(args.lake_root)
        report = service.doctor(args.dataset) if args.action == "doctor" else service.audit()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2 if args.action == "diagnostics" and args.strict and not report["healthy"] else 0
    if args.action == "us-equity-momentum-diagnostics":
        from kairos.features import UsEquityMomentumDiagnostics
        report = UsEquityMomentumDiagnostics(args.lake_root).report(study_id=args.study_id, version=args.version)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2 if args.strict and report["summary"]["errors"] else 0
    if args.action == "validate":
        from kairos.data.quality import DatasetQualityService
        assessment = DatasetQualityService(args.lake_root).assess(args.release)
        print(json.dumps(to_primitive(assessment), ensure_ascii=False, indent=2))
        return 0 if assessment.passed else 2
    if args.action == "prepare":
        register_default_products(args.lake_root)
        if args.connector_config is not None:
            register_configured_products(args.lake_root, args.connector_config)
        from kairos.product_workflow import _archive_progress
        providers = default_provider_registry(
            args.lake_root, connector_config=args.connector_config,
            progress=None if args.quiet else _archive_progress,
        )
        client = ResearchDataClient(args.lake_root, providers=providers)
        from kairos.data.preparation import DataPreparationService
        prepared = DataPreparationService(client).prepare(
            args.dataset, start=datetime.fromisoformat(args.start), end=datetime.fromisoformat(args.end),
            minimum_quality=QualityLevel(args.quality), provider=args.provider, venue=args.venue,
            acquire_missing=args.acquire_missing, promote=args.promote, actor=args.actor, reason=args.reason,
        )
        print(json.dumps(to_primitive(prepared), ensure_ascii=False, indent=2)); return 0
    if args.action == "prepare-us-equity-momentum":
        result = _prepare_us_equity_momentum(args)
        print(json.dumps(to_primitive(result), ensure_ascii=False, indent=2))
        return 0 if result["readiness"]["summary"]["errors"] == 0 else 2
    if args.action == "query":
        if args.limit <= 0:
            raise SystemExit("--limit must be positive")
        query = ResearchDataClient(args.lake_root).get(
            args.dataset, start=args.start, end=args.end, fields=tuple(args.field) or None,
        )
        rows = query.collect(OutputFormat.ROWS)
        print(json.dumps({
            "release_id": query.release_id, "explain": query.explain(),
            "returned_rows": min(len(rows), args.limit), "total_rows": len(rows),
            "rows": to_primitive(rows[:args.limit]),
        }, ensure_ascii=False, indent=2)); return 0
    if args.action == "freeze":
        client = ResearchDataClient(args.lake_root)
        queries = tuple(client.get(dataset) for dataset in args.dataset)
        target = client.freeze_study(
            args.output, args.study_id, queries, code_version=args.code_version,
        )
        print(json.dumps({
            "study_id": args.study_id, "snapshot": str(target),
            "release_ids": [query.release_id for query in queries],
        }, ensure_ascii=False, indent=2)); return 0
    if args.action == "catalog":
        if args.refresh:
            register_default_products(args.lake_root)
        catalog = DataCatalog(args.lake_root)
        if args.refresh:
            catalog.discover(); catalog.save()
        values = [{
            "logical_key": str(product.key), "title": product.title, "layer": product.layer.value,
            "dimensions": dict(product.dimensions), "primary_time": product.primary_time,
            "sources": to_primitive(product.sources),
            "releases": [{
                "release_id": release.release_id, "version": release.release_version,
                "provider": release.provider, "venue": release.venue, "content_hash": release.content_hash,
                "quality_level": release.quality_level.value, "status": release.status.value,
                "published_at": release.published_at, "aliases": list(release.aliases),
            } for release in catalog.releases(product)],
        } for product in catalog.products()]
        print(json.dumps({"products": values}, ensure_ascii=False, indent=2)); return 0
    if args.action == "compare":
        comparison = ResearchDataClient(args.lake_root).compare(args.first, args.second)
        print(json.dumps(comparison, ensure_ascii=False, indent=2)); return 0
    if args.action == "audit-artifact":
        from kairos.data.artifact_audit import audit_governed_artifact
        report = audit_governed_artifact(args.lake_root, args.artifact)
        print(json.dumps(to_primitive(report), ensure_ascii=False, indent=2))
        return 0 if report.passed else 2
    if args.action == "alias":
        catalog = DataCatalog(args.lake_root)
        release = catalog.promote_alias(
            args.alias, args.release, actor=args.actor, reason=args.reason,
            quality_report_hash=args.quality_report_hash,
        )
        print(json.dumps({"alias": args.alias, "release_id": release.release_id}, indent=2)); return 0
    if args.action in {"plan", "acquire"}:
        register_default_products(args.lake_root)
        if args.connector_config is not None:
            register_configured_products(args.lake_root, args.connector_config)
        from kairos.product_workflow import _archive_progress
        providers = default_provider_registry(
            args.lake_root, connector_config=args.connector_config,
            progress=(None if args.quiet or args.action == "plan" else _archive_progress),
        )
        client = ResearchDataClient(args.lake_root, providers=providers)
        start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
        plan = client.plan(args.dataset, start=start, end=end, provider=args.provider, venue=args.venue)
        if args.action == "plan":
            print(json.dumps(to_primitive(plan), ensure_ascii=False, indent=2)); return 0
        release = client.acquire(plan, refresh=args.refresh)
        print(json.dumps(to_primitive(release), ensure_ascii=False, indent=2)); return 0
    if args.action == "promote":
        release = DataCatalog(args.lake_root).promote(
            args.release, args.status, actor=args.actor, reason=args.reason,
        )
        print(json.dumps(to_primitive(release), ensure_ascii=False, indent=2)); return 0
    if args.action == "quarantine-insecure-massive-cache":
        moved = MassiveVendorArchiveClient.quarantine_non_https(args.lake_root)
        print(json.dumps({"quarantined": len(moved), "paths": [str(item) for item in moved]}, ensure_ascii=False, indent=2)); return 0
    if args.action == "sync-massive-reference":
        pipeline = MassiveReferencePipeline(args.lake_root, MassiveClient(MassiveConfig.from_env()))
        result: dict[str, object] = {"code_tables": pipeline.sync_code_tables()}
        if args.equity_tickers:
            result["equity_tickers"] = pipeline.sync_equity_tickers(include_inactive=not args.active_only)
        if args.ticker:
            if not args.start or not args.end:
                raise SystemExit("--start and --end are required with --ticker")
            result["corporate_actions"] = pipeline.sync_corporate_actions(args.ticker, datetime.fromisoformat(args.start), datetime.fromisoformat(args.end))
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    if args.action == "build-massive-equity-identity":
        reference_rows = json.loads(args.reference_rows.read_text(encoding="utf-8"))
        ticker_events = json.loads(args.ticker_events.read_text(encoding="utf-8")) if args.ticker_events else []
        resolver = MassiveEquityIdentityResolver()
        resolved = resolver.resolve(reference_rows, ticker_events)
        manifest = resolver.save(resolved, args.lake_root)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0 if not resolved.quarantined else 2
    if args.action == "build-massive-slices":
        dataset = MassiveMarketSnapshotBuilder(args.lake_root, reference_catalog_path=args.reference_catalog_path, dataset_root=args.dataset_root).build(
            args.source_dataset, args.output_dataset, datetime.fromisoformat(args.start), datetime.fromisoformat(args.end),
            sampling_seconds=args.sampling_seconds, max_quote_age_seconds=args.max_quote_age_seconds,
            split=args.split, risk_free_rate=args.risk_free_rate)
        print(f"{dataset.manifest.dataset_id}: slices={dataset.manifest.slice_count} hash={dataset.manifest.content_hash}")
        return 0
    if args.action == "massive-entitlement-diagnostics":
        report = MassiveEntitlementDiagnostics(MassiveClient(MassiveConfig.from_env())).check(
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
    if args.action == "massive-fetch":
        client = MassiveClient(MassiveConfig.from_env())
        archive = MassiveVendorArchiveClient(args.lake_root, client)
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
    if args.action == "prepare-spxw-daily-ohlcv":
        manifest = SpxwDailyOhlcvPipeline(args.lake_root).prepare(
            args.dataset_id, date.fromisoformat(args.start), date.fromisoformat(args.end),
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    if args.action == "prepare-option-daily-ohlcv":
        manifest = OptionDailyOhlcvPipeline(args.lake_root, args.option_root).prepare(
            args.dataset_id, date.fromisoformat(args.start), date.fromisoformat(args.end),
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    if args.action == "prepare-equity-daily-ohlcv":
        manifest = MassiveEquityDailyOhlcvPipeline(
            args.lake_root, MassiveClient(MassiveConfig.from_env()),
        ).prepare(
            args.dataset_id, args.ticker, date.fromisoformat(args.start), date.fromisoformat(args.end),
            view=args.view,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    if args.action == "prepare-option-close-implied-volatility":
        manifest = OptionCloseImpliedVolatilityPipeline(args.lake_root).prepare(
            args.dataset_id, args.option_dataset, args.equity_dataset,
            risk_free_rate=args.risk_free_rate, dividend_yield=args.dividend_yield,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    if args.action == "btc-options-readiness":
        module = _workspace_research_module("studies.btc_options_readiness")
        result = module.btc_options_readiness(args.lake_root); print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["signal_research_ready"] else 2
    metadata = ResearchDataClient(args.lake_root).metadata(args.dataset)
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


def _prepare_us_equity_momentum(args: argparse.Namespace) -> dict[str, object]:
    from kairos.data.preparation import DataPreparationService
    from kairos.features import UsEquityMomentumDiagnostics
    from kairos.product_workflow import start_governed_study

    start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("US equity momentum preparation requires timezone-aware increasing [start,end) timestamps")
    register_default_products(args.lake_root)
    if args.connector_config is not None:
        register_configured_products(args.lake_root, args.connector_config)
    providers = default_provider_registry(
        args.lake_root, connector_config=args.connector_config,
        progress=None if args.quiet else None,
    )
    client = ResearchDataClient(args.lake_root, providers=providers)
    prepared_raw = []
    raw_release_paths = []
    for raw_dataset in args.raw_dataset:
        raw = DataPreparationService(client).prepare(
            raw_dataset,
            start=start,
            end=end,
            minimum_quality=QualityLevel.RESEARCH,
            provider=args.provider,
            venue=args.venue,
            acquire_missing=True,
            promote=False,
            actor="us-equity-momentum-one-click",
            reason="prepare US equity momentum source data",
        )
        prepared_raw.append(raw)
        raw_release_paths.append(client.catalog.release(raw.release_id).relative_path)
    raw_source_directory = _common_lake_directory(args.lake_root, raw_release_paths)
    corporate_actions_directory = args.corporate_actions_directory
    corporate_action_sync = None
    if corporate_actions_directory is None and args.sync_corporate_actions:
        corporate_action_sync = _sync_us_equity_momentum_corporate_actions(
            args.lake_root, raw_release_paths, start, end, dataset_id=args.dataset_id,
        )
        corporate_actions_directory = corporate_action_sync["directory"]
    reference_directory = args.reference_directory
    reference_evidence = {"directory": reference_directory, "auto_detected": False}
    if reference_directory is None:
        reference_evidence = _latest_us_equity_identity_reference(args.lake_root)
        reference_directory = reference_evidence["directory"]
    policy = UsEquityMomentumPolicy(
        minimum_price=args.minimum_price,
        minimum_adv20=args.minimum_adv20,
        minimum_history=args.minimum_history,
    )
    features_manifest = UsEquityMomentumDatasetBuilder(args.lake_root).build_from_ohlcv_directory(
        raw_source_directory,
        dataset_id=args.dataset_id,
        policy=policy,
        corporate_actions_directory=corporate_actions_directory,
        reference_directory=reference_directory,
    )
    study_args = argparse.Namespace(
        lake_root=args.lake_root,
        study_id=args.study_id,
        version=args.version,
        hypothesis=args.hypothesis,
        dataset="features.momentum.equity.us.1d",
        start=args.start,
        end=args.end,
        quiet=args.quiet,
    )
    study = start_governed_study(study_args)
    readiness = UsEquityMomentumDiagnostics(args.lake_root).report(study_id=args.study_id, version=args.version)
    return {
        "workflow": "us-equity-momentum",
        "scope": "bounded-configured-products",
        "raw_datasets": list(args.raw_dataset),
        "raw_source_directory": raw_source_directory,
        "raw_releases": [to_primitive(item) for item in prepared_raw],
        "corporate_actions": (
            corporate_action_sync
            if corporate_action_sync is not None
            else {"directory": corporate_actions_directory, "synced": False}
        ),
        "reference": reference_evidence,
        "features": features_manifest,
        "study": study,
        "readiness": readiness,
        "ready_for_study": readiness["ready_for_study"],
        "ready_for_backtest": readiness["ready_for_backtest"],
        "limitations": [
            "This command prepares configured Massive equity products, not a proven full-market active/inactive universe.",
            "Full backtest readiness still requires complete reference, coverage, corporate action and delisting evidence.",
        ],
    }


def _latest_us_equity_identity_reference(lake_root: str | Path) -> dict[str, object]:
    root = Path(lake_root)
    manifests = sorted((root / "reference/provider=massive/equity_identity").glob("version=*/manifest.json"))
    if not manifests:
        return {"directory": None, "auto_detected": False, "reason": "missing"}
    candidates = []
    for path in manifests:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if int(manifest.get("quarantine_count", 0) or 0) != 0:
            continue
        if not (path.parent / "instruments.json").exists() or not (path.parent / "mappings.json").exists():
            continue
        candidates.append((path, manifest))
    if not candidates:
        return {"directory": None, "auto_detected": False, "reason": "no clean equity_identity manifest"}
    path, manifest = candidates[-1]
    release = _ensure_us_equity_identity_release(root, path.parent, manifest)
    return {
        "directory": str(path.parent.relative_to(root)),
        "auto_detected": True,
        "content_sha256": manifest.get("sha256"),
        "release_id": release.release_id,
        "quality_level": release.quality_level.value,
        "instrument_count": manifest.get("instrument_count"),
        "mapping_count": manifest.get("mapping_count"),
    }


def _ensure_us_equity_identity_release(root: Path, directory: Path, manifest: dict[str, object]) -> DatasetRelease:
    digest = str(manifest.get("sha256") or "")
    if not digest:
        raise ValueError(f"equity identity manifest is missing sha256: {directory}")
    register_default_products(root)
    catalog = DataCatalog(root)
    release_id = f"identity_{digest[:24]}"
    try:
        return catalog.release(release_id)
    except KeyError:
        pass
    product = catalog.product("reference.identity.equity.us.massive")
    catalog.register_release(DatasetRelease(
        release_id,
        product.key,
        f"content.{digest[:16]}",
        "reference.identity.equity.us.massive.v1",
        "1",
        "massive.equity_identity",
        "1",
        str(directory.relative_to(root)),
        "json",
        digest,
        "massive",
        "us-securities",
        ("reference.identity.equity.us.massive@latest-research",),
        DatasetStatus.APPROVED_FOR_RESEARCH,
        QualityLevel.RESEARCH,
        datetime.now(timezone.utc).isoformat(),
        DatasetStorageKind.REFERENCE,
        "1",
    ))
    catalog.save()
    assessment = DatasetQualityService(root).assess(release_id)
    return DataCatalog(root).release(release_id)


def _sync_us_equity_momentum_corporate_actions(
    lake_root: str | Path,
    raw_release_paths: list[str],
    start: datetime,
    end: datetime,
    *,
    dataset_id: str,
) -> dict[str, object]:
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("corporate action sync requires timezone-aware increasing [start,end) timestamps")
    ticker_map = _raw_equity_ticker_map(lake_root, raw_release_paths)
    if not ticker_map:
        raise ValueError("cannot sync corporate actions because prepared raw releases contain no ticker/instrument rows")

    archive = MassiveVendorArchiveClient(lake_root, MassiveClient(MassiveConfig.from_env()))
    events: list[dict[str, object]] = []
    receipts: list[str] = []
    per_ticker: dict[str, dict[str, int]] = {}
    for ticker, instrument_id in sorted(ticker_map.items()):
        split_archive = archive.fetch_pages("/v3/reference/splits", {
            "ticker": ticker,
            "execution_date.gte": start.date(),
            "execution_date.lt": end.date(),
            "limit": 1000,
        })
        dividend_archive = archive.fetch_pages("/v3/reference/dividends", {
            "ticker": ticker,
            "ex_dividend_date.gte": start.date(),
            "ex_dividend_date.lt": end.date(),
            "limit": 1000,
        })
        receipts.extend([
            str((split_archive.directory / "receipt.json").relative_to(Path(lake_root))),
            str((dividend_archive.directory / "receipt.json").relative_to(Path(lake_root))),
        ])
        split_count = 0
        dividend_count = 0
        for row in archive.iter_results(split_archive):
            ratio = Decimal(str(row["split_to"])) / Decimal(str(row["split_from"]))
            if ratio <= 0:
                raise ValueError(f"Massive split ratio must be positive for {ticker}")
            events.append({
                "source": "massive.splits",
                "source_id": str(row.get("id") or f"{ticker}:{row.get('execution_date') or row.get('ex_date')}:{ratio}"),
                "ticker": ticker,
                "instrument_id": instrument_id,
                "effective_at": {"$datetime": _corporate_action_date(row.get("execution_date") or row.get("ex_date")).isoformat()},
                "ratio": {"$decimal": str(ratio)},
            })
            split_count += 1
        for row in archive.iter_results(dividend_archive):
            amount = Decimal(str(row["cash_amount"]))
            if amount < 0:
                raise ValueError(f"Massive dividend amount cannot be negative for {ticker}")
            events.append({
                "source": "massive.dividends",
                "source_id": str(row.get("id") or f"{ticker}:{row.get('ex_dividend_date')}:{amount}"),
                "ticker": ticker,
                "instrument_id": instrument_id,
                "ex_date": {"$datetime": _corporate_action_date(row.get("ex_dividend_date")).isoformat()},
                "pay_date": {"$datetime": _corporate_action_date(row.get("pay_date") or row.get("ex_dividend_date")).isoformat()},
                "currency": str(row.get("currency") or "USD"),
                "amount_per_share": {"$decimal": str(amount)},
            })
            dividend_count += 1
        per_ticker[ticker] = {"splits": split_count, "dividends": dividend_count}

    digest = sha256(json.dumps(events, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    directory = (
        Path(lake_root)
        / "reference/provider=massive/corporate_actions/scope=us_equity_momentum_bounded"
        / f"dataset={_safe_dataset_component(dataset_id)}"
        / f"version={digest}"
    )
    write_json(directory / "events.json", events)
    write_json(directory / "manifest.json", {
        "manifest_version": 1,
        "provider": "massive",
        "scope": "us_equity_momentum_bounded",
        "dataset_id": dataset_id,
        "identity_source": "prepared raw release instrument_id",
        "boundary": "[start,end)",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "ticker_count": len(ticker_map),
        "event_count": len(events),
        "sha256": digest,
        "source_receipts": receipts,
        "per_ticker": per_ticker,
        "known_limitations": [
            "bounded ticker corporate action sync; requires full point-in-time identity mapping before full-market backtest readiness",
        ],
    })
    catalog = DataCatalog(lake_root)
    product = catalog.product("reference.corporate_actions.equity.us.massive")
    release_id = f"corpact_{digest[:24]}"
    relative = str(directory.relative_to(Path(lake_root)))
    catalog.register_release(DatasetRelease(
        release_id,
        product.key,
        f"content.{digest[:16]}",
        "reference.corporate_actions.equity.us.massive.v1",
        "1",
        "massive.corporate_actions",
        "1",
        relative,
        "json",
        digest,
        "massive",
        "us-securities",
        ("reference.corporate_actions.equity.us.massive@latest-research",),
        DatasetStatus.APPROVED_FOR_RESEARCH,
        QualityLevel.RESEARCH,
        datetime.now(timezone.utc).isoformat(),
        DatasetStorageKind.REFERENCE,
        "1",
    ))
    catalog.save()
    assessment = DatasetQualityService(lake_root).assess(release_id)
    return {
        "synced": True,
        "directory": str(directory.relative_to(Path(lake_root))),
        "release_id": release_id,
        "content_sha256": digest,
        "quality_level": assessment.level.value,
        "quality_passed": assessment.passed,
        "event_count": len(events),
        "ticker_count": len(ticker_map),
        "per_ticker": per_ticker,
    }


def _raw_equity_ticker_map(lake_root: str | Path, relative_paths: list[str]) -> dict[str, str]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("US equity momentum corporate action sync requires the 'data' optional dependency") from error
    root = Path(lake_root)
    mapping: dict[str, str] = {}
    for relative in relative_paths:
        source = root / relative
        for path in _parquet_files(source):
            for row in pq.read_table(path, columns=["ticker", "instrument_id"]).to_pylist():
                ticker = str(row.get("ticker") or "").strip().upper()
                instrument_id = str(row.get("instrument_id") or "").strip()
                if not ticker or not instrument_id:
                    continue
                previous = mapping.get(ticker)
                if previous is not None and previous != instrument_id:
                    raise ValueError(f"ticker {ticker} maps to multiple instrument IDs in prepared raw data")
                mapping[ticker] = instrument_id
    return mapping


def _parquet_files(source: Path) -> list[Path]:
    manifest_path = source / "manifest.json"
    declared: list[Path] = []
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("file", "files"):
            value = manifest.get(key)
            if isinstance(value, str):
                declared.append(source / value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        declared.append(source / item)
                    elif isinstance(item, dict) and item.get("path"):
                        declared.append(source / str(item["path"]))
    existing = sorted({path for path in declared if path.suffix == ".parquet" and path.exists()})
    return existing or sorted(source.glob("**/part-*.parquet")) or sorted(source.glob("*.parquet"))


def _corporate_action_date(value: object) -> datetime:
    if value is None:
        raise ValueError("Massive corporate action is missing a date")
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return datetime.combine(date.fromisoformat(str(value)), datetime.min.time(), timezone.utc)


def _safe_dataset_component(value: str) -> str:
    return "".join(item if item.isalnum() or item in {"-", "_", "."} else "_" for item in value)


def _common_lake_directory(lake_root: str | Path, relative_paths: list[str]) -> str:
    if not relative_paths:
        raise ValueError("at least one raw release path is required")
    if len(relative_paths) == 1:
        return relative_paths[0]
    root = Path(lake_root)
    paths = [root / item for item in relative_paths]
    common = Path(os.path.commonpath([str(item) for item in paths]))
    return str(common.relative_to(root)) if common.is_relative_to(root) else str(common)


def _features(args: argparse.Namespace) -> int:
    if args.feature_set == "us-equity-momentum-v1":
        if not args.source_directory or not args.dataset_id:
            raise SystemExit("us-equity-momentum-v1 requires --source-directory and --dataset-id")
        policy = UsEquityMomentumPolicy(
            minimum_price=args.minimum_price,
            minimum_adv20=args.minimum_adv20,
            minimum_history=args.minimum_history,
        )
        manifest = UsEquityMomentumDatasetBuilder(args.lake_root).build_from_ohlcv_directory(
            args.source_directory, dataset_id=args.dataset_id, policy=policy,
            corporate_actions_directory=args.corporate_actions_directory,
            reference_directory=args.reference_directory,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    builders = {"btc-iv-rv-v1": BtcIvRvFeatureBuilder, "btc-term-skew-v1": BtcTermSkewFeatureBuilder,
                "btc-deribit-trade-skew-v1": BtcDeribitTradeSkewFeatureBuilder}
    release = builders[args.feature_set](args.lake_root).build()
    print(f"{release.release_id}: product={release.product_key} hash={release.content_hash}")
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
    module = _workspace_research_module("studies.spxw_put_skew.study")
    ResearchConfig, execute_research = module.ResearchConfig, module.execute_research
    raw = json.loads(args.study_config.read_text(encoding="utf-8"))
    raw.pop("dataset_id", None)
    decimal_fields = {
        "target_short_delta", "target_long_delta", "high_skew_percentile", "minimum_quote_coverage",
        "maximum_stale_rate", "minimum_surface_calibration_rate", "profit_target",
        "stop_loss_multiple", "commission_per_contract",
    }
    config = ResearchConfig(**{key: Decimal(str(value)) if key in decimal_fields else value for key, value in raw.items()})
    client = ResearchDataClient(args.lake_root, run_mode=RunMode.BACKTEST)
    feed = client.replay_snapshots(args.dataset)
    dataset = feed.dataset
    collection = client.collection(args.dataset)
    panel, readiness, conclusion = execute_research(dataset, config, collection)
    release = feed.release
    artifact_payload = {
        "artifact_schema_version": 1,
        "study": "spxw-put-skew-readiness",
        "consumed_inputs": [{
            "release_id": release.release_id,
            "content_hash": release.content_hash,
            "quality_level": release.quality_level.value,
        }],
        "config": to_primitive(config),
        "readiness": to_primitive(readiness),
        "conclusion": to_primitive(conclusion),
        "eligible_panel_rows": len(panel),
    }
    material = json.dumps(artifact_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    artifact_payload["audit_hash"] = sha256(material.encode()).hexdigest()
    artifact = Path(args.lake_root) / "studies" / "spxw-put-skew-readiness" / artifact_payload["audit_hash"] / "manifest.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    temporary = artifact.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(artifact_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(artifact)
    print(f"Dataset: {dataset.manifest.dataset_id}")
    print(f"Ready: {readiness.ready}")
    print(f"Conclusion status: {conclusion.status}")
    print(f"Eligible panel rows: {len(panel)}")
    for key, value in readiness.metrics.items():
        print(f"{key}: {value}")
    for reason in readiness.reasons:
        print(f"FAIL: {reason}")
    print(f"Artifact: {artifact}")
    return 0 if readiness.ready else 2


def _vol(args: argparse.Namespace) -> int:
    client = ResearchDataClient(args.lake_root, run_mode=RunMode.RESEARCH)
    feed = client.replay_snapshots(args.dataset)
    dataset = feed.dataset
    catalog = dataset.reference_catalog()
    valuation_engine = OptionValuationService(catalog, risk_free_rate=args.rate, dividend_yield=args.dividend_yield)
    surfaces, failures = [], []
    for market in dataset.slices:
        _, valuation = valuation_engine.value(market)
        failures.extend(valuation.failures)
        if valuation.surface is not None:
            surfaces.append(valuation.surface)
    calibrated = sum(any(smile.parameters is not None for smile in item.smiles) for item in surfaces)
    arbitrage_passed = sum(item.diagnostics.passed for item in surfaces)
    print(f"Dataset: {dataset.manifest.dataset_id}")
    print(f"Surfaces: {len(surfaces)}")
    print(f"Calibrated: {calibrated}")
    print(f"Arbitrage checks passed: {arbitrage_passed}")
    print(f"Valuation failures: {len(failures)}")
    if surfaces:
        from kairos.data.surface_features import SurfaceFeaturePublisher
        release = SurfaceFeaturePublisher(args.lake_root).publish(
            tuple(surfaces), input_release_id=feed.release.release_id,
        )
        print(f"Last surface: {surfaces[-1].surface_id}")
        print(f"Last input hash: {surfaces[-1].input_hash}")
        print(f"Feature Release: {release.release_id}")
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


def _backtest(args: argparse.Namespace) -> int:
    if args.action in {"spxw-reference-scenario", "golden-spxw"}:
        from kairos.backtest.spxw_reference_pipeline import build_spxw_reference_pipeline
        payload = build_spxw_reference_pipeline(
            args.lake_root,
            args.backtest_root,
            event_release_id=args.event_release,
            source_slice_release_id=args.source_slices,
            curated_slice_release_id=args.curated_slices,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.action == "sma":
        from kairos.domain.market_data import Bar
        client = ResearchDataClient(args.lake_root, run_mode=RunMode.BACKTEST)
        query = client.get(args.dataset, start=args.start, end=args.end, fields=(
            "instrument_id", "period_start", "period_end", "open", "high", "low", "close", "volume",
        ))
        rows = query.collect(OutputFormat.ROWS)
        release_id = query.release_id
        bars = tuple(Bar(
            InstrumentId(str(row["instrument_id"])),
            row["period_start"] if isinstance(row["period_start"], datetime) else datetime.fromisoformat(str(row["period_start"]).replace("Z", "+00:00")),
            row["period_end"] if isinstance(row["period_end"], datetime) else datetime.fromisoformat(str(row["period_end"]).replace("Z", "+00:00")),
            Decimal(str(row["open"])), Decimal(str(row["high"])), Decimal(str(row["low"])),
            Decimal(str(row["close"])), Decimal(str(row["volume"])),
        ) for row in rows)
        result = backtest_sma_cross(
            BarSeries(release_id, bars), SmaCrossConfig(args.fast, args.slow, args.initial_cash, args.fee_bps),
        )
        release = client.resolve(release_id)
        payload = {
            "artifact_schema_version": 1,
            "strategy": f"sma-{args.fast}-{args.slow}", "release_id": release_id,
            "input": {
                "logical_key": str(release.product_key), "release_id": release.release_id,
                "content_hash": release.content_hash, "schema_id": release.schema_id,
                "schema_version": release.schema_version, "transform_id": release.transform_id,
                "transform_version": release.transform_version, "quality_level": release.quality_level.value,
                "start": args.start, "end": args.end, "boundary": "[start,end)",
            },
            "config": {
                "fast": args.fast, "slow": args.slow, "initial_cash": str(args.initial_cash),
                "fee_bps": str(args.fee_bps),
            },
            "bars": len(bars), "metrics": to_primitive(result.metrics),
        }
        material = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        audit_hash = sha256(material.encode()).hexdigest()
        payload["audit_hash"] = audit_hash
        directory = Path(args.backtest_root) / "sma" / audit_hash
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "manifest.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(target)
        payload["artifact"] = str(target)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    backtests = BacktestRepository(args.backtest_root)
    if args.action == "synthetic-scenario":
        dataset = build_synthetic_backtest_dataset(SyntheticScenario(args.scenario), split=args.split)
        directory = MarketSnapshotStorageDriver(args.dataset_root).save(dataset)
        product = DataProductDefinition(
            DatasetKey(f"curated.synthetic.{args.scenario}.{args.split}"), f"Synthetic {args.scenario} {args.split}",
            DatasetLayer.CURATED, dimensions={"synthetic": "true", "split": args.split}, primary_time="timestamp",
        )
        register_market_replay_dataset(args.lake_root, dataset, directory, product, provider="synthetic", venue="synthetic", synthetic=True)
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
        feed = ResearchDataClient(args.lake_root, dataset_root=args.dataset_root,
                                  run_mode=RunMode.BACKTEST).replay_snapshots(args.dataset)
        dataset = feed.dataset
        values = json.loads(args.config.read_text()) if args.config else {}
        strategy_config = BullPutSpreadConfig(**_coerce_decimal_fields(values.get("strategy", {}), BullPutSpreadConfig))
        risk_limits = RiskLimits(**_coerce_decimal_fields(values.get("risk", {}), RiskLimits))
        backtest_values = _coerce_decimal_fields(values.get("backtest", {}), BacktestConfig)
        backtest_values.pop("start", None)
        backtest_values.pop("end", None)
        config = BacktestConfig(dataset.manifest.start, dataset.manifest.end, **backtest_values)
        conservative, stress = BacktestExperimentRunner(backtests).run_suite(feed, config, strategy_config, risk_limits)
        for result in (conservative, stress):
            print(f"{result.config.fill_model}: run={result.run_id} status={result.status.value} return={result.metrics['total_return']}")
        return 0
    if args.action == "validate":
        client = ResearchDataClient(args.lake_root, dataset_root=args.dataset_root, run_mode=RunMode.BACKTEST)
        selected_feeds = tuple(client.replay_snapshots(value) for value in (args.development, args.validation, args.test))
        selected = tuple(feed.dataset for feed in selected_feeds)
        values = json.loads(args.config.read_text()) if args.config else {}
        strategy_config = BullPutSpreadConfig(**_coerce_decimal_fields(values.get("strategy", {}), BullPutSpreadConfig))
        risk_limits = RiskLimits(**_coerce_decimal_fields(values.get("risk", {}), RiskLimits))
        bt_values = _coerce_decimal_fields(values.get("backtest", {}), BacktestConfig)
        bt_values.pop("start", None)
        bt_values.pop("end", None)
        config = BacktestConfig(selected[0].manifest.start, selected[0].manifest.end, **bt_values)
        directory = BacktestExperimentRunner(backtests).validate_splits(selected_feeds, config, strategy_config, risk_limits)
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
    dataset = ResearchDataClient(args.lake_root, run_mode=RunMode.BACKTEST).replay_snapshots(
        manifest["dataset_id"],
    ).dataset
    replayed = BacktestEngine(dataset, config, BullPutSpreadStrategy(strategy_config), risk_limits).run()
    replayed.metrics["dataset_hash"] = dataset.manifest.content_hash
    replayed.metrics["code_version"] = dataset.manifest.code_version
    from kairos.strategies.specs import bull_put_strategy_spec
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
    repository = ReferenceCatalogRepository(args.reference_catalog_path)
    if not repository.path.exists():
        raise SystemExit("catalog is missing; run 'kairos catalog sync' first")
    catalog = repository.load()
    now = datetime.now(timezone.utc)
    definitions = tuple(catalog.instruments.get(InstrumentId(value.strip()), now) for value in args.instruments.split(",") if value.strip())
    session = None
    if args.venue == "ibkr":
        if environment not in {Environment.PAPER, Environment.LIVE}:
            raise SystemExit("IBKR normalized capture requires paper or live environment")
        session = _ibkr_session(readonly=True)
        reference = IbkrReferenceDataClient(session)
        for definition in definitions:
            reference.bind_definition(definition, catalog)
        provider = IbkrMarketDataClient(session)
    else:
        if environment not in {Environment.TESTNET, Environment.LIVE}:
            raise SystemExit("Binance normalized capture requires testnet or live environment")
        spot_base = "https://testnet.binance.vision" if environment is Environment.TESTNET else "https://api.binance.com"
        futures_base = "https://testnet.binancefuture.com" if environment is Environment.TESTNET else "https://dapi.binance.com" if args.inverse else "https://fapi.binance.com"
        futures_path = "/dapi/v1/ticker/bookTicker" if args.inverse else "/fapi/v1/ticker/bookTicker"
        routes = {
            ProductType.CRYPTO_SPOT: BinanceMarketDataClient(UrllibBinanceTransport(spot_base)),
            ProductType.PERPETUAL: BinanceMarketDataClient(UrllibBinanceTransport(futures_base), ProductType.PERPETUAL, path=futures_path),
            ProductType.FUTURE: BinanceMarketDataClient(UrllibBinanceTransport(futures_base), ProductType.FUTURE, path=futures_path),
        }
        if environment is Environment.LIVE:
            routes[ProductType.CRYPTO_OPTION] = BinanceMarketDataClient(UrllibBinanceTransport("https://eapi.binance.com"), ProductType.CRYPTO_OPTION)
        provider = CompositeMarketDataClient(routes)
    series_spec = SeriesCaptureSpec(args.dataset_id, args.samples, args.interval_seconds, args.split)
    try:
        dataset = NormalizedSeriesCaptureService(MarketSnapshotStorageDriver(args.dataset_root)).capture(
            provider, catalog, definitions, series_spec, source=f"{args.venue}.normalized-series", market_data_type=environment.value,
        )
    finally:
        if session is not None:
            session.disconnect()
    print(f"Dataset: {dataset.manifest.dataset_id}")
    print(f"Products: {','.join(sorted({item.instrument_type.value for item in definitions}))}")
    print(f"Slices: {dataset.manifest.slice_count}")
    print(f"Hash: {dataset.manifest.content_hash}")
    return 0


def _catalog(args: argparse.Namespace) -> int:
    environment = Environment(args.environment)
    products = {item.strip() for item in args.products.split(",") if item.strip()}
    symbols = tuple(item.strip() for item in args.symbols.split(",") if item.strip())
    from kairos.reference import ReferenceCatalog
    from kairos.reference.repository import ReferenceCatalogRepository
    repository = ReferenceCatalogRepository(args.reference_catalog_path)
    catalog = repository.load() if repository.path.exists() else ReferenceCatalog()
    before = len(catalog.instruments.values())
    if args.venue == "ibkr":
        if environment not in {Environment.PAPER, Environment.LIVE}:
            raise SystemExit("IBKR catalog sync requires paper or live environment")
        session = _ibkr_session(readonly=True)
        reference_client = IbkrReferenceDataClient(session)
        try:
            if "equity" in products:
                catalog.merge(reference_client.sync(ReferenceDataRequest(ProductType.EQUITY, tuple(item for item in symbols if ":" not in item))))
            if "option" in products:
                catalog.merge(reference_client.sync(ReferenceDataRequest(ProductType.LISTED_OPTION, tuple(item for item in symbols if ":" in item))))
        finally:
            session.disconnect()
    else:
        if environment not in {Environment.TESTNET, Environment.LIVE}:
            raise SystemExit("Binance catalog sync requires testnet or live environment")
        if "spot" in products:
            transport = UrllibBinanceTransport("https://testnet.binance.vision" if environment is Environment.TESTNET else "https://api.binance.com")
            catalog.merge(BinanceSpotReferenceDataClient(transport).sync(ReferenceDataRequest(ProductType.CRYPTO_SPOT, symbols)))
        if "perpetual" in products:
            transport = UrllibBinanceTransport("https://testnet.binancefuture.com" if environment is Environment.TESTNET else "https://dapi.binance.com" if args.inverse else "https://fapi.binance.com")
            catalog.merge(BinanceFuturesReferenceDataClient(transport, inverse=args.inverse).sync(ReferenceDataRequest(ProductType.PERPETUAL, symbols)))
        if "future" in products:
            transport = UrllibBinanceTransport("https://testnet.binancefuture.com" if environment is Environment.TESTNET else "https://dapi.binance.com" if args.inverse else "https://fapi.binance.com")
            catalog.merge(BinanceFuturesReferenceDataClient(transport, inverse=args.inverse).sync(ReferenceDataRequest(ProductType.FUTURE, symbols)))
        if "option" in products:
            if environment is Environment.TESTNET:
                raise SystemExit("Binance options do not provide the same public testnet contract; use live public reference data only")
            catalog.merge(BinanceOptionsReferenceDataClient(UrllibBinanceTransport("https://eapi.binance.com")).sync(ReferenceDataRequest(ProductType.CRYPTO_OPTION, symbols)))
    repository.save(catalog)
    print(f"Reference Catalog: {repository.path}")
    print(f"Synced: {len(catalog.instruments.values()) - before} instruments from {args.venue} ({environment.value})")
    return 0


def _authoritative_runtime_store(args: argparse.Namespace):
    from kairos.orchestration.runtime_store import SQLiteRuntimeStore

    runtime_path = Path(args.runtime_db) if args.runtime_db else Path(args.event_log_path).parent / "runtime.sqlite3"
    store = SQLiteRuntimeStore(runtime_path)
    return store, runtime_path


def _account(args: argparse.Namespace) -> int:
    environment = Environment(args.environment)
    if args.venue == "binance" and args.product == "options" and environment is not Environment.LIVE:
        raise SystemExit("Binance options account is live-only; no equivalent options testnet is available")
    runtime_store, _ = _authoritative_runtime_store(args)
    ledger = runtime_store.load_ledger()
    catalog_repository = ReferenceCatalogRepository(args.reference_catalog_path)
    catalog = catalog_repository.load() if catalog_repository.path.exists() else ReferenceCatalog()
    account = _account_key(args.venue, args.account_id, args.product)
    account_gateway = _account_gateway(args.venue, environment, account, ledger, args.product, catalog, args.inverse)
    report = ReconciliationService(ledger, account_gateway).reconcile(account)
    print(f"Environment: {environment.value.upper()}")
    print(f"Account: {account.value}")
    print(f"Matched: {report.matched}")
    for difference in report.differences:
        print(f"{difference.kind} {difference.key}: local={difference.local} venue={difference.venue}")
    return 0 if report.matched else 2


def _runtime_l4_preflight(args: argparse.Namespace) -> dict[str, object]:
    import socket
    from kairos.strategies.deployment import StrategyDeploymentGate
    environment = Environment(args.environment)
    compatible_environment = (
        args.venue == "binance" and environment is Environment.TESTNET
        or args.venue == "ibkr" and environment is Environment.PAPER
    )
    strategy_id = {"covered-call": "covered-call-v1", "spot-perp-carry": "spot-perpetual-carry-v1"}.get(args.strategy, args.strategy)
    deployment = StrategyDeploymentGate(Path(args.lake_root) / "strategies").evaluate(
        strategy_id, environment, simulated_venue=False,
    )
    instrument_ready = False
    instrument_reason = "instrument catalog is missing"
    catalog_path = Path(args.reference_catalog_path)
    if catalog_path.exists():
        try:
            catalog = ReferenceCatalogRepository(catalog_path).load()
            definition = catalog.instruments.get(InstrumentId(args.instrument), datetime.now(timezone.utc))
            if not catalog.active_listings(definition.instrument_id, datetime.now(timezone.utc)):
                raise LookupError("no active listing")
            instrument_ready = True
            instrument_reason = "active Venue listing found"
        except (LookupError, ValueError) as error:
            instrument_reason = str(error)
    if args.venue == "binance":
        external_ready = bool(os.getenv("BINANCE_TESTNET_API_KEY") and os.getenv("BINANCE_TESTNET_API_SECRET"))
        external_reason = "testnet credentials present" if external_ready else "BINANCE_TESTNET_API_KEY/API_SECRET are missing"
    else:
        host = os.getenv("IBKR_HOST", "127.0.0.1")
        port = int(os.getenv("IBKR_PORT", "4001"))
        connection = socket.socket(); connection.settimeout(0.25)
        try:
            connection.connect((host, port)); external_ready = True
            external_reason = f"IBKR Paper Gateway reachable at {host}:{port}"
        except OSError:
            external_ready = False
            external_reason = f"IBKR Paper Gateway unreachable at {host}:{port}"
        finally:
            connection.close()
    checks = {
        "environment_compatible": compatible_environment,
        "external_connection_ready": external_ready,
        "strategy_paper_approved": deployment.allowed,
        "instrument_listing_ready": instrument_ready,
    }
    payload = {
        "schema_version": 1,
        "kind": "runtime_l4_preflight",
        "ready": all(checks.values()),
        "venue": args.venue,
        "environment": args.environment,
        "strategy": strategy_id,
        "instrument": args.instrument,
        "checks": checks,
        "reasons": {
            "external": external_reason,
            "strategy": deployment.reason,
            "instrument": instrument_reason,
        },
    }
    if getattr(args, "evidence_artifact", None):
        payload["artifact"] = str(_write_l4_preflight_artifact(args.evidence_artifact, payload))
    return payload


def _write_l4_preflight_artifact(target: str | Path, payload: dict[str, object]) -> Path:
    path = Path(target)
    material = {key: value for key, value in payload.items() if key not in {"artifact", "audit_hash"}}
    audit_hash = sha256(json.dumps(
        to_primitive(material), ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    artifact_payload = {**material, "audit_hash": audit_hash}
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and json.loads(path.read_text(encoding="utf-8")) != artifact_payload:
        raise ValueError("l4 preflight evidence artifact path already contains different content")
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(artifact_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        temporary.replace(path)
    return path


def _trade(args: argparse.Namespace) -> int:
    if not bool(getattr(args,"manual_order",False)):
        warnings.warn("trade run is a compatibility facade; use order submit for manual orders or run paper/live for strategies",
            DeprecationWarning,stacklevel=2)
    environment = Environment(args.environment)
    if environment is Environment.LIVE and not args.confirm_live:
        raise SystemExit("live trading requires --confirm-live")
    if args.venue == "ibkr" and environment is Environment.TESTNET:
        raise SystemExit("IBKR uses paper rather than testnet")
    if args.venue == "binance" and environment is Environment.PAPER:
        raise SystemExit("Binance uses testnet rather than paper")
    if args.venue == "binance" and args.product == "options" and environment is not Environment.LIVE:
        raise SystemExit("Binance options execution is live-only; no equivalent options testnet is available")
    manual_order=bool(getattr(args,"manual_order",False))
    from kairos.strategies.deployment import StrategyDeploymentGate
    if args.venue == "simulated" and not manual_order:
        from kairos.strategies.specs import register_builtin_strategies
        register_builtin_strategies(Path(args.lake_root) / "strategies")
    strategy_id="manual-operations-v1" if manual_order else {"covered-call":"covered-call-v1","spot-perp-carry":"spot-perpetual-carry-v1"}.get(args.strategy,args.strategy)
    if not manual_order:
        deployment=StrategyDeploymentGate(Path(args.lake_root)/"strategies").evaluate(strategy_id,environment,simulated_venue=args.venue=="simulated")
        if not deployment.allowed:raise SystemExit(f"strategy deployment rejected: {deployment.reason}")
        print(f"Strategy lifecycle: {deployment.lifecycle.value} ({deployment.strategy_directory})")
    else:
        print(f"Manual operations intent: actor={args.actor} reason={args.reason}")
    catalog_repository = ReferenceCatalogRepository(args.reference_catalog_path)
    if not catalog_repository.path.exists():
        raise SystemExit("catalog is missing; run 'kairos catalog sync' first")
    catalog = catalog_repository.load()
    definition = catalog.instruments.get(InstrumentId(args.instrument), datetime.now(timezone.utc))
    runtime_store, runtime_path = _authoritative_runtime_store(args)
    ledger = runtime_store.load_ledger()
    listings = catalog.active_listings(definition.instrument_id, datetime.now(timezone.utc))
    venue = listings[0].venue_id if args.venue == "simulated" else VenueId(args.venue)
    account = AccountKey(InstitutionId(args.venue), args.account_id, _account_type(args.product))
    if args.venue == "simulated":
        balances, positions = _local_state(ledger, account)
        execution_gateway = SimulatedExecutionAccountGateway(venue, account, balances, positions, environment)
        market_ready = True
    else:
        execution_gateway = _execution_account_gateway(args.venue, environment, args.product, definition, catalog, args.inverse)
        market_ready = args.market_data_ready
    reconciliation = ReconciliationService(ledger, execution_gateway, runtime_store=runtime_store)
    event_log = PersistentEventLog(args.event_log_path)
    from kairos.execution.ingestion import DurableExecutionIngestionService
    from kairos.execution.recovery import VenueOrderRecoveryService
    order_recovery = None
    if callable(getattr(execution_gateway, "recover_order", None)):
        order_recovery = VenueOrderRecoveryService(
            runtime_store,
            {account: execution_gateway},
            DurableExecutionIngestionService(
                LedgerService(ledger, catalog),
                runtime_store,
            ),
        )
    kill_switch = KillSwitch((execution_gateway,), runtime_store=runtime_store)
    from kairos.application import (
        ApplicationConfig, FunctionProbe, RuntimePaths, RuntimeRecoveryService, KairosApplication,
    )
    runtime_root = runtime_path.parent
    paths = RuntimePaths(runtime_root, Path(args.reference_catalog_path), Path(args.lake_root), runtime_path, runtime_root / "artifacts")
    application = KairosApplication(
        ApplicationConfig(environment, paths), runtime_store, runtime_id=f"cli-{uuid4()}", accounts=(account,),
        order_recovery=order_recovery,
        recovery=RuntimeRecoveryService(
            runtime_store,
            catalog,
            settlement_asset(catalog, definition, datetime.now(timezone.utc)),
            {account: execution_gateway},
            marks={definition.instrument_id: args.limit_price} if args.limit_price is not None else {},
        ),
        probes=(
            FunctionProbe("instrument_catalog", lambda: (True, f"loaded {definition.instrument_id}")),
            FunctionProbe("market_data", lambda: (market_ready, "ready" if market_ready else "not confirmed")),
            FunctionProbe("account", lambda: (execution_gateway.account_state(account).account == account, "account query passed")),
            FunctionProbe("reconciliation", lambda: (
                (report := reconciliation.reconcile(account)).matched,
                "matched" if report.matched else f"{len(report.differences)} differences",
            )),
        ),
    )
    coordinator = ExecutionCoordinator(
        ExecutionRouter(catalog, (execution_gateway,)), {account: reconciliation}, kill_switch, event_log,
        runtime_store=runtime_store, application=application,
    )
    print(f"Environment: {environment.value.upper()}")
    if args.soak_seconds < 0 or args.cycle_seconds <= 0:
        raise SystemExit("--soak-seconds cannot be negative and --cycle-seconds must be positive")
    supervisor = None
    soak_started = None
    if args.soak_seconds:
        from kairos.application import RecoveryBackgroundService, RuntimeSupervisor
        from kairos.orchestration.monitoring import OperationalMonitor
        background_services = [RecoveryBackgroundService(order_recovery)] if order_recovery is not None else []
        if args.venue == "ibkr" and order_recovery is not None:
            from kairos.connectors.ibkr.ingestion import IbkrDurableFillIngestion
            execution = getattr(execution_gateway, "execution", None)
            session = getattr(execution, "session", None)
            if session is not None:
                background_services = [IbkrDurableFillIngestion(session, order_recovery)]
        if args.venue == "binance" and args.product == "futures":
            from kairos.connectors.binance.funding_settlement import BinanceFundingSettlementClient
            from kairos.connectors.binance.funding_ingestion import BinanceDurableFundingBackfill
            from kairos.execution.ingestion import DurableAccountingIngestionService
            execution = getattr(execution_gateway, "execution", None)
            if execution is not None:
                symbols = getattr(execution, "instrument_symbols", {})
                funding_client = BinanceFundingSettlementClient(
                    execution.transport, execution.signer, environment,
                    inverse=bool(getattr(execution, "inverse", False)),
                    instrument_lookup={symbol: instrument for instrument, symbol in symbols.items()},
                )
                background_services.append(BinanceDurableFundingBackfill(
                    account, funding_client,
                    DurableAccountingIngestionService(LedgerService(ledger, catalog), runtime_store),
                ))
        supervisor = RuntimeSupervisor(
            application, {account: reconciliation}, kill_switch,
            OperationalMonitor(application.config.maximum_clock_skew_ms),
            background_services=tuple(background_services), activate=coordinator.activate,
        )
        soak_started = datetime.now(timezone.utc)
        supervisor.start()
    else:
        application.start()
    try:
        if supervisor is None:
            coordinator.activate()
            application.run()
        order_type = OrderType(args.order_type)
        if order_type is OrderType.LIMIT and args.limit_price is None:
            raise SystemExit("limit orders require --limit-price")
        correlation = str(uuid5(NAMESPACE_URL, f"cli:{strategy_id}:{args.instrument}:{datetime.now(timezone.utc).date()}"))
        if manual_order:
            event_log.append(f"manual-intent:{correlation}","manual_order_intent",{
                "actor":args.actor,"reason":args.reason,"strategy_id":strategy_id,
                "instrument_id":args.instrument,"side":args.side,"quantity":str(args.quantity),
                "environment":environment.value,"created_at":datetime.now(timezone.utc).isoformat(),
            })
        request = OrderRequest(
            f"internal-{correlation}", f"client-{correlation}", strategy_id, f"intent-{correlation}", correlation,
            account, definition.instrument_id, TradeSide(args.side), args.quantity,
            ExecutionInstructions(order_type, TimeInForce.DAY, args.limit_price, post_only=args.post_only, reduce_only=args.reduce_only),
        )
        ack = coordinator.submit(request, datetime.now(timezone.utc))
        print(f"Accepted: client={ack.client_order_id} venue_order={ack.venue_order_id} intent={ack.intent_id}")
        if supervisor is not None:
            supervisor.run_for(args.soak_seconds, interval_seconds=args.cycle_seconds)
        if args.kill_switch_drill:
            result = kill_switch.trigger((account,), "CLI drill")
            application.degrade("CLI kill-switch drill")
            print(f"Kill switch: cancelled={len(result.cancelled_orders)} failures={len(result.failures)} reduce_only={kill_switch.reduce_only}")
        if supervisor is not None:
            supervisor.stop()
            restart_passed = False
            if args.restart_drill:
                application.start()
                restart_passed = application.status.value == "ready"
                application.stop()
            from kairos.application import write_soak_artifact
            ended = datetime.now(timezone.utc)
            target = args.soak_artifact or (
                paths.artifacts / "soak" / f"{environment.value}-{account.account_id}-{int(soak_started.timestamp())}.json"
            )
            soak = write_soak_artifact(
                supervisor, target, started_at=soak_started, ended_at=ended,
                target_duration_seconds=args.soak_seconds, environment=environment.value,
                restart_drill_passed=restart_passed,
                kill_switch_drill_passed=args.kill_switch_drill and kill_switch.triggered,
            )
            print(json.dumps(soak, ensure_ascii=False, indent=2))
            return 0 if soak["passed"] else 2
        return 0
    finally:
        if supervisor is not None and supervisor.started:
            supervisor.stop()
        elif application.status.value != "stopped":
            application.stop()


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


def _account_gateway(venue: str, environment: Environment, account: AccountKey, ledger, product: str, catalog, inverse: bool):
    if venue == "simulated":
        balances, positions = _local_state(ledger, account)
        return SimulatedExecutionAccountGateway(VenueId("simulated"), account, balances, positions, environment)
    if venue == "ibkr":
        session = _ibkr_session(readonly=True)
        reference = IbkrReferenceDataClient(session)
        for definition in catalog.instruments.values(datetime.now(timezone.utc)):
            if definition.instrument_type.value in {"equity", "etf", "listed_option"}:
                reference.bind_definition(definition, catalog)
        return IbkrAccountGateway(session, environment)
    key, secret = _credentials(environment)
    if product == "options":
        lookup = {
            listing.trading_symbol: listing.instrument_id
            for listing in catalog.listings.values(datetime.now(timezone.utc)) if listing.venue_id == VenueId("binance")
        }
        return BinanceOptionsAccountGateway(
            UrllibBinanceTransport("https://eapi.binance.com"), BinanceSigner(key, secret),
            environment, instrument_lookup=lookup,
        )
    base = "https://testnet.binancefuture.com" if product == "futures" and environment is Environment.TESTNET else "https://dapi.binance.com" if product == "futures" and inverse else "https://fapi.binance.com" if product == "futures" else "https://testnet.binance.vision" if environment is Environment.TESTNET else "https://api.binance.com"
    lookup = {
        listing.trading_symbol: listing.instrument_id
        for listing in catalog.listings.values(datetime.now(timezone.utc)) if listing.venue_id == VenueId("binance")
    }
    return BinanceAccountGateway(UrllibBinanceTransport(base), BinanceSigner(key, secret), environment, futures=product == "futures", inverse=inverse, instrument_lookup=lookup)


def _execution_account_gateway(venue: str, environment: Environment, product: str, definition, catalog, inverse: bool):
    if venue == "ibkr":
        session = _ibkr_session(readonly=False)
        IbkrReferenceDataClient(session).bind_definition(definition, catalog)
        return _CombinedExecutionAccount(IbkrExecutionGateway(session, environment), IbkrAccountGateway(session, environment))
    key, secret = _credentials(environment)
    if product == "options":
        transport, signer = UrllibBinanceTransport("https://eapi.binance.com"), BinanceSigner(key, secret)
        lookup = {
            listing.trading_symbol: listing.instrument_id
            for listing in catalog.listings.values(datetime.now(timezone.utc)) if listing.venue_id == VenueId("binance")
        }
        symbol = next(item.trading_symbol for item in catalog.active_listings(definition.instrument_id, datetime.now(timezone.utc)) if item.venue_id == VenueId("binance"))
        return _CombinedExecutionAccount(
            BinanceOptionsExecutionGateway(transport, signer, environment, instrument_symbols={definition.instrument_id: symbol}),
            BinanceOptionsAccountGateway(transport, signer, environment, instrument_lookup=lookup),
        )
    base = "https://testnet.binancefuture.com" if product == "futures" and environment is Environment.TESTNET else "https://dapi.binance.com" if product == "futures" and inverse else "https://fapi.binance.com" if product == "futures" else "https://testnet.binance.vision" if environment is Environment.TESTNET else "https://api.binance.com"
    transport, signer = UrllibBinanceTransport(base), BinanceSigner(key, secret)
    symbol = next(item.trading_symbol for item in catalog.active_listings(definition.instrument_id, datetime.now(timezone.utc)) if item.venue_id == VenueId("binance"))
    execution = BinanceExecutionGateway(
        transport, signer, environment, futures=product == "futures", inverse=inverse,
        instrument_symbols={definition.instrument_id: symbol},
    )
    lookup = {
        listing.trading_symbol: listing.instrument_id
        for listing in catalog.listings.values(datetime.now(timezone.utc)) if listing.venue_id == VenueId("binance")
    }
    account = BinanceAccountGateway(transport, signer, environment, futures=product == "futures", inverse=inverse, instrument_lookup=lookup)
    return _CombinedExecutionAccount(execution, account)


class _CombinedExecutionAccount:
    def __init__(self, execution, account) -> None:
        self.execution, self.account = execution, account
        self.institution_id = execution.institution_id
        self.venue_id, self.environment, self.capabilities = execution.venue_id, execution.environment, execution.capabilities
    def place_order(self, request): return self.execution.place_order(request)
    def cancel_order(self, account, venue_order_id): return self.execution.cancel_order(account, venue_order_id)
    def open_orders(self, account): return self.execution.open_orders(account)
    def account_state(self, account): return self.account.account_state(account)
    def recover_order(self, account, request, venue_order_id=None):
        recovery = getattr(self.execution, "recover_order", None)
        if not callable(recovery):
            raise NotImplementedError(f"{self.venue_id} execution gateway does not support order recovery")
        return recovery(account, request, venue_order_id)


def _account_key(venue: str, account_id: str, product: str) -> AccountKey:
    return AccountKey(InstitutionId(venue), account_id, _account_type(product))


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


def _workspace_research_module(module_name: str):
    try:
        return importlib.import_module(module_name)
    except ImportError as error:
        raise SystemExit(
            f"{module_name} is a source-workspace study module and is not included in the pip package. "
            "Run this command from the Kairos source checkout, or migrate the study into your own project workspace."
        ) from error


if __name__ == "__main__":
    raise SystemExit(main())
