from __future__ import annotations

from kairospy.identity import InstitutionId

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from kairospy import __version__
from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT

from kairospy.portfolio.accounting.ledger import LedgerService
from kairospy.environment import Environment
from kairospy.execution.ports import OrderRequest
from kairospy.reference.ports import ReferenceDataRequest
from kairospy.integrations.connectors.market_data_router import CompositeMarketDataClient
from kairospy.integrations.connectors.binance.account_gateway import (
    BinanceAccountGateway,
    BinanceOptionsAccountGateway,
)
from kairospy.integrations.connectors.binance.execution_gateway import (
    BinanceExecutionGateway,
    BinanceOptionsExecutionGateway,
)
from kairospy.integrations.connectors.binance.market_data_client import BinanceMarketDataClient
from kairospy.integrations.connectors.binance.reference_data import (
    BinanceFuturesReferenceDataClient,
    BinanceOptionsReferenceDataClient,
    BinanceSpotReferenceDataClient,
)
from kairospy.integrations.connectors.binance.request_signing import BinanceSigner
from kairospy.integrations.connectors.binance.rest_transport import UrllibBinanceTransport
from kairospy.integrations.connectors.ibkr.account_gateway import IbkrAccountGateway
from kairospy.integrations.connectors.ibkr.execution_gateway import IbkrExecutionGateway
from kairospy.integrations.connectors.ibkr.market_data_client import IbkrMarketDataClient
from kairospy.integrations.connectors.ibkr.reference_data import IbkrReferenceDataClient
from kairospy.integrations.connectors.ibkr.option_chain_provider import IbkrSpxwOptionChainProvider
from kairospy.integrations.connectors.ibkr.session import IbkrSession
from kairospy.integrations.connectors.simulated import SimulatedExecutionAccountGateway
from kairospy.integrations.connectors.massive import MassiveClient, MassiveConfig, MassiveMarketSnapshotBuilder, MassiveEntitlementDiagnostics, MassiveEquityDailyOhlcvPipeline, MassiveEquityHourlyOhlcvPipeline, MassiveEquityIdentityResolver, MassiveFlatFileBatchDownloader, MassiveFlatFileClient, MassiveReferencePipeline, MassiveVendorArchiveClient, OptionCloseImpliedVolatilityPipeline, OptionDailyOhlcvPipeline, SpxwDailyOhlcvPipeline
from kairospy.reference import ReferenceCatalog, ReferenceCatalogRepository
from kairospy.reference.access import settlement_asset
from kairospy.execution.orders import OrderType
from kairospy.execution.events import TradeSide
from kairospy.identity import AccountRef, AccountType, AssetId, InstrumentId, VenueId
from kairospy.portfolio.ledger import LedgerBook
from kairospy.execution.orders import ExecutionInstructions, TimeInForce
from kairospy.reference.contracts import OptionRight, ProductType
from kairospy.execution.router import ExecutionRouter
from kairospy.runtime.coordinator import ExecutionCoordinator
from kairospy.runtime.store.event_log import PersistentEventLog
from kairospy.governance.kill_switch import KillSwitch
from kairospy.governance.reconciliation import ReconciliationService
from kairospy.research.capture.report import summarize
from kairospy.research.capture.option_capture import OptionCaptureService
from kairospy.research.capture.spec import MarketDataType, OptionChainCaptureSpec
from kairospy.infrastructure.storage.repository import FileOptionCaptureRepository
from kairospy.data.market_snapshot_storage import MarketSnapshotStorageDriver
from kairospy.infrastructure.storage.codec import restore_primitives, to_primitive
from kairospy.infrastructure.storage.data_lake import write_json
from kairospy.research.capture.series import SeriesCaptureProgress, SeriesCaptureService, SeriesCaptureSpec
from kairospy.research.capture.normalized_series import NormalizedSeriesCaptureService
from kairospy.analytics.pricing import PricingInput, PricingModel, OptionValuationService, implied_volatility, price_with_volatility
from kairospy.risk import RevaluationPosition, Scenario, ScenarioEngine, explain_scenario
from kairospy.data import (
    AcquisitionLimits, DataCatalog, DatasetKey, DatasetLayer, DataProductDefinition, DatasetQualityService, DatasetRelease,
    DatasetStatus, DatasetStorageKind, OutputFormat, QualityLevel, DatasetClient, RunMode,
)
from kairospy.data.bootstrap import default_provider_registry, register_configured_products, register_default_products
from kairospy.market.repository import ParquetMarketEventRepository
from kairospy.analytics.features import BtcIvRvFeatureBuilder, BtcTermSkewFeatureBuilder, BtcDeribitTradeSkewFeatureBuilder
from kairospy.analytics.features.us_equity_momentum import UsEquityMomentumDatasetBuilder, UsEquityMomentumPolicy


def _program_name() -> str:
    executable = Path(sys.argv[0]).name
    if executable in {"kairospy"}:
        return executable
    return "kairospy"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _hide_subcommands(action, names: set[str]) -> None:
    action._choices_actions[:] = [item for item in action._choices_actions if item.dest not in names]
    action.metavar = "{" + ",".join(name for name in action.choices if name not in names) + "}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=_program_name(), description="Multi-asset data, workspace, run, reconciliation, and execution toolkit")
    parser.add_argument("--data-root", default=f"{DEFAULT_LAKE_ROOT}/snapshots")
    parser.add_argument("--dataset-root", default=f"{DEFAULT_LAKE_ROOT}/curated")
    parser.add_argument("--backtest-root", default=f"{DEFAULT_LAKE_ROOT}/backtests")
    parser.add_argument("--catalog-path", default=f"{DEFAULT_LAKE_ROOT}/catalog/instruments.json")
    parser.add_argument("--reference-catalog-path", default=f"{DEFAULT_LAKE_ROOT}/reference/catalog.json")
    parser.add_argument("--event-log-path", default=f"{DEFAULT_LAKE_ROOT}/events/kairospy.jsonl")
    parser.add_argument("--runtime-db", help="transactional runtime database; defaults beside --event-log-path")
    parser.add_argument(
        "--lake-root",
        default=os.environ.get("KAIROSPY_LAKE_ROOT", DEFAULT_LAKE_ROOT),
        help=f"data lake root; defaults to KAIROSPY_LAKE_ROOT or {DEFAULT_LAKE_ROOT}",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text",
                        help="output format; human-readable text is the default")
    parser.add_argument("--lang", choices=("zh-CN", "en-US"), help="display language; defaults from the system locale")
    parser.add_argument("--quiet", action="store_true", help="suppress successful product command output")
    commands = parser.add_subparsers(dest="group", required=True)
    init = commands.add_parser("init", help="create a Kairos project in any local directory")
    init.add_argument("target_path", nargs="?", type=Path, help="project directory, e.g. kairospy init my-desk")
    init.add_argument("--target", type=Path, default=Path("."), help="project directory; defaults to the current directory")
    init.add_argument("--name", help="project name; defaults to the target directory name")
    init.add_argument("--force", action="store_true", help="overwrite existing scaffold files")
    init.add_argument("--interactive", action="store_true", help="prompt for project target, name and overwrite behavior")
    workspace = commands.add_parser("workspace", help="manage project workspaces and data bindings")
    workspace_actions = workspace.add_subparsers(dest="action", required=True)
    workspace_create = workspace_actions.add_parser("create", help="create or open one Workspace")
    workspace_create.add_argument("name")
    workspace_bind_data = workspace_actions.add_parser("bind-data", help="bind a Dataset to a workspace-local name")
    workspace_bind_data.add_argument("workspace")
    workspace_bind_data.add_argument("--name", required=True, help="workspace-local data name")
    workspace_bind_data.add_argument("--dataset", required=True, help="Dataset name, alias, or release id")
    workspace_bind_live = workspace_actions.add_parser("bind-live", help="bind a Live View to a workspace-local name")
    workspace_bind_live.add_argument("workspace")
    workspace_bind_live.add_argument("--name", required=True, help="workspace-local live data name")
    workspace_bind_live.add_argument("--dataset", required=True, help="Live View dataset name")
    workspace_inspect = workspace_actions.add_parser("inspect", help="inspect Workspace bindings")
    workspace_inspect.add_argument("name")
    data = commands.add_parser("data", help="prepare and inspect governed market datasets")
    data_actions = data.add_subparsers(dest="action", required=True)
    data_apply = data_actions.add_parser("apply", help="apply a Data manifest such as kairos.data.toml")
    data_apply.add_argument("manifest", nargs="?", type=Path, default=Path("kairos.data.toml"),
                            help="Data manifest path; defaults to kairos.data.toml")
    data_apply.add_argument("--only", help="only apply one manifest dataset name")
    data_apply.add_argument("--dry-run", action="store_true", help="show manifest actions without applying them")
    data_start = data_actions.add_parser("start", help="show Data onboarding or start one Dataset")
    data_start.add_argument("--dry-run", action="store_true", help="show the action without applying it")
    data_start.add_argument("--kind", choices=("file", "connector", "product", "live"),
                            help="what you have: file, connector, product, or live")
    data_start.add_argument("--file", type=Path, help="CSV/Parquet file path for --kind file")
    data_start.add_argument("--source", type=Path, help="connector path or live source key; also accepted for --kind file")
    data_start.add_argument("--product", help="built-in Data product key")
    data_start.add_argument("--name", help="Dataset name to expose")
    data_start.add_argument("--as", dest="as_dataset", help="Dataset name to expose")
    data_start.add_argument("--time", help="primary time field")
    data_start.add_argument("--start", dest="start_time", help="historical start timestamp")
    data_start.add_argument("--end", dest="end_time", help="historical end timestamp")
    data_start.add_argument("--account", help="account or credential reference for live data")
    data_start.add_argument("--instrument", action="append", default=[], help="instrument id or provider symbol; repeat as needed")
    data_start.add_argument("--channel", help="live channel name")
    data_start.add_argument("--market", choices=("spot", "usdm"), default="spot", help="market type for built-in Binance live sources")
    data_start.add_argument("--levels", type=int, choices=(5, 10, 20), help="order book depth levels for Binance orderbook")
    data_start.add_argument("--interval", choices=("100ms", "1000ms"), help="Binance orderbook update interval")
    data_start.add_argument("--for", dest="for_use", choices=("workspace", "backtest", "shadow", "paper", "live"),
                            help="target use; defaults to workspace for historical data and shadow for live data")
    data_add = data_actions.add_parser("add", help="add user-defined historical data as a named Dataset")
    data_add.add_argument("source", type=Path, help="CSV file or user connector path")
    data_add.add_argument("--name", required=True, help="Dataset name to expose")
    data_add.add_argument("--time", help="primary time field; inferred when omitted")
    data_add.add_argument("--protocol", choices=("historical",), help="treat source as a user HistoricalDataProtocol")
    data_add.add_argument("--start", help="optional protocol request start timestamp")
    data_add.add_argument("--end", help="optional protocol request end timestamp")
    data_add.add_argument("--instrument", action="append", default=[], help="optional protocol instrument; repeat for a bounded universe")
    data_use = data_actions.add_parser(
        "use",
        help="use a historical Data Product",
        description="Use a historical Data Product. Run 'kairospy data products list' to see built-in product keys, titles, capabilities and account requirements.",
    )
    data_use.add_argument("key", nargs="?", help="Data Product key or alias")
    data_use.add_argument("--as", dest="as_dataset", help="Dataset name to expose; defaults to the Data Product key")
    data_use.add_argument("--list-products", action="store_true", help="list built-in Data products and exit")
    data_use.add_argument("--start", help="inclusive ISO-8601 timestamp with timezone")
    data_use.add_argument("--end", help="exclusive ISO-8601 timestamp with timezone")
    data_use.add_argument("--provider")
    data_use.add_argument("--venue")
    data_use.add_argument("--instrument", action="append", default=[], help="instrument id or provider symbol; repeat for a bounded universe")
    data_use.add_argument("--for", dest="for_use", choices=("workspace", "backtest", "production"),
                          default="workspace", help="target use; defaults to workspace")
    data_use.add_argument("--refresh", action="store_true")
    data_use.add_argument("--dry-run", action="store_true", help="show the plan without downloading")
    data_connect = data_actions.add_parser("connect", help="connect a live Data source")
    data_connect.add_argument("source", type=Path, help="built-in live source key or user LiveDataProtocol connector path")
    data_connect.add_argument("--as", dest="as_dataset", required=True, help="Dataset name to expose")
    data_connect.add_argument("--protocol", choices=("live",), default="live")
    data_connect.add_argument("--time", default="timestamp", help="primary time field; defaults to timestamp")
    data_connect.add_argument("--account", help="account or credential reference for this live source")
    data_connect.add_argument("--instrument", action="append", default=[], help="instrument id or provider symbol; repeat for a bounded universe")
    data_connect.add_argument("--channel", help="live channel name")
    data_connect.add_argument("--market", choices=("spot", "usdm"), default="spot", help="market type for built-in Binance live sources")
    data_connect.add_argument("--levels", type=int, choices=(5, 10, 20), help="order book depth levels for Binance orderbook")
    data_connect.add_argument("--interval", choices=("100ms", "1000ms"), help="Binance orderbook update interval")
    data_connect.add_argument("--for", dest="for_use", choices=("shadow", "paper", "live"),
                              default="shadow", help="target use; defaults to shadow")
    data_connect.add_argument("--freshness-seconds", type=float, default=5.0, help="freshness max age required before paper/live use")
    data_sample = data_actions.add_parser("sample", help="sample rows from a live Data source")
    data_sample.add_argument("source", help="built-in live source key or user LiveDataProtocol connector path")
    data_sample.add_argument("--as", dest="as_dataset", help="temporary Dataset name for sample context")
    data_sample.add_argument("--instrument", action="append", default=[], help="instrument id or provider symbol; repeat as needed")
    data_sample.add_argument("--channel", help="live channel name")
    data_sample.add_argument("--market", choices=("spot", "usdm"), help="market type for built-in Binance live sources")
    data_sample.add_argument("--levels", type=int, choices=(5, 10, 20), help="order book depth levels for Binance orderbook")
    data_sample.add_argument("--interval", choices=("100ms", "1000ms"), help="Binance orderbook update interval")
    data_sample.add_argument("--limit", type=_positive_int, default=5, help="maximum rows to sample")
    data_reconnect = data_actions.add_parser("reconnect", help="reconnect a configured live Dataset")
    data_reconnect.add_argument("dataset", help="Dataset name to reconnect")
    data_reconnect.add_argument("--account", help="override account or credential reference")
    data_reconnect.add_argument("--instrument", action="append", default=[], help="override instruments; repeat for a bounded universe")
    data_reconnect.add_argument("--channel", help="override live channel name")
    data_reconnect.add_argument("--market", choices=("spot", "usdm"), help="override market type for built-in Binance live sources")
    data_reconnect.add_argument("--levels", type=int, choices=(5, 10, 20), help="override order book depth levels for Binance orderbook")
    data_reconnect.add_argument("--interval", choices=("100ms", "1000ms"), help="override Binance orderbook update interval")
    data_reconnect.add_argument("--freshness-seconds", type=float, help="override freshness max age")
    data_product = data_actions.add_parser(
        "products", aliases=("product",), help="list built-in Data products",
    )
    data_product_actions = data_product.add_subparsers(dest="product_action", required=True)
    data_product_actions.add_parser("list", help="list built-in Data products")
    data_product_doctor = data_product_actions.add_parser("doctor", help="diagnose one Data Product")
    data_product_doctor.add_argument("product", help="Data Product key or alias")
    data_protocol = data_actions.add_parser("protocol", help="create and check user Data protocols")
    data_protocol_actions = data_protocol.add_subparsers(dest="protocol_action", required=True)
    data_protocol_actions.add_parser("list", help="list supported user Data protocol types")
    protocol_template = data_protocol_actions.add_parser("template", help="print or write a user Data protocol template")
    protocol_template.add_argument("--kind", choices=("historical", "live"), required=True)
    protocol_template.add_argument("--output", type=Path, help="optional Python file to write")
    protocol_check = data_protocol_actions.add_parser("check", help="check a user Data protocol file")
    protocol_check.add_argument("source", type=Path, help="Python protocol file")
    protocol_check.add_argument("--kind", choices=("historical", "live"), required=True)
    protocol_check.add_argument("--name", default="workspace.protocol_check", help="temporary Dataset name for the check request")
    protocol_check.add_argument("--start", help="optional historical start timestamp")
    protocol_check.add_argument("--end", help="optional historical end timestamp")
    protocol_check.add_argument("--instrument", action="append", default=[], help="optional instrument; repeat as needed")
    protocol_check.add_argument("--account", help="optional live account reference for request shape validation")
    protocol_check.add_argument("--channel", help="optional live channel for request shape validation")
    data_download = data_actions.add_parser("download", help="download a registered Data Product by key")
    data_download.add_argument("key", help="registered Data Product key, for example tutorial-sma-data")
    data_register_download = data_actions.add_parser(
        "register-download", help="register a reusable Data Product download entry",
    )
    data_register_download.add_argument("--key", required=True, help="stable Data Product key")
    data_register_download.add_argument("--spec", type=Path, required=True, help="JSON or YAML Data Product download spec")
    data_register_provider = data_actions.add_parser(
        "register-provider", help="register a reusable Data Product provider",
    )
    data_register_provider.add_argument("--name", required=True, help="stable Data provider name")
    data_register_provider.add_argument("--spec", type=Path, required=True, help="JSON or YAML Data provider spec")
    data_write = data_actions.add_parser("write", help="write external data into the Data Contract")
    data_write.add_argument("--file", type=Path, help="CSV file to import as a historical time series")
    data_write.add_argument("--live", action="store_true", help="register a live data view instead of importing a file")
    data_write.add_argument("--connector", type=Path, help="live connector code file used with --live")
    data_write.add_argument("--as", dest="as_dataset", required=True, help="logical dataset identity to publish")
    data_write.add_argument("--contract", type=Path, required=True, help="JSON Data Contract")
    live_binance = data_actions.add_parser(
        "live-binance", help=argparse.SUPPRESS,
    )
    live_binance.add_argument("--symbol", required=True, help="Binance venue symbol, for example BTCUSDT")
    live_binance.add_argument("--channel", choices=("bookTicker", "trade", "aggTrade", "depth"),
                              default="bookTicker")
    live_binance.add_argument("--messages", type=int, default=10)
    live_binance.add_argument("--futures", action="store_true")
    live_binance.add_argument("--instrument", help="stable internal InstrumentId; defaults from symbol and product line")
    live_binance.add_argument("--journal", type=Path, help="raw JSONL capture path")
    soak_binance = data_actions.add_parser(
        "soak-binance", help=argparse.SUPPRESS,
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
    soak_binance.add_argument(
        "--live-view-manifest", type=Path,
        help="Live View manifest to mark healthy/unhealthy with audited soak channel diagnostics",
    )
    inspect_data = data_actions.add_parser("inspect", help="show schema, lineage and time coverage")
    inspect_data.add_argument("--dataset", required=True)
    list_data = data_actions.add_parser("list", help="list Datasets and readiness")
    list_data.add_argument("--dimension", action="append", default=[], help="key=value dimension; repeatable")
    releases_data = data_actions.add_parser("releases", help="list local dataset releases and versions")
    releases_data.add_argument("--dataset", help="logical dataset key, release id, or alias")
    releases_data.add_argument("--dimension", action="append", default=[], help="key=value dimension; repeatable")
    search_data = data_actions.add_parser("search", help="find Datasets by structured dimensions")
    search_data.add_argument("--dimension", action="append", default=[], help="key=value dimension; repeatable")
    describe_data = data_actions.add_parser("describe", help="show Dataset readiness, time and issues")
    describe_data.add_argument("dataset_arg", nargs="?", help="Dataset name to describe")
    describe_data.add_argument("--dataset", dest="dataset", help="Dataset name to describe; kept for compatibility")
    doctor_data = data_actions.add_parser("doctor", help="diagnose one Dataset readiness")
    doctor_data.add_argument("dataset_arg", nargs="?", help="Dataset name to diagnose")
    doctor_data.add_argument("--dataset", dest="dataset", help="Dataset name to diagnose; kept for compatibility")
    metadata_data = data_actions.add_parser("metadata", help="show inferred Dataset metadata without audit internals")
    metadata_data.add_argument("dataset_arg", nargs="?", help="Dataset name to inspect")
    metadata_data.add_argument("--dataset", dest="dataset", help="Dataset name to inspect; kept for compatibility")
    metadata_data.add_argument("--time", help="override the Dataset primary time field")
    diagnostics_data = data_actions.add_parser("diagnostics", help="audit all Catalog products and releases")
    diagnostics_data.add_argument("--strict", action="store_true", help="return non-zero when errors exist")
    us_equity_diagnostics = data_actions.add_parser("us-equity-momentum-diagnostics", help="audit the local US equity momentum data package")
    us_equity_diagnostics.add_argument("--workspace", default="us-equity-momentum")
    us_equity_diagnostics.add_argument("--version", default="1.0.0")
    us_equity_diagnostics.add_argument("--strict", action="store_true", help="return non-zero when diagnostics errors exist")
    validate_data = data_actions.add_parser("validate", help="validate a Dataset quality profile")
    validate_data.add_argument("dataset_arg", nargs="?", help="Dataset name to validate")
    validate_data.add_argument("--dataset", dest="dataset", help="Dataset name to validate; kept for compatibility")
    validate_data.add_argument("--release", help="release id to validate; kept for internal compatibility")
    prepare_data = data_actions.add_parser("prepare", help="plan, acquire, validate and optionally promote a product")
    prepare_data.add_argument("--dataset", required=True)
    prepare_data.add_argument("--start", required=True)
    prepare_data.add_argument("--end", required=True)
    prepare_data.add_argument("--quality", choices=tuple(item.value for item in QualityLevel), default=QualityLevel.WORKSPACE.value)
    prepare_data.add_argument("--provider")
    prepare_data.add_argument("--venue")
    prepare_data.add_argument("--acquire-missing", action="store_true")
    _add_acquisition_limit_args(prepare_data)
    prepare_data.add_argument("--promote", action="store_true", help="explicitly approve promotion after quality passes")
    prepare_data.add_argument("--actor", default="data-prepare")
    prepare_data.add_argument("--reason", default="explicit data preparation")
    prepare_us_equity_momentum = data_actions.add_parser(
        "prepare-us-equity-momentum",
        help="one-command bounded US equity momentum data, feature and diagnostics workflow",
    )
    prepare_us_equity_momentum.add_argument(
        "--raw-dataset", action="append", required=True,
        help="configured Massive raw equity OHLCV product; repeat for a bounded multi-stock basket",
    )
    prepare_us_equity_momentum.add_argument("--start", required=True, help="inclusive ISO-8601 timestamp with timezone")
    prepare_us_equity_momentum.add_argument("--end", required=True, help="exclusive ISO-8601 timestamp with timezone")
    prepare_us_equity_momentum.add_argument("--provider", default="massive")
    prepare_us_equity_momentum.add_argument("--venue", default="us-securities")
    prepare_us_equity_momentum.add_argument("--dataset-id", default="us-equity-momentum.bounded.v1")
    prepare_us_equity_momentum.add_argument("--workspace", default="us-equity-momentum")
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
    query_data = data_actions.add_parser("query", help="query rows from a Dataset")
    query_data.add_argument("dataset_arg", nargs="?", help="Dataset name to query")
    query_data.add_argument("--dataset", dest="dataset", help="Dataset name to query; kept for compatibility")
    query_data.add_argument("--start")
    query_data.add_argument("--end")
    query_data.add_argument("--field", action="append", default=[])
    query_data.add_argument("--limit", type=int, default=100)
    replay_data = data_actions.add_parser(
        "replay",
        help="print replay rows from a Dataset",
        description="Replay a Dataset and print each returned row to the terminal in replay order.",
    )
    replay_data.add_argument("dataset_arg", nargs="?", help="Dataset name to replay")
    replay_data.add_argument("--dataset", dest="dataset", help="Dataset name to replay; kept for compatibility")
    replay_data.add_argument("--start")
    replay_data.add_argument("--end")
    replay_data.add_argument("--field", action="append", default=[])
    replay_data.add_argument("--instrument", action="append", default=[], help="instrument id or provider symbol; repeat as needed")
    replay_data.add_argument("--limit", type=_positive_int, default=20)
    freeze_data = data_actions.add_parser("freeze", help="freeze one or more dataset inputs for a workspace")
    freeze_data.add_argument("--workspace", required=True)
    freeze_data.add_argument("--dataset", action="append", required=True)
    freeze_data.add_argument("--output", type=Path, required=True)
    freeze_data.add_argument("--code-version", default=__version__)
    compare_data = data_actions.add_parser("compare", help="compare two immutable dataset releases")
    compare_data.add_argument("--first", required=True)
    compare_data.add_argument("--second", required=True)
    audit_artifact = data_actions.add_parser("audit-artifact", help="verify an artifact consumes frozen Q3/Q4 releases")
    audit_artifact.add_argument("--artifact", type=Path, required=True)
    audit_data = data_actions.add_parser("audit", help="show Dataset audit evidence")
    audit_data.add_argument("dataset", help="Dataset name to audit")
    audit_data.add_argument("--verbose", action="store_true", help="include internal manifest, hash and path evidence")
    alias_data = data_actions.add_parser("alias", help="promote an audited floating alias to an approved release")
    alias_data.add_argument("--alias", required=True)
    alias_data.add_argument("--release", required=True)
    alias_data.add_argument("--actor", required=True)
    alias_data.add_argument("--reason", required=True)
    alias_data.add_argument("--quality-report-hash", required=True)
    catalog_data = data_actions.add_parser("catalog", help="list governed logical datasets, versions, aliases and formats")
    catalog_data.add_argument("--refresh", action="store_true", help="discover and persist existing governed datasets")
    copy_data = data_actions.add_parser("copy", help="copy a governed dataset release between local data lake roots")
    copy_data.add_argument("--from", dest="source_root", required=True, type=Path,
                           help="source data lake root, for example /path/to/project/.kairos/data")
    copy_data.add_argument("--to", dest="target_root", type=Path,
                           help="target data lake root; defaults to --lake-root")
    copy_data.add_argument("--dataset", required=True, help="logical dataset key to copy")
    copy_data.add_argument("--release", help="specific release id or alias; defaults to the source catalog selected release")
    copy_data.add_argument("--include-source-cache", action="store_true",
                           help="also copy source/provider=<provider> raw provider cache, such as Binance payload.zip files")
    copy_data.add_argument("--overwrite", action="store_true", help="overwrite existing copied files")
    copy_data.add_argument("--dry-run", action="store_true", help="show what would be copied without writing files")
    for action, help_text in (("plan", "show local coverage and missing-data acquisition plan"),
                              ("acquire", "acquire missing data and publish an immutable release")):
        command = data_actions.add_parser(action, help=help_text)
        command.add_argument("--dataset", required=action == "plan")
        command.add_argument("--start", required=action == "plan", help="inclusive ISO-8601 timestamp with timezone")
        command.add_argument("--end", required=action == "plan", help="exclusive ISO-8601 timestamp with timezone")
        command.add_argument("--provider")
        command.add_argument("--venue")
        command.add_argument("--instrument", action="append", default=[], help="instrument id or provider symbol; repeat for a bounded universe")
        _add_acquisition_limit_args(command)
        if action == "acquire":
            command.add_argument("--refresh", action="store_true")
            command.add_argument("--yes", action="store_true", help="skip confirmation after showing the acquisition plan")
            command.add_argument("--dry-run", action="store_true", help="show the plan without downloading")
            command.add_argument("--list-products", action="store_true", help="list acquirable data products and exit")
    promote_data = data_actions.add_parser("promote", help="promote a Dataset for a higher use")
    promote_data.add_argument("dataset", nargs="?", help="Dataset name for user-facing promotion")
    promote_data.add_argument("--for", dest="for_use", choices=("workspace", "backtest", "production"),
                              help="target use for Dataset promotion")
    promote_data.add_argument("--actor")
    promote_data.add_argument("--reason")
    provider_fetch = data_actions.add_parser("provider-fetch", help=argparse.SUPPRESS)
    provider_fetch.add_argument("--provider", choices=("massive",), default="massive")
    provider_fetch.add_argument("--resource", choices=("option-contracts", "option-quotes", "option-trades", "aggregates", "option-chain"), required=True)
    provider_fetch.add_argument("--ticker", help="option ticker for quote/trade or underlying ticker for aggregates")
    provider_fetch.add_argument("--underlying", help="underlying ticker for contracts or current option-chain snapshot")
    provider_fetch.add_argument("--start", help="inclusive start date/timestamp")
    provider_fetch.add_argument("--end", help="exclusive end date/timestamp")
    provider_fetch.add_argument("--limit", type=int, default=50000)
    provider_fetch.add_argument("--max-pages", type=int, default=100000, help="fail closed if pagination exceeds this bound")
    provider_fetch.add_argument("--multiplier", type=int, default=1)
    provider_fetch.add_argument("--timespan", default="minute")
    provider_flat = data_actions.add_parser("provider-flat-file", help=argparse.SUPPRESS)
    provider_flat.add_argument("--provider", choices=("massive",), default="massive")
    provider_flat.add_argument("--operation", choices=("usage", "status", "download"), required=True)
    provider_flat.add_argument("--key", help="Flat File key for status/download")
    provider_flat_batch = data_actions.add_parser("provider-flat-file-batch", help=argparse.SUPPRESS)
    provider_flat_batch.add_argument("--provider", choices=("massive",), default="massive")
    provider_flat_batch.add_argument("--start", required=True, help="inclusive trading date YYYY-MM-DD")
    provider_flat_batch.add_argument("--end", required=True, help="exclusive date YYYY-MM-DD")
    provider_flat_batch.add_argument("--max-files", type=int, default=5, help="maximum non-local files to inspect/download in this run")
    provider_flat_batch.add_argument("--dry-run", action="store_true", help="only inspect cache status and write a plan")
    prepare_spxw_daily_ohlcv = data_actions.add_parser("prepare-spxw-daily-ohlcv", help="inventory and convert downloaded OPRA daily OHLCV into governed SPXW Parquet; compatibility alias for prepare-spxw-daily-ohlcv")
    prepare_spxw_daily_ohlcv.add_argument("--dataset-id", required=True)
    prepare_spxw_daily_ohlcv.add_argument("--start", required=True, help="inclusive date YYYY-MM-DD")
    prepare_spxw_daily_ohlcv.add_argument("--end", required=True, help="exclusive date YYYY-MM-DD")
    prepare_spxw_day_aggs = data_actions.add_parser(
        "prepare-spxw-day-aggs",
        help="compatibility alias for prepare-spxw-daily-ohlcv",
        description="compatibility alias for prepare-spxw-daily-ohlcv",
    )
    prepare_spxw_day_aggs.add_argument("--dataset-id", required=True)
    prepare_spxw_day_aggs.add_argument("--start", required=True, help="inclusive date YYYY-MM-DD")
    prepare_spxw_day_aggs.add_argument("--end", required=True, help="exclusive date YYYY-MM-DD")
    prepare_option_daily_ohlcv = data_actions.add_parser("prepare-option-daily-ohlcv", help="convert downloaded OPRA daily OHLCV for one OCC root; compatibility alias for prepare-option-daily-ohlcv")
    prepare_option_daily_ohlcv.add_argument("--dataset-id", required=True)
    prepare_option_daily_ohlcv.add_argument("--option-root", required=True, help="OCC root without O: prefix, for example NVDA")
    prepare_option_daily_ohlcv.add_argument("--start", required=True)
    prepare_option_daily_ohlcv.add_argument("--end", required=True)
    prepare_option_day_aggs = data_actions.add_parser(
        "prepare-option-day-aggs",
        help="compatibility alias for prepare-option-daily-ohlcv",
        description="compatibility alias for prepare-option-daily-ohlcv",
    )
    prepare_option_day_aggs.add_argument("--dataset-id", required=True)
    prepare_option_day_aggs.add_argument("--option-root", required=True, help="OCC root without O: prefix, for example NVDA")
    prepare_option_day_aggs.add_argument("--start", required=True)
    prepare_option_day_aggs.add_argument("--end", required=True)
    prepare_equity_daily_ohlcv = data_actions.add_parser("prepare-equity-daily-ohlcv", help="archive and convert provider equity daily OHLCV; compatibility alias for prepare-equity-daily-ohlcv")
    prepare_equity_daily_ohlcv.add_argument("--provider", choices=("massive",), default="massive")
    prepare_equity_daily_ohlcv.add_argument("--dataset-id", required=True)
    prepare_equity_daily_ohlcv.add_argument("--ticker", required=True)
    prepare_equity_daily_ohlcv.add_argument("--start", required=True)
    prepare_equity_daily_ohlcv.add_argument("--end", required=True)
    prepare_equity_daily_ohlcv.add_argument("--view", choices=("raw", "vendor_adjusted"), default="vendor_adjusted")
    prepare_equity_day_aggs = data_actions.add_parser(
        "prepare-equity-day-aggs",
        help="compatibility alias for prepare-equity-daily-ohlcv",
        description="compatibility alias for prepare-equity-daily-ohlcv",
    )
    prepare_equity_day_aggs.add_argument("--provider", choices=("massive",), default="massive")
    prepare_equity_day_aggs.add_argument("--dataset-id", required=True)
    prepare_equity_day_aggs.add_argument("--ticker", required=True)
    prepare_equity_day_aggs.add_argument("--start", required=True)
    prepare_equity_day_aggs.add_argument("--end", required=True)
    prepare_equity_day_aggs.add_argument("--view", choices=("raw", "vendor_adjusted"), default="vendor_adjusted")
    prepare_equity_hourly_ohlcv = data_actions.add_parser("prepare-equity-hourly-ohlcv", help="archive and convert provider equity hourly OHLCV")
    prepare_equity_hourly_ohlcv.add_argument("--provider", choices=("massive",), default="massive")
    prepare_equity_hourly_ohlcv.add_argument("--dataset-id", required=True)
    prepare_equity_hourly_ohlcv.add_argument("--ticker", required=True)
    prepare_equity_hourly_ohlcv.add_argument("--start", required=True)
    prepare_equity_hourly_ohlcv.add_argument("--end", required=True)
    prepare_equity_hourly_ohlcv.add_argument("--view", choices=("raw", "vendor_adjusted"), default="vendor_adjusted")
    prepare_equity_hour_aggs = data_actions.add_parser("prepare-equity-hour-aggs", help="compatibility alias for prepare-equity-hourly-ohlcv")
    prepare_equity_hour_aggs.add_argument("--provider", choices=("massive",), default="massive")
    prepare_equity_hour_aggs.add_argument("--dataset-id", required=True)
    prepare_equity_hour_aggs.add_argument("--ticker", required=True)
    prepare_equity_hour_aggs.add_argument("--start", required=True)
    prepare_equity_hour_aggs.add_argument("--end", required=True)
    prepare_equity_hour_aggs.add_argument("--view", choices=("raw", "vendor_adjusted"), default="vendor_adjusted")
    prepare_option_close_iv = data_actions.add_parser("prepare-option-close-implied-volatility", help="materialize close-based implied volatility for an option daily OHLCV dataset")
    prepare_option_close_iv.add_argument("--dataset-id", required=True)
    prepare_option_close_iv.add_argument("--option-dataset", required=True)
    prepare_option_close_iv.add_argument("--equity-dataset", required=True)
    prepare_option_close_iv.add_argument("--risk-free-rate", type=Decimal, default=Decimal("0.04"))
    prepare_option_close_iv.add_argument("--dividend-yield", type=Decimal, default=Decimal("0.0003"))
    compact_massive = data_actions.add_parser("compact-market-events", help="explicitly compact immutable Parquet event partitions")
    compact_massive.add_argument("--dataset", required=True)
    provider_entitlement = data_actions.add_parser("provider-entitlement-diagnostics", help=argparse.SUPPRESS)
    provider_entitlement.add_argument("--provider", choices=("massive",), default="massive")
    provider_entitlement.add_argument("--underlying", required=True)
    provider_entitlement.add_argument("--option-ticker", required=True)
    provider_entitlement.add_argument("--date", required=True)
    provider_slices = data_actions.add_parser("build-provider-slices", help=argparse.SUPPRESS)
    provider_slices.add_argument("--provider", choices=("massive",), default="massive")
    provider_slices.add_argument("--source-dataset", required=True)
    provider_slices.add_argument("--output-dataset", required=True)
    provider_slices.add_argument("--start", required=True)
    provider_slices.add_argument("--end", required=True)
    provider_slices.add_argument("--sampling-seconds", type=int, default=60)
    provider_slices.add_argument("--max-quote-age-seconds", type=int, default=300)
    provider_slices.add_argument("--risk-free-rate", type=Decimal, default=Decimal("0"), help="continuously compounded annual rate used for put-call parity")
    provider_slices.add_argument("--split", choices=("development", "validation", "test"), default="development")
    sync_provider_reference = data_actions.add_parser("sync-provider-reference", help=argparse.SUPPRESS)
    sync_provider_reference.add_argument("--provider", choices=("massive",), default="massive")
    sync_provider_reference.add_argument("--equity-tickers", action="store_true", help="sync active and inactive US common stock ticker reference")
    sync_provider_reference.add_argument("--active-only", action="store_true", help="only sync currently active equity tickers")
    sync_provider_reference.add_argument("--ticker")
    sync_provider_reference.add_argument("--start")
    sync_provider_reference.add_argument("--end")
    build_equity_identity = data_actions.add_parser("build-provider-equity-identity", help=argparse.SUPPRESS)
    build_equity_identity.add_argument("--provider", choices=("massive",), default="massive")
    build_equity_identity.add_argument("--reference-rows", type=Path, required=True)
    build_equity_identity.add_argument("--ticker-events", type=Path)
    quarantine_provider_cache = data_actions.add_parser("quarantine-insecure-provider-cache", help=argparse.SUPPRESS)
    quarantine_provider_cache.add_argument("--provider", choices=("massive",), default="massive")
    _hide_subcommands(data_actions, {
        "download",
        "register-download",
        "register-provider",
        "write",
        "live-binance",
        "soak-binance",
        "inspect",
        "releases",
        "search",
        "diagnostics",
        "us-equity-momentum-diagnostics",
        "prepare",
        "prepare-us-equity-momentum",
        "freeze",
        "compare",
        "audit-artifact",
        "alias",
        "catalog",
        "copy",
        "plan",
        "acquire",
        "provider-fetch",
        "provider-flat-file",
        "provider-flat-file-batch",
        "provider-entitlement-diagnostics",
        "build-provider-slices",
        "sync-provider-reference",
        "build-provider-equity-identity",
        "quarantine-insecure-provider-cache",
        "prepare-spxw-daily-ohlcv",
        "prepare-spxw-day-aggs",
        "prepare-option-daily-ohlcv",
        "prepare-option-day-aggs",
        "prepare-equity-daily-ohlcv",
        "prepare-equity-day-aggs",
        "prepare-equity-hourly-ohlcv",
        "prepare-equity-hour-aggs",
        "prepare-option-close-implied-volatility",
        "compact-market-events",
    })
    providers = commands.add_parser("providers", help="inspect Providers and Data Product readiness")
    providers_actions = providers.add_subparsers(dest="action", required=True)
    providers_list = providers_actions.add_parser("list", help="list known Providers")
    providers_doctor = providers_actions.add_parser("doctor", help="diagnose one Provider")
    providers_doctor.add_argument("provider", help="Provider name, for example massive or binance")
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
    project = commands.add_parser("project", help="inspect the local Kairos project")
    project_actions = project.add_subparsers(dest="action", required=True)
    project_actions.add_parser("status", help="show project root, configuration and operational gates")
    config = commands.add_parser("config", help="inspect and edit the local Kairos project configuration")
    config_actions = config.add_subparsers(dest="action", required=True)
    config_actions.add_parser("path", help="print the discovered kairos.toml path")
    config_show = config_actions.add_parser("show", help="print the current configuration with secrets redacted")
    config_show.add_argument("--raw", action="store_true", help="include raw values without redaction")
    config_validate = config_actions.add_parser("validate", help="validate the discovered configuration")
    config_validate.add_argument("--strict", action="store_true", help="return non-zero when warnings exist")
    config_set = config_actions.add_parser("set", help="set a TOML value by dotted path")
    config_set.add_argument("path", help="dotted TOML path, for example credentials.massive_marketdata_primary.api_key")
    config_set.add_argument("value", help="scalar value; use env:VARIABLE_NAME for credentials")
    config_unset = config_actions.add_parser("unset", help="remove a TOML value by dotted path")
    config_unset.add_argument("path", help="dotted TOML path to remove")
    doctor = commands.add_parser("doctor", help="diagnose the local Kairos project setup")
    doctor.add_argument("--strict", action="store_true", help="return non-zero when warnings exist")
    configure = commands.add_parser("configure", help="configure a provider in the local Kairos project")
    configure.add_argument("--interactive", action="store_true", help="prompt for provider and credential environment variables")
    configure_actions = configure.add_subparsers(dest="provider", required=False)
    configure_massive = configure_actions.add_parser("massive", help="configure Massive credentials")
    configure_massive.add_argument("--api-key-env", default="KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY", help="environment variable containing the Massive API key")
    configure_binance = configure_actions.add_parser("binance", help="configure Binance credentials")
    configure_binance.add_argument("--environment", choices=("testnet", "live"), default="testnet")
    configure_binance.add_argument("--api-key-env", help="environment variable containing the Binance API key")
    configure_binance.add_argument("--api-secret-env", help="environment variable containing the Binance API secret")
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
    account = commands.add_parser("account", help="reconcile Ledger balances and positions with a venue")
    account_actions = account.add_subparsers(dest="action", required=True)
    reconcile = account_actions.add_parser("reconcile")
    reconcile.add_argument("--venue", choices=("ibkr", "binance", "simulated"), required=True)
    reconcile.add_argument("--environment", choices=("paper", "testnet", "live"), required=True)
    reconcile.add_argument("--account-id", default="default")
    reconcile.add_argument("--product", choices=("securities", "spot", "futures", "options"), default="spot")
    reconcile.add_argument("--inverse", action="store_true")
    accounts = commands.add_parser("accounts", help="inspect configured trading AccountBindings")
    accounts_actions = accounts.add_subparsers(dest="action", required=True)
    accounts_doctor = accounts_actions.add_parser("doctor", help="diagnose one configured AccountBinding")
    accounts_doctor.add_argument("account", help="AccountBinding name from kairos.toml, for example binance_live_spot")
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
    runtime_failure_policy = runtime_actions.add_parser(
        "failure-policy", help="run deterministic L3 crash-window and restart acceptance drills",
    )
    runtime_failure_policy.add_argument("--root", type=Path, required=True, help="isolated output root for drill state and audit artifacts")
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
    runtime_soak = runtime_actions.add_parser("soak", help="run an externally gated runtime soak and write promotion evidence")
    runtime_soak.add_argument("--strategy", choices=("covered-call", "spot-perp-carry"), required=True)
    runtime_soak.add_argument("--venue", choices=("ibkr", "binance", "simulated"), required=True)
    runtime_soak.add_argument("--environment", choices=("paper", "testnet", "live"), required=True)
    runtime_soak.add_argument("--confirm-live", action="store_true")
    runtime_soak.add_argument("--account-id", default="default")
    runtime_soak.add_argument("--product", choices=("securities", "spot", "futures", "options"), default="spot")
    runtime_soak.add_argument("--instrument", required=True)
    runtime_soak.add_argument("--side", choices=("buy", "sell"), required=True)
    runtime_soak.add_argument("--quantity", type=Decimal, required=True)
    runtime_soak.add_argument("--order-type", choices=("market", "limit"), default="limit")
    runtime_soak.add_argument("--limit-price", type=Decimal)
    runtime_soak.add_argument("--reduce-only", action="store_true")
    runtime_soak.add_argument("--post-only", action="store_true")
    runtime_soak.add_argument("--market-data-ready", action="store_true", help="explicit operational readiness acknowledgement for non-simulated venues")
    runtime_soak.add_argument("--kill-switch-drill", action="store_true")
    runtime_soak.add_argument("--soak-seconds", type=int, default=0, help="run the supervised runtime for this many wall-clock seconds")
    runtime_soak.add_argument("--cycle-seconds", type=float, default=5.0, help="supervisor heartbeat/reconciliation interval")
    runtime_soak.add_argument("--restart-drill", action="store_true", help="restart and recover the Application after the soak")
    runtime_soak.add_argument("--soak-artifact", type=Path, help="explicit L4 soak manifest path")
    runtime_soak.add_argument("--inverse", action="store_true")
    runtime_soak.set_defaults(manual_order=False)

    run_product = commands.add_parser("run", help="run strategy code from a Workspace across backtest, simulation, paper or live")
    run_actions = run_product.add_subparsers(dest="action", required=True)
    run_start = run_actions.add_parser("start", help="start a reusable RunConfig")
    run_start.add_argument("--config", required=True, type=Path, help="RunConfig TOML path, for example configs/runs/backtest.toml")
    run_start.add_argument("--param", action="append", default=[], help="strategy parameter key=value; repeatable")
    run_start.add_argument("--confirm-live", action="store_true", help="required explicit confirmation for live runs")
    run_start.add_argument(
        "--supervise-live-services",
        action="store_true",
        help="for live runs, start configured live market services under the runtime supervisor",
    )
    run_config = run_actions.add_parser("config", help="validate or explain reusable RunConfig files")
    run_config_actions = run_config.add_subparsers(dest="config_action", required=True)
    run_config_validate = run_config_actions.add_parser("validate", help="validate a RunConfig TOML file")
    run_config_validate.add_argument("path", type=Path)
    run_config_validate.add_argument("--strict", action="store_true")
    run_config_explain = run_config_actions.add_parser("explain", help="show the resolved RunConfig launch intent")
    run_config_explain.add_argument("path", type=Path)
    run_live = run_actions.add_parser("live", help="operate a named long-lived live run")
    run_live.add_argument(
        "live_action",
        choices=("start", "recover", "status", "stop", "kill-switch", "reset-kill-switch", "reload-risk-limits"),
        nargs="?",
        default="status",
    )
    run_live.add_argument("--run-id", required=True, help="unique run instance id")
    run_live.add_argument("--config", type=Path, help="RunConfig TOML path for start/recover")
    run_live.add_argument("--param", action="append", default=[], help="strategy parameter key=value; repeatable")
    run_live.add_argument("--confirm-live", action="store_true", help="required explicit confirmation for live start/recover")
    run_live.add_argument("--duration-seconds", type=float, help="bounded foreground run duration; omit to run until stop/fault")
    run_live.add_argument("--poll-seconds", type=float, default=0.25, help="foreground daemon stop-request poll interval")
    run_live.add_argument("--stale-after-seconds", type=float, default=5.0, help="heartbeat age before live status is stale")
    run_live.add_argument("--reason", help="operator reason for stop requests")
    run_live.add_argument("--actor", default="cli", help="operator actor for control commands")
    run_live.add_argument("--reconciliation-evidence", help="required evidence reference for reset-kill-switch")
    run_live.add_argument("--risk-limits-hash", help="limits hash for reload-risk-limits")
    run_inspect = run_actions.add_parser("inspect"); run_inspect.add_argument("--db", type=Path)
    run_inspect.add_argument("--artifact",type=Path);run_inspect.add_argument("--at")
    run_inspect.add_argument("--run-id")
    target_run_replay = run_actions.add_parser("replay", help="replay a Run workspace from its snapshot")
    target_run_replay.add_argument("--run-id", required=True)
    target_run_compare = run_actions.add_parser("compare", help="compare two Run workspaces")
    target_run_compare.add_argument("--first", required=True)
    target_run_compare.add_argument("--second", required=True)
    return parser


def _add_global_cli_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-root", default=f"{DEFAULT_LAKE_ROOT}/snapshots")
    parser.add_argument("--dataset-root", default=f"{DEFAULT_LAKE_ROOT}/curated")
    parser.add_argument("--backtest-root", default=f"{DEFAULT_LAKE_ROOT}/backtests")
    parser.add_argument("--catalog-path", default=f"{DEFAULT_LAKE_ROOT}/catalog/instruments.json")
    parser.add_argument("--reference-catalog-path", default=f"{DEFAULT_LAKE_ROOT}/reference/catalog.json")
    parser.add_argument("--event-log-path", default=f"{DEFAULT_LAKE_ROOT}/events/kairospy.jsonl")
    parser.add_argument("--runtime-db", type=Path, help="transactional runtime database; defaults beside --event-log-path")
    parser.add_argument("--lake-root", default=os.environ.get("KAIROSPY_LAKE_ROOT", DEFAULT_LAKE_ROOT),
                        help="data lake root; defaults to KAIROSPY_LAKE_ROOT or .kairos/data")
    parser.add_argument("--format", choices=("text", "json"), default="text",
                        help="output format; human-readable text is the default")
    parser.add_argument("--lang", choices=("zh-CN", "en-US"), default=None,
                        help="display language; defaults from the system locale")
    parser.add_argument("--quiet", action="store_true", help="suppress successful product command output")


def _add_sma_input_arguments(parser):
    parser.add_argument("--dataset"); parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--start"); parser.add_argument("--end")


def _add_acquisition_limit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-requests", type=int, default=10_000, help="maximum provider requests allowed for this acquisition")
    parser.add_argument("--max-instruments", type=int, default=10_000, help="maximum instruments allowed for this acquisition")
    parser.add_argument("--max-bytes", type=int, help="maximum estimated bytes allowed for this acquisition")


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


def _has_cli_option(raw_argv: list[str], name: str) -> bool:
    return any(item == name or item.startswith(name + "=") for item in raw_argv)


def _apply_project_config_defaults(args: argparse.Namespace, raw_argv: list[str]) -> None:
    from kairospy.infrastructure.configuration import load_project_config_or_none

    config = load_project_config_or_none()
    if config is None:
        return
    setattr(args, "_kairospy_project_config", config)
    lake_root = config.relative_path("paths.lake_root", DEFAULT_LAKE_ROOT)
    defaults = {
        "--lake-root": ("lake_root", "paths.lake_root", DEFAULT_LAKE_ROOT),
        "--dataset-root": ("dataset_root", "paths.dataset_root", str(lake_root / "curated")),
        "--catalog-path": ("catalog_path", "paths.catalog_path", str(lake_root / "catalog" / "instruments.json")),
        "--reference-catalog-path": ("reference_catalog_path", "paths.reference_catalog", str(lake_root / "reference" / "catalog.json")),
        "--event-log-path": ("event_log_path", "paths.event_log", str(lake_root / "events" / "kairospy.jsonl")),
    }
    for option, (attribute, dotted_path, default) in defaults.items():
        if not hasattr(args, attribute) or _has_cli_option(raw_argv, option):
            continue
        value = config.relative_path(dotted_path, default)
        setattr(args, attribute, str(value))
    if hasattr(args, "data_root") and not _has_cli_option(raw_argv, "--data-root"):
        setattr(args, "data_root", str(lake_root / "snapshots"))


def _require_project_config(args: argparse.Namespace):
    from kairospy.infrastructure.configuration import KairosProjectConfig

    existing = getattr(args, "_kairospy_project_config", None)
    return existing if existing is not None else KairosProjectConfig.discover()


def _config_command(args: argparse.Namespace) -> int:
    from kairospy.infrastructure.configuration import ConfigError, set_config_value, unset_config_value
    from kairospy.surface.cli.output import render_key_value_panel, render_status_table

    try:
        config = _require_project_config(args)
        if args.action == "path":
            print(config.path)
            return 0
        if args.action == "show":
            payload = config.data if args.raw else config.to_redacted_dict()
            if args.format == "json":
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(render_key_value_panel("Kairos Configuration", _flatten_config(payload)))
            return 0
        if args.action == "set":
            set_config_value(config.path, args.path, args.value)
            if not args.quiet:
                print(f"Set {args.path}")
            return 0
        if args.action == "unset":
            removed = unset_config_value(config.path, args.path)
            if not args.quiet:
                print(f"{'Removed' if removed else 'Not set'} {args.path}")
            return 0
        issues = config.validate()
        payload = {"path": str(config.path), "valid": not issues, "issues": issues}
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            rows = [{"name": "config", "status": "ok" if not issues else "warn", "detail": str(config.path)}]
            rows.extend({"name": "issue", "status": "warn", "detail": issue} for issue in issues)
            print(render_status_table("Kairos Config Validation", rows))
        return 2 if issues and args.strict else 0
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc


def _project_command(args: argparse.Namespace) -> int:
    from kairospy.infrastructure.configuration import ConfigError
    from kairospy.surface.cli.output import render_key_value_panel

    try:
        config = _require_project_config(args)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc
    if args.action != "status":
        raise SystemExit(f"unsupported project action: {args.action}")
    payload = {
        "project": config.get("project.name", config.root.name),
        "root": str(config.root),
        "config": str(config.path),
        "data_root": str(config.relative_path("paths.lake_root", DEFAULT_LAKE_ROOT)),
        "default_environment": config.get("execution.default_environment", "simulated"),
        "live_trading": "enabled" if config.get("execution.live_trading_enabled", False) else "locked",
    }
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_key_value_panel("Kairos Project Status", tuple((key.replace("_", " ").title(), value) for key, value in payload.items())))
    return 0


def _configure_command(args: argparse.Namespace) -> int:
    from kairospy.infrastructure.configuration import ConfigError, set_config_value
    from kairospy.surface.cli.output import render_command_success

    try:
        config = _require_project_config(args)
        if args.provider is None:
            args = _prompt_configure_args(args)
        if args.provider == "massive":
            set_config_value(config.path, "credentials.massive_marketdata_primary.api_key", f"env:{args.api_key_env}")
            message = f"Configured Massive API key from {args.api_key_env}"
        elif args.provider == "binance":
            key_env = args.api_key_env or (
                "KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_KEY"
                if args.environment == "testnet"
                else "KAIROS_BINANCE_TRADING_LIVE_SPOT_API_KEY"
            )
            secret_env = args.api_secret_env or (
                "KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_SECRET"
                if args.environment == "testnet"
                else "KAIROS_BINANCE_TRADING_LIVE_SPOT_API_SECRET"
            )
            base = (
                "credentials.binance_trading_testnet_spot"
                if args.environment == "testnet"
                else "credentials.binance_trading_live_spot"
            )
            set_config_value(config.path, f"{base}.api_key", f"env:{key_env}")
            set_config_value(config.path, f"{base}.api_secret", f"env:{secret_env}")
            message = f"Configured Binance {args.environment} credentials from {key_env}/{secret_env}"
        else:
            raise SystemExit(f"unsupported provider: {args.provider}")
        if args.format == "json":
            print(json.dumps({"configured": args.provider, "path": str(config.path)}, ensure_ascii=False, indent=2))
        elif not args.quiet:
            print(render_command_success("Kairos Provider Configured", (
                ("Provider", args.provider),
                ("Config", str(config.path)),
                ("Result", message),
            )))
        return 0
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc


def _doctor(args: argparse.Namespace) -> int:
    from kairospy.infrastructure.configuration import ConfigError

    checks: list[dict[str, object]] = []
    try:
        config = _require_project_config(args)
    except ConfigError as exc:
        checks.append({"name": "project", "status": "error", "detail": str(exc)})
        _print_doctor(checks, args.format)
        return 2
    checks.append({"name": "project", "status": "ok", "detail": str(config.path)})
    if not isinstance(config.get("project"), dict) or not config.get("project.name"):
        checks.append({"name": "config", "status": "warning", "detail": "[project].name is required"})
    else:
        checks.append({"name": "config", "status": "ok", "detail": "kairos.toml is structurally valid"})
    lake_root = config.relative_path("paths.lake_root", DEFAULT_LAKE_ROOT)
    checks.append({
        "name": "data",
        "status": "ok" if lake_root.exists() else "warning",
        "detail": f"lake root: {lake_root}",
    })
    _print_doctor(checks, args.format)
    failed = any(item["status"] == "error" or (args.strict and item["status"] == "warning") for item in checks)
    return 2 if failed else 0


def _print_doctor(checks: list[dict[str, object]], output_format: str) -> None:
    from kairospy.surface.cli.output import render_next_steps, render_status_table

    next_steps = _doctor_next_steps(checks)

    if output_format == "json":
        print(json.dumps({"checks": checks, "next_steps": next_steps}, ensure_ascii=False, indent=2))
        return
    print(render_status_table("Kairos Doctor", checks))
    if next_steps:
        print()
        print(render_next_steps(next_steps))


def _doctor_next_steps(checks: list[dict[str, object]]) -> list[str]:
    steps: list[str] = []
    by_name = {str(item.get("name")): str(item.get("status", "")).lower() for item in checks}
    if by_name.get("project") == "error":
        steps.append("kairospy init")
        return steps
    if by_name.get("config") in {"warning", "warn", "error"}:
        steps.append("kairospy config validate")
    if by_name.get("data") in {"warning", "warn", "error"}:
        steps.append("kairospy data start")
    if not steps:
        steps.append("kairospy data catalog")
    return steps


def _flatten_config(payload: dict[str, Any], prefix: str = "") -> list[tuple[str, object]]:
    rows: list[tuple[str, object]] = []
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            rows.extend(_flatten_config(value, path))
        else:
            rows.append((path, value))
    return rows


def _prompt_configure_args(args: argparse.Namespace) -> argparse.Namespace:
    if not args.interactive and not sys.stdin.isatty():
        raise SystemExit("configure requires a provider in non-interactive mode; use 'kairospy configure massive' or 'kairospy configure binance'")
    provider = _prompt_choice("Provider", ("massive", "binance"), default="massive")
    setattr(args, "provider", provider)
    if provider == "massive":
        setattr(args, "api_key_env", _prompt_text("Massive API key environment variable", "KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY"))
        return args
    environment = _prompt_choice("Binance environment", ("testnet", "live"), default="testnet")
    default_key = (
        "KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_KEY"
        if environment == "testnet"
        else "KAIROS_BINANCE_TRADING_LIVE_SPOT_API_KEY"
    )
    default_secret = (
        "KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_SECRET"
        if environment == "testnet"
        else "KAIROS_BINANCE_TRADING_LIVE_SPOT_API_SECRET"
    )
    setattr(args, "environment", environment)
    setattr(args, "api_key_env", _prompt_text("Binance API key environment variable", default_key))
    setattr(args, "api_secret_env", _prompt_text("Binance API secret environment variable", default_secret))
    return args


def _prompt_choice(label: str, choices: tuple[str, ...], *, default: str) -> str:
    if not sys.stdin.isatty():
        prompt = f"{label} [{'/'.join(choices)}] ({default}): "
        value = input(prompt).strip()
        return value if value in choices else default
    try:
        import questionary
    except Exception:
        prompt = f"{label} [{'/'.join(choices)}] ({default}): "
        value = input(prompt).strip()
        return value if value in choices else default
    value = questionary.select(label, choices=list(choices), default=default).ask()
    return str(value or default)


def _prompt_text(label: str, default: str) -> str:
    if not sys.stdin.isatty():
        value = input(f"{label} ({default}): ").strip()
        return value or default
    try:
        import questionary
    except Exception:
        value = input(f"{label} ({default}): ").strip()
        return value or default
    value = questionary.text(label, default=default).ask()
    return str(value or default)


def _prompt_bool(label: str, default: bool) -> bool:
    if not sys.stdin.isatty():
        value = input(f"{label} ({'yes' if default else 'no'}): ").strip().lower()
        if value in {"y", "yes", "true", "1"}:
            return True
        if value in {"n", "no", "false", "0"}:
            return False
        return default
    try:
        import questionary
    except Exception:
        value = input(f"{label} ({'yes' if default else 'no'}): ").strip().lower()
        if value in {"y", "yes", "true", "1"}:
            return True
        if value in {"n", "no", "false", "0"}:
            return False
        return default
    value = questionary.confirm(label, default=default).ask()
    return bool(default if value is None else value)


def _massive_marketdata_config(args: argparse.Namespace | None = None) -> MassiveConfig:
    from kairospy.infrastructure.configuration import ConfigError, load_project_config_or_none
    from kairospy.integrations.config import resolve_massive_marketdata_config

    config = getattr(args, "_kairospy_project_config", None) if args is not None else None
    config = config or load_project_config_or_none()
    if config is not None:
        try:
            return resolve_massive_marketdata_config(config)
        except ConfigError:
            pass
    return MassiveConfig.from_env()


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    parser = _parser()
    if not raw_argv:
        parser.print_help()
        return 0
    args = parser.parse_args(raw_argv)
    if args.group != "init":
        _apply_project_config_defaults(args, raw_argv)
    if args.group == "config":
        return _config_command(args)
    if args.group == "project":
        return _project_command(args)
    if args.group == "doctor":
        return _doctor(args)
    if args.group == "configure":
        return _configure_command(args)
    if args.group == "providers":
        return _providers(args)
    if args.group == "catalog":
        return _catalog(args)
    if args.group == "accounts":
        return _accounts(args)
    if args.group == "account":
        return _account(args)
    if args.group == "order":
        return _submit_order_or_runtime_soak(args)
    if args.group == "runtime" and args.action == "soak":
        return _submit_order_or_runtime_soak(args)
    if args.group == "runtime":
        if args.action == "reference-artifact":
            from kairospy.runtime.profiles.live.reference_artifact import run_runtime_reference_artifact
            result = run_runtime_reference_artifact(args.root)
            payload = {
                "scenario_id": result.scenario_id,
                "audit_hash": result.audit_hash,
                "artifact": str(result.artifact),
            }
        elif args.action == "failure-policy":
            from kairospy.governance.incidents import run_runtime_failure_policy
            result = run_runtime_failure_policy(args.root)
            payload = {
                "policy_id": result["policy_id"],
                "passed": result["passed"],
                "audit_hash": result["audit_hash"],
                "artifact": result["artifact"],
            }
        elif args.action == "orders":
            from kairospy.execution.order_state import DurableOrderStatus
            from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
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
            from kairospy.execution import build_execution_calibration_release
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
        from kairospy.surface.project import initialize_project, render_project_init
        if getattr(args, "target_path", None) is not None:
            args.target = args.target_path
        if args.interactive:
            args.target = Path(_prompt_text("Project directory", str(args.target)))
            args.name = _prompt_text("Project name", args.name or args.target.name or "kairospy-project")
            args.force = _prompt_bool("Overwrite existing scaffold files", args.force)
        result = initialize_project(args.target, name=args.name, force=args.force)
        if args.format == "json":
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        elif not args.quiet:
            print(render_project_init(result))
        return 0
    if args.group == "workspace":
        return _workspace(args)
    if args.group == "run":
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
    repository = FileOptionCaptureRepository(args.data_root)
    service = OptionCaptureService(repository)
    if args.action == "governance-audit":
        from kairospy.governance import audit_governance
        result=audit_governance(args.lake_root)
        print(json.dumps({"passed":result.passed,"checked_datasets":result.checked_datasets,
            "checked_experiments":result.checked_experiments,"checked_strategies":result.checked_strategies,
            "violations":result.violations},ensure_ascii=False,indent=2))
        return 0 if result.passed else 2
    if args.action == "capture-series":
        if args.instruments:
            return _capture_normalized_series(args)
        spec = _spec(args)
        provider = IbkrSpxwOptionChainProvider(spec, host=args.host, port=args.port, client_id=args.client_id)
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
        provider = IbkrSpxwOptionChainProvider(spec, host=args.host, port=args.port, client_id=args.client_id)
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
    from kairospy.surface.cli.output import render_error, render_product_result, resolve_language
    from kairospy.surface import product as product_surface
    from kairospy.integrations.connectors.binance.historical_archive import GracefulShutdown

    if args.group == "run" and args.action in {"start", "config", "live", "inspect", "replay", "compare"}:
        handlers = {
            ("run", "start"): product_surface.run_start,
            ("run", "config"): product_surface.run_config,
            ("run", "live"): product_surface.run_live,
            ("run", "inspect"): product_surface.run_inspect,
            ("run", "replay"): product_surface.run_replay,
            ("run", "compare"): product_surface.run_compare,
        }
        try:
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
        if args.group == "run" and args.action == "config" and getattr(args, "config_action", None) == "validate":
            if payload.get("issues") and getattr(args, "strict", False):
                return 2
        return 0

    raise SystemExit(f"unsupported product command {args.group} {args.action}")


def _workspace(args: argparse.Namespace) -> int:
    from kairospy.workspace import WorkspaceRepository

    repository = WorkspaceRepository.discover(Path.cwd())
    if args.action == "create":
        workspace = repository.open_or_create(args.name)
        payload = {
            "product": "workspace",
            "operation": "create",
            "workspace": workspace.name,
            "root": str(workspace.root),
            "bindings": workspace.snapshot()["bindings"],
        }
    elif args.action == "bind-data":
        workspace = repository.open_or_create(args.workspace)
        binding = workspace.bind_data(args.name, dataset=args.dataset)
        payload = {
            "product": "workspace",
            "operation": "bind-data",
            "workspace": workspace.name,
            "binding": binding.to_dict(),
            "root": str(workspace.root),
        }
    elif args.action == "bind-live":
        workspace = repository.open_or_create(args.workspace)
        binding = workspace.bind_live(args.name, dataset=args.dataset)
        payload = {
            "product": "workspace",
            "operation": "bind-live",
            "workspace": workspace.name,
            "binding": binding.to_dict(),
            "root": str(workspace.root),
        }
    elif args.action == "inspect":
        workspace = repository.open(args.name)
        payload = {
            "product": "workspace",
            "operation": "inspect",
            "workspace": workspace.name,
            "root": str(workspace.root),
            **workspace.snapshot(),
        }
    else:
        raise SystemExit(f"unsupported workspace action {args.action!r}")
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    elif not args.quiet:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _providers(args: argparse.Namespace) -> int:
    from kairospy.surface import providers as provider_surface

    if args.action == "list":
        payload = provider_surface.providers_list(args.lake_root)
    else:
        payload = provider_surface.provider_doctor(args.lake_root, args.provider)
    _emit_provider_payload(args, "Kairos Providers" if args.action == "list" else "Kairos Provider Diagnostics", payload)
    return 2 if payload.get("status") == "unknown_provider" else 0


def _emit_provider_payload(args: argparse.Namespace, title: str, payload: dict[str, object]) -> None:
    from kairospy.surface.cli.output import render_generic_payload, render_key_value_panel, render_status_table

    primitive = to_primitive(payload)
    if args.format == "json":
        print(json.dumps(primitive, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if args.action == "list" and isinstance(primitive.get("providers"), list):
        rows = [{
            "provider": item.get("provider", ""),
            "status": item.get("status", ""),
            "data_products": item.get("data_products", ""),
            "available": item.get("available_data_products", ""),
            "venues": ", ".join(str(value) for value in item.get("venues", ())),
        } for item in primitive["providers"] if isinstance(item, dict)]
        print(render_status_table(title, rows, columns=("provider", "status", "data_products", "available", "venues")))
        return
    if args.action == "doctor":
        rows = [
            ("Provider", primitive.get("provider", "")),
            ("Status", primitive.get("status", "")),
            ("Venues", ", ".join(str(value) for value in primitive.get("venues", ()))),
        ]
        output = [render_key_value_panel(title, [(label, value) for label, value in rows if value not in ("", None)])]
        products = primitive.get("data_products")
        if isinstance(products, list):
            product_rows = [{
                "key": item.get("key", ""),
                "status": item.get("status", ""),
                "capability": item.get("capability", ""),
                "dataset": item.get("dataset", ""),
            } for item in products if isinstance(item, dict)]
            output.append(render_status_table("Data Products", product_rows, columns=("key", "status", "capability", "dataset")))
        issues = primitive.get("issues")
        if isinstance(issues, list) and issues:
            issue_rows = [{
                "code": item.get("code", ""),
                "message": item.get("message", ""),
            } for item in issues if isinstance(item, dict)]
            output.append(render_status_table("Issues", issue_rows, columns=("code", "message")))
        print("\n\n".join(item for item in output if item))
        return
    print(render_generic_payload(title, primitive))


def _data(args: argparse.Namespace) -> int:
    if args.action == "apply":
        from kairospy.surface import product as product_surface
        payload = product_surface.data_apply(args)
        _emit_data_payload(args, "Kairos Data Manifest", payload)
        return 0
    if args.action == "start":
        from kairospy.surface import product as product_surface
        payload = product_surface.data_start(args)
        _emit_data_payload(args, "Kairos Data Start", payload)
        return 0
    if args.action == "add":
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_add(args)
        except Exception as error:
            from kairospy.data.metadata import DataNeedsTimeError

            if isinstance(error, DataNeedsTimeError):
                payload = error.to_payload(dataset_id=str(args.name), source=args.source)
                _emit_data_payload(args, "Kairos Dataset", payload)
                return 2
            if isinstance(error, product_surface.DataAddInputError):
                payload = error.to_payload(dataset_id=str(args.name))
                _emit_data_payload(args, "Kairos Dataset", payload)
                return 2
            raise
        _emit_data_payload(args, "Kairos Dataset", payload)
        return 0
    if args.action == "use":
        if not args.list_products and not args.key:
            raise SystemExit("data use requires a built-in product key or --list-products")
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_use(args)
        except product_surface.DataProductNotFoundError as error:
            payload = error.to_payload()
            _emit_data_payload(args, "Kairos Built-In Data Product", payload)
            return 2
        _emit_data_payload(args, "Kairos Dataset", payload)
        return 0
    if args.action in {"product", "products"}:
        from kairospy.surface import product as product_surface
        if args.product_action == "list":
            _emit_data_payload(args, "Kairos Dataset", product_surface.data_product_list(args))
            return 0
        if args.product_action == "doctor":
            payload = product_surface.data_product_doctor(args)
            _emit_data_payload(args, "Kairos Data Product", payload)
            return 2 if payload.get("status") == "unknown_data_product" else 0
        else:
            raise SystemExit(f"unsupported data product action {args.product_action!r}")
    if args.action == "protocol":
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_protocol(args)
        except (FileNotFoundError, ValueError) as error:
            payload = product_surface.data_protocol_error(args, error)
            _emit_data_payload(args, "Kairos Data Protocol", payload)
            return 2
        _emit_data_payload(args, "Kairos Data Protocol", payload)
        return 0
    if args.action == "connect":
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_connect(args)
        except product_surface.DataProductNotFoundError as error:
            payload = error.to_payload()
            _emit_data_payload(args, "Kairos Built-In Data Product", payload)
            return 2
        _emit_data_payload(args, "Kairos Dataset", payload)
        return 0
    if args.action == "sample":
        from kairospy.surface import product as product_surface
        try:
            if args.format == "json":
                payload = product_surface.data_sample(args)
            else:
                started = {"value": False}

                def _print_sample_row(row):
                    if not started["value"]:
                        print("Kairos Data Sample")
                        print("Rows")
                        started["value"] = True
                    print(json.dumps(to_primitive(row), ensure_ascii=False, sort_keys=True), flush=True)

                payload = product_surface.data_sample(args, on_row=_print_sample_row)
        except product_surface.DataProductNotFoundError as error:
            payload = error.to_payload()
            _emit_data_payload(args, "Kairos Built-In Data Product", payload)
            return 2
        _emit_data_payload(args, "Kairos Data Sample Summary" if args.format != "json" else "Kairos Data Sample", payload)
        return 0
    if args.action == "reconnect":
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_reconnect(args)
        except product_surface.DataLiveDatasetNotConfiguredError as error:
            payload = error.to_payload()
            _emit_data_payload(args, "Kairos Dataset", payload)
            return 2
        _emit_data_payload(args, "Kairos Dataset", payload)
        return 0
    if args.action in {"download", "register-download", "register-provider", "write"}:
        from kairospy.surface import product as product_surface
        handlers = {
            "download": product_surface.data_download,
            "register-download": product_surface.data_register_download,
            "register-provider": product_surface.data_register_provider,
            "write": product_surface.data_write,
        }
        payload = handlers[args.action](args)
        print(json.dumps(to_primitive(payload), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.action == "soak-binance":
        if args.duration_seconds <= 0 or args.minimum_events <= 0 or args.maximum_silence_seconds <= 0:
            raise SystemExit("soak duration, minimum events and maximum silence must be positive")
        import asyncio
        from kairospy.integrations.connectors.binance.market_stream import BinanceStreamSession, WebSocketClientConnector, websocket_url
        from kairospy.integrations.connectors.binance.stream import BinanceCanonicalStreamService
        from kairospy.market.capture import RotatingCanonicalCaptureWriter
        from kairospy.market.soak import run_binance_market_restart_campaign, run_binance_market_soak
        from kairospy.market.stream import BoundedEventChannel

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
                    {symbol: instrument}, output, source_instance="kairospy-soak", stream_id=stream,
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
        payload = to_primitive(result)
        if args.live_view_manifest is not None:
            from kairospy.data import update_live_view_manifest_freshness
            manifest = update_live_view_manifest_freshness(args.live_view_manifest, payload)
            payload["live_view_manifest"] = {
                "artifact": str(args.live_view_manifest),
                "live_view_id": manifest.live_view_id,
                "freshness_status": manifest.freshness_status,
                "manifest_hash": manifest.manifest_hash,
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if result.passed else 2
    if args.action == "live-binance":
        if args.messages <= 0:
            raise SystemExit("--messages must be positive")
        import asyncio
        from kairospy.integrations.connectors.binance.market_stream import BinanceStreamSession, WebSocketClientConnector, websocket_url
        from kairospy.integrations.connectors.binance.stream import BinanceCanonicalStreamService
        from kairospy.market.stream import BoundedEventChannel
        from kairospy.market.capture import CanonicalCaptureWriter

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
                source_instance="kairospy-cli", stream_id=stream,
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
    if args.action == "list":
        from kairospy.surface import product as product_surface
        args.dimension = _dimension_filters(args.dimension)
        payload = product_surface.data_list(args)
        _emit_data_payload(args, "Kairos Data Products", payload)
        return 0
    if args.action == "releases":
        payload = {"releases": DatasetClient(args.lake_root).releases(
            args.dataset, **_dimension_filters(args.dimension),
        )}
        _emit_data_payload(args, "Kairos Data Releases", payload)
        return 0
    if args.action == "search":
        from kairospy.surface import product as product_surface
        args.dimension = _dimension_filters(args.dimension)
        payload = product_surface.data_list(args)
        payload["operation"] = "search"
        _emit_data_payload(args, "Kairos Data Search", payload)
        return 0
    if args.action == "describe":
        from kairospy.surface import product as product_surface
        _emit_data_payload(args, "Kairos Dataset", product_surface.data_doctor(args))
        return 0
    if args.action == "audit":
        from kairospy.surface import product as product_surface
        _emit_data_payload(args, "Kairos Dataset Audit", product_surface.data_audit(args))
        return 0
    if args.action == "doctor":
        from kairospy.surface import product as product_surface
        _emit_data_payload(args, "Kairos Data Diagnostics", product_surface.data_doctor(args))
        return 0
    if args.action == "metadata":
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_metadata(args)
        except product_surface.DataDatasetInputError as error:
            payload = error.to_payload()
            _emit_data_payload(args, "Kairos Dataset Metadata", payload)
            return 2
        _emit_data_payload(args, "Kairos Dataset Metadata", payload)
        return 0
    if args.action == "diagnostics":
        from kairospy.data.diagnostics import DataDiagnosticsService
        service = DataDiagnosticsService(args.lake_root)
        report = service.audit()
        _emit_data_payload(args, "Kairos Data Diagnostics", report)
        return 2 if args.strict and not report["healthy"] else 0
    if args.action == "us-equity-momentum-diagnostics":
        from kairospy.analytics.features import UsEquityMomentumDiagnostics
        report = UsEquityMomentumDiagnostics(args.lake_root).report(workspace=args.workspace, version=args.version)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2 if args.strict and report["summary"]["errors"] else 0
    if args.action == "validate":
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_validate(args)
        except product_surface.DataDatasetInputError as error:
            payload = error.to_payload()
            _emit_data_payload(args, "Kairos Data Validation", payload)
            return 2
        _emit_data_payload(args, "Kairos Data Validation", payload)
        return 0 if payload["status"] == "passed" else 2
    if args.action == "prepare":
        register_default_products(args.lake_root)
        register_configured_products(args.lake_root)
        providers = default_provider_registry(
            args.lake_root,
            progress=None,
        )
        client = DatasetClient(args.lake_root, providers=providers, acquisition_limits=_acquisition_limits(args))
        from kairospy.data.preparation import DataPreparationService
        prepared = DataPreparationService(client).prepare(
            args.dataset, start=datetime.fromisoformat(args.start), end=datetime.fromisoformat(args.end),
            minimum_quality=QualityLevel(args.quality), provider=args.provider, venue=args.venue,
            acquire_missing=args.acquire_missing, promote=args.promote, actor=args.actor, reason=args.reason,
        )
        _emit_data_payload(args, "Kairos Data Preparation", to_primitive(prepared)); return 0
    if args.action == "prepare-us-equity-momentum":
        result = _prepare_us_equity_momentum(args)
        print(json.dumps(to_primitive(result), ensure_ascii=False, indent=2))
        return 0 if result["readiness"]["summary"]["errors"] == 0 else 2
    if args.action == "query":
        if args.limit <= 0:
            raise SystemExit("--limit must be positive")
        dataset = _dataset_argument(args)
        try:
            query = DatasetClient(args.lake_root).get(
                dataset, start=args.start, end=args.end, fields=tuple(args.field) or None,
            )
        except KeyError as error:
            from kairospy.surface import product as product_surface
            if product_surface._live_view_metadata_payloads(Path(args.lake_root), dataset):
                payload = product_surface._historical_not_configured_error("query", dataset).to_payload()
            else:
                payload = product_surface._dataset_not_found_error("query", dataset).to_payload()
            _emit_data_payload(args, "Kairos Data Query", payload)
            return 2
        rows = query.collect(OutputFormat.ROWS)
        logical_name = str(query.explain().get("logical_name") or dataset)
        payload = {
            "product": "data", "operation": "query", "dataset": logical_name,
            "returned_rows": min(len(rows), args.limit), "total_rows": len(rows),
            "rows": to_primitive(rows[:args.limit]),
        }
        _emit_data_payload(args, "Kairos Data Query", payload); return 0
    if args.action == "replay":
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_replay(args)
        except product_surface.DataDatasetInputError as error:
            payload = error.to_payload()
            _emit_data_payload(args, "Kairos Data Replay", payload)
            return 2
        _emit_data_payload(args, "Kairos Data Replay", payload)
        return 0
    if args.action == "freeze":
        client = DatasetClient(args.lake_root)
        queries = tuple(client.get(dataset) for dataset in args.dataset)
        target = client.freeze_snapshot(
            args.output, args.workspace, queries, code_version=args.code_version,
        )
        print(json.dumps({
            "workspace": args.workspace, "snapshot": str(target),
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
        _emit_data_payload(args, "Kairos Data Catalog", {"products": values}); return 0
    if args.action == "copy":
        from kairospy.data.transfer import copy_dataset_release
        result = copy_dataset_release(
            args.source_root,
            args.target_root or args.lake_root,
            args.dataset,
            release=args.release,
            include_source_cache=args.include_source_cache,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
        _emit_data_payload(args, "Kairos Data Copy", to_primitive(result)); return 0
    if args.action == "compare":
        comparison = DatasetClient(args.lake_root).compare(args.first, args.second)
        print(json.dumps(comparison, ensure_ascii=False, indent=2)); return 0
    if args.action == "audit-artifact":
        from kairospy.data.artifact_audit import audit_governed_artifact
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
        register_configured_products(args.lake_root)
        providers = default_provider_registry(
            args.lake_root,
            progress=None,
        )
        client = DatasetClient(args.lake_root, providers=providers, acquisition_limits=_acquisition_limits(args))
        if args.action == "acquire" and getattr(args, "list_products", False):
            _emit_data_payload(args, "Kairos Acquirable Data Products", {"products": _acquirable_product_rows(client, providers)})
            return 0
        interactive_acquire = args.action == "acquire" and not (args.dataset and args.start and args.end)
        if args.action == "acquire":
            _prompt_acquire_args(args, client, providers)
        if not args.dataset or not args.start or not args.end:
            raise SystemExit("--dataset, --start and --end are required outside interactive acquire")
        start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
        plan = client.plan(args.dataset, start=start, end=end, provider=args.provider, venue=args.venue)
        plan = _plan_with_cli_instruments(plan, providers, tuple(args.instrument))
        if args.action == "plan":
            _emit_data_payload(args, "Kairos Acquisition Plan", _acquisition_plan_payload(plan, providers, tuple(args.instrument))); return 0
        _emit_data_payload(args, "Kairos Acquisition Plan", _acquisition_plan_payload(plan, providers, tuple(args.instrument)))
        if args.dry_run:
            return 0
        should_confirm = interactive_acquire or sys.stdin.isatty()
        if should_confirm and not args.yes and not _prompt_bool("Proceed with acquisition", False):
            print("Acquisition cancelled")
            return 1
        try:
            release = client.acquire(plan, instruments=tuple(args.instrument), refresh=args.refresh)
        except RuntimeError as error:
            raise SystemExit(str(error)) from error
        _emit_data_payload(args, "Kairos Dataset", _dataset_acquire_payload(release)); return 0
    if args.action == "promote":
        if getattr(args, "for_use", None):
            if not args.dataset:
                raise SystemExit("data promote requires a Dataset name with --for")
            from kairospy.surface import product as product_surface
            try:
                payload = product_surface.data_promote(args)
            except product_surface.DataDatasetInputError as error:
                payload = error.to_payload()
                _emit_data_payload(args, "Kairos Dataset Promotion", payload)
                return 2
            _emit_data_payload(args, "Kairos Dataset Promotion", payload)
            return 0 if payload.get("status") in {"ready_for_workspace", "ready_for_backtest", "ready_for_production"} else 2
        raise SystemExit("data promote requires --for workspace, --for backtest, or --for production")
    if args.action == "quarantine-insecure-provider-cache":
        moved = MassiveVendorArchiveClient.quarantine_non_https(args.lake_root)
        print(json.dumps({"quarantined": len(moved), "paths": [str(item) for item in moved]}, ensure_ascii=False, indent=2)); return 0
    if args.action == "sync-provider-reference":
        pipeline = MassiveReferencePipeline(args.lake_root, MassiveClient(_massive_marketdata_config(args)))
        result: dict[str, object] = {"code_tables": pipeline.sync_code_tables()}
        if args.equity_tickers:
            result["equity_tickers"] = pipeline.sync_equity_tickers(include_inactive=not args.active_only)
        if args.ticker:
            if not args.start or not args.end:
                raise SystemExit("--start and --end are required with --ticker")
            result["corporate_actions"] = pipeline.sync_corporate_actions(args.ticker, datetime.fromisoformat(args.start), datetime.fromisoformat(args.end))
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    if args.action == "build-provider-equity-identity":
        reference_rows = json.loads(args.reference_rows.read_text(encoding="utf-8"))
        ticker_events = json.loads(args.ticker_events.read_text(encoding="utf-8")) if args.ticker_events else []
        resolver = MassiveEquityIdentityResolver()
        resolved = resolver.resolve(reference_rows, ticker_events)
        manifest = resolver.save(resolved, args.lake_root)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0 if not resolved.quarantined else 2
    if args.action == "build-provider-slices":
        dataset = MassiveMarketSnapshotBuilder(args.lake_root, reference_catalog_path=args.reference_catalog_path, dataset_root=args.dataset_root).build(
            args.source_dataset, args.output_dataset, datetime.fromisoformat(args.start), datetime.fromisoformat(args.end),
            sampling_seconds=args.sampling_seconds, max_quote_age_seconds=args.max_quote_age_seconds,
            split=args.split, risk_free_rate=args.risk_free_rate)
        print(f"{dataset.manifest.dataset_id}: slices={dataset.manifest.slice_count} hash={dataset.manifest.content_hash}")
        return 0
    if args.action == "provider-entitlement-diagnostics":
        report = MassiveEntitlementDiagnostics(MassiveClient(_massive_marketdata_config(args))).check(
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
    if args.action == "provider-fetch":
        client = MassiveClient(_massive_marketdata_config(args))
        archive = MassiveVendorArchiveClient(args.lake_root, client)
        resource, params = _massive_request(args)
        result = archive.fetch_pages(resource, params, max_pages=args.max_pages)
        print(json.dumps({"fingerprint": result.fingerprint, "directory": str(result.directory), "receipt": result.receipt}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "provider-flat-file":
        client = MassiveClient(_massive_marketdata_config(args))
        flat = MassiveFlatFileClient(args.lake_root, client)
        if args.operation == "usage":
            print(json.dumps(flat.usage(), ensure_ascii=False, indent=2)); return 0
        if not args.key:
            raise SystemExit("--key is required for Massive Flat File status/download")
        if args.operation == "status":
            print(json.dumps(flat.cache_status(args.key), ensure_ascii=False, indent=2)); return 0
        print(flat.download(args.key)); return 0
    if args.action == "provider-flat-file-batch":
        flat = MassiveFlatFileClient(args.lake_root, MassiveClient(_massive_marketdata_config(args)))
        report = MassiveFlatFileBatchDownloader(flat).download_range(
            date.fromisoformat(args.start), date.fromisoformat(args.end), max_files=args.max_files, dry_run=args.dry_run,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2)); return 0
    if args.action in {"prepare-spxw-daily-ohlcv", "prepare-spxw-day-aggs"}:
        manifest = SpxwDailyOhlcvPipeline(args.lake_root).prepare(
            args.dataset_id, date.fromisoformat(args.start), date.fromisoformat(args.end),
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    if args.action in {"prepare-option-daily-ohlcv", "prepare-option-day-aggs"}:
        manifest = OptionDailyOhlcvPipeline(args.lake_root, args.option_root).prepare(
            args.dataset_id, date.fromisoformat(args.start), date.fromisoformat(args.end),
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    if args.action in {"prepare-equity-daily-ohlcv", "prepare-equity-day-aggs"}:
        manifest = MassiveEquityDailyOhlcvPipeline(
            args.lake_root, MassiveClient(_massive_marketdata_config(args)),
        ).prepare(
            args.dataset_id, args.ticker, date.fromisoformat(args.start), date.fromisoformat(args.end),
            view=args.view,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    if args.action in {"prepare-equity-hourly-ohlcv", "prepare-equity-hour-aggs"}:
        manifest = MassiveEquityHourlyOhlcvPipeline(
            args.lake_root, MassiveClient(_massive_marketdata_config(args)),
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
    metadata = DatasetClient(args.lake_root).metadata(args.dataset)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


def _emit_data_payload(args: argparse.Namespace, title: str, payload: object) -> None:
    from kairospy.surface.cli.output import (
        render_builtin_data_products, render_data_catalog, render_dataset_detail, render_dataset_list,
        render_dataset_releases, render_generic_payload, render_key_value_panel, render_status_table,
    )

    primitive = to_primitive(payload)
    if getattr(args, "action", None) != "audit":
        primitive = _hide_default_data_internals(primitive)
    if args.format == "json":
        print(json.dumps(primitive, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if not isinstance(primitive, dict):
        print(json.dumps(primitive, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if args.action == "catalog" and isinstance(primitive.get("products"), list):
        print(render_data_catalog(primitive["products"]))
        return
    if (
        args.action in {"product", "products"}
        and getattr(args, "product_action", None) == "list"
        and isinstance(primitive.get("products"), list)
    ):
        print(render_builtin_data_products("Kairos Built-In Data Products", primitive["products"]))
        return
    if args.action in {"product", "products"} and getattr(args, "product_action", None) == "doctor":
        print(_render_data_product_doctor_payload(title, primitive))
        return
    if args.action == "protocol":
        print(_render_data_protocol_payload(title, primitive))
        return
    if args.action == "use" and getattr(args, "list_products", False) and isinstance(primitive.get("products"), list):
        print(render_builtin_data_products("Kairos Built-In Data Products", primitive["products"]))
        return
    if args.action == "use" and primitive.get("operation") == "use":
        print(_render_data_use_payload(title, primitive))
        return
    if args.action == "list" and isinstance(primitive.get("products"), list):
        print(render_dataset_list(title, primitive["products"]))
        return
    if args.action == "releases" and isinstance(primitive.get("releases"), list):
        print(render_dataset_releases(title, primitive["releases"]))
        return
    if args.action == "search" and isinstance(primitive.get("products"), list):
        print(render_dataset_list(title, primitive["products"]))
        return
    if args.action == "acquire" and isinstance(primitive.get("products"), list):
        print(render_dataset_list(title, primitive["products"]))
        return
    if args.action in {"describe", "doctor"}:
        print(_render_data_doctor_payload(title, primitive))
        return
    if args.action == "diagnostics":
        print(render_status_table(title, _diagnostic_rows(primitive)))
        return
    if args.action == "acquire" and primitive.get("operation") == "acquire":
        print(_render_dataset_acquire_payload(title, primitive))
        return
    if args.action in {"plan", "acquire"}:
        print(_render_acquisition_plan_payload(title, primitive))
        return
    if args.action == "query":
        print(_render_query_payload(title, primitive))
        return
    if args.action == "replay":
        print(_render_replay_payload(title, primitive))
        return
    if args.action == "sample":
        print(_render_sample_payload(title, primitive))
        return
    print(render_generic_payload(title, primitive))


def _hide_default_data_internals(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _hide_default_data_internals(item)
            for key, item in value.items()
            if key != "source_kind"
        }
    if isinstance(value, list):
        return [_hide_default_data_internals(item) for item in value]
    return value


def _dimension_filters(values: list[str]) -> dict[str, str]:
    dimensions = {}
    for item in values:
        if "=" not in item:
            raise SystemExit("--dimension must use key=value")
        key, value = item.split("=", 1)
        if not key.strip() or not value.strip():
            raise SystemExit("--dimension key and value cannot be empty")
        dimensions[key.strip()] = value.strip()
    return dimensions


def _render_data_use_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel

    historical = payload.get("historical") if isinstance(payload.get("historical"), dict) else {}
    rows = [
        ("Product", payload.get("product", "")),
        ("Operation", payload.get("operation", "")),
        ("Dataset", payload.get("dataset", "")),
        ("Data Product", payload.get("data_product", "")),
        ("Default Dataset", payload.get("default_dataset", "")),
        ("Title", payload.get("title", "")),
        ("Capability", payload.get("capability", "")),
        ("Target Use", payload.get("target_use", "")),
        ("Status", historical.get("status", "")),
        ("Ready For", ", ".join(str(item) for item in historical.get("ready_for", ()))),
        ("Blocked For", ", ".join(str(item) for item in historical.get("blocked_for", ()))),
        ("Time", payload.get("time", "")),
        ("Requires Account", payload.get("requires_account", "")),
        ("Provider", payload.get("provider", "")),
        ("Venue", payload.get("venue", "")),
    ]
    return render_key_value_panel(title, [(label, value) for label, value in rows if value not in ("", None)])


def _dataset_argument(args: argparse.Namespace) -> str:
    option = getattr(args, "dataset", None)
    positional = getattr(args, "dataset_arg", None)
    if option and positional and str(option) != str(positional):
        raise SystemExit(f"conflicting Dataset values: {positional} and {option}")
    dataset = option or positional
    if not dataset:
        raise SystemExit("Dataset is required")
    return str(dataset)


def _render_sample_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel

    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    rows = [
        ("Product", payload.get("product", "")),
        ("Operation", payload.get("operation", "")),
        ("Source", payload.get("source", "")),
        ("Dataset", payload.get("dataset", "")),
        ("Provider", payload.get("provider", "")),
        ("Venue", payload.get("venue", "")),
        ("Market", runtime.get("market", "")),
        ("Symbol", runtime.get("symbol", "")),
        ("Channel", runtime.get("channel", "")),
        ("Levels", runtime.get("levels", "")),
        ("Interval", runtime.get("interval", "")),
        ("Stream", runtime.get("stream", "")),
        ("Limit", payload.get("limit", "")),
        ("Row Count", payload.get("row_count", "")),
    ]
    return render_key_value_panel(title, [(label, value) for label, value in rows if value not in ("", None)])


def _prompt_acquire_args(args: argparse.Namespace, client: DatasetClient, providers) -> None:
    if args.dataset and args.start and args.end:
        return
    products = _acquirable_product_rows(client, providers)
    if not products:
        raise SystemExit("no acquirable data products are registered")
    if args.dataset is None:
        print("Acquirable Data Products")
        for index, product in enumerate(products, start=1):
            print(f"  {index}. {product['logical_key']}  {product['title']}")
        selected = _prompt_text("Dataset number or logical key", "1").strip()
        if selected.isdigit() and 1 <= int(selected) <= len(products):
            args.dataset = str(products[int(selected) - 1]["logical_key"])
        else:
            args.dataset = selected
    if args.start is None:
        args.start = _prompt_text("Start [inclusive ISO-8601]", "")
    if args.end is None:
        args.end = _prompt_text("End [exclusive ISO-8601]", "")
    if not args.instrument:
        universe = _prompt_text("Universe [full-market or comma-separated instruments]", "full-market").strip()
        if universe and universe != "full-market":
            args.instrument = tuple(item.strip() for item in universe.split(",") if item.strip())


def _acquirable_product_rows(client: DatasetClient, providers) -> list[dict[str, object]]:
    rows = []
    specs = getattr(providers, "_specs", {})
    for key, spec in sorted(specs.items()):
        product = spec.product
        rows.append({
            "logical_key": str(key),
            "title": product.title,
            "layer": product.layer.value,
            "dimensions": dict(product.dimensions),
            "primary_time": product.primary_time,
            "sources": to_primitive(product.sources),
            "releases": [to_primitive(release) for release in client.catalog.releases(product)],
        })
    return rows


def _plan_with_cli_instruments(plan, providers, instruments: tuple[str, ...]):
    if not instruments or plan.selected is None or not plan.connector_available:
        return plan
    from dataclasses import replace
    from kairospy.data import AcquisitionRequest

    connector = providers.get(plan.selected.provider, plan.logical_key)
    request = AcquisitionRequest(
        plan.logical_key, plan.missing, plan.selected, instruments,
        base_release_id=plan.local_release_id,
    )
    estimate = connector.estimate(request) if hasattr(connector, "estimate") else plan.estimate
    return replace(plan, estimate=estimate)


def _acquisition_plan_payload(plan, providers, instruments: tuple[str, ...]) -> dict[str, object]:
    payload = to_primitive(plan)
    if plan.selected is None or not plan.connector_available:
        return payload
    connector = providers.get(plan.selected.provider, plan.logical_key)
    task_plan = getattr(connector, "task_plan", None)
    if task_plan is None:
        return payload
    from kairospy.data import AcquisitionRequest

    request = AcquisitionRequest(
        plan.logical_key, plan.missing, plan.selected, instruments,
        base_release_id=plan.local_release_id,
    )
    try:
        payload["provider_tasks"] = task_plan(request)
    except Exception as error:
        payload["provider_tasks"] = {"status": "unavailable", "error": f"{type(error).__name__}: {error}"}
    return payload


def _acquisition_limits(args: argparse.Namespace) -> AcquisitionLimits:
    max_requests = int(getattr(args, "max_requests", 10_000))
    max_instruments = int(getattr(args, "max_instruments", 10_000))
    max_bytes = getattr(args, "max_bytes", None)
    if max_requests <= 0 or max_instruments <= 0 or max_bytes is not None and int(max_bytes) <= 0:
        raise SystemExit("acquisition limits must be positive")
    return AcquisitionLimits(maximum_requests=max_requests, maximum_instruments=max_instruments, maximum_bytes=max_bytes)


def _diagnostic_rows(payload: dict[str, object]) -> list[dict[str, object]]:
    if isinstance(payload.get("checks"), list):
        return [
            {
                "name": item.get("name", item.get("check", "check")),
                "status": "ok" if item.get("passed", item.get("healthy", False)) else "warn",
                "detail": item.get("detail", item.get("message", "")),
            }
            for item in payload["checks"] if isinstance(item, dict)
        ]
    summary = payload.get("summary")
    if isinstance(summary, dict):
        return [{"name": key, "status": "ok" if not value else "warn", "detail": value} for key, value in summary.items()]
    healthy = payload.get("healthy", payload.get("passed", True))
    return [{"name": "data", "status": "ok" if healthy else "warn", "detail": "healthy" if healthy else "needs attention"}]


def _render_data_doctor_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel

    def join_values(key: str) -> str:
        values = payload.get(key)
        if isinstance(values, list):
            return ", ".join(str(item) for item in values) if values else "-"
        return "-"

    rows = (
        ("Dataset", payload.get("dataset", "-")),
        ("Status", payload.get("status", "-")),
        ("Time", payload.get("time", "-")),
        ("Ready For", join_values("ready_for")),
        ("Blocked For", join_values("blocked_for")),
        ("Issues", join_values("issues")),
    )
    return render_key_value_panel(title, rows)


def _dataset_acquire_payload(release: DatasetRelease) -> dict[str, object]:
    ready_for = _ready_for_dataset_status(release.status)
    all_uses = ("workspace", "backtest", "production")
    return {
        "product": "data",
        "operation": "acquire",
        "dataset": str(release.product_key),
        "status": _dataset_ready_status(release.status),
        "ready_for": ready_for,
        "blocked_for": [value for value in all_uses if value not in ready_for],
        "provider": release.provider,
        "venue": release.venue,
        "quality_level": release.quality_level.value,
        "format": release.format,
    }


def _dataset_ready_status(status: DatasetStatus) -> str:
    if status is DatasetStatus.APPROVED_FOR_PRODUCTION:
        return "ready_for_production"
    if status is DatasetStatus.APPROVED_FOR_BACKTEST:
        return "ready_for_backtest"
    if status is DatasetStatus.APPROVED_FOR_WORKSPACE:
        return "ready_for_workspace"
    return status.value


def _ready_for_dataset_status(status: DatasetStatus) -> list[str]:
    if status is DatasetStatus.APPROVED_FOR_PRODUCTION:
        return ["workspace", "backtest", "production"]
    if status is DatasetStatus.APPROVED_FOR_BACKTEST:
        return ["workspace", "backtest"]
    if status is DatasetStatus.APPROVED_FOR_WORKSPACE:
        return ["workspace"]
    return []


def _render_dataset_acquire_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel

    def join_values(key: str) -> str:
        values = payload.get(key)
        if isinstance(values, list):
            return ", ".join(str(item) for item in values) if values else "-"
        return "-"

    rows = (
        ("Dataset", payload.get("dataset", "-")),
        ("Status", payload.get("status", "-")),
        ("Ready For", join_values("ready_for")),
        ("Blocked For", join_values("blocked_for")),
        ("Provider", payload.get("provider", "-")),
        ("Venue", payload.get("venue", "-")),
        ("Quality Level", payload.get("quality_level", "-")),
        ("Format", payload.get("format", "-")),
    )
    return render_key_value_panel(title, rows)


def _render_data_product_doctor_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel, render_status_table

    aliases = payload.get("aliases")
    alias_text = ", ".join(str(alias) for alias in aliases) if isinstance(aliases, list) and aliases else "-"
    rows = (
        ("Data Product", payload.get("key") or payload.get("requested_key", "-")),
        ("Requested", payload.get("requested_key", "-")),
        ("Status", payload.get("status", "-")),
        ("Available", "yes" if payload.get("available") else "no"),
        ("Provider", payload.get("provider", "-")),
        ("Venue", payload.get("venue", "-")),
        ("Dataset", payload.get("dataset", "-")),
        ("Capability", payload.get("capability", "-")),
        ("Aliases", alias_text),
    )
    output = [render_key_value_panel(title, rows)]
    issues = payload.get("issues")
    if isinstance(issues, list) and issues:
        output.append(render_status_table(
            "Issues",
            [{"code": item.get("code", ""), "message": item.get("message", "")}
             for item in issues if isinstance(item, dict)],
            columns=("code", "message"),
        ))
    commands = payload.get("next_commands")
    if isinstance(commands, list) and commands:
        output.append(render_status_table(
            "Next Commands",
            [{"command": command} for command in commands],
            columns=("command",),
        ))
    return "\n\n".join(item for item in output if item)


def _render_query_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel, render_status_table

    if payload.get("status") and "returned_rows" not in payload:
        issues = payload.get("issues")
        issue_codes = []
        if isinstance(issues, list):
            for issue in issues:
                if isinstance(issue, dict):
                    issue_codes.append(str(issue.get("code") or issue.get("message") or issue))
                else:
                    issue_codes.append(str(issue))
        return render_key_value_panel(title, (
            ("Dataset", payload.get("dataset", "-")),
            ("Status", payload.get("status", "-")),
            ("Issues", ", ".join(issue_codes) if issue_codes else "-"),
            ("Next Command", payload.get("next_command", "-")),
        ))

    output = [render_key_value_panel(title, (
        ("Dataset", payload.get("dataset", "-")),
        ("Returned Rows", payload.get("returned_rows", "-")),
        ("Total Rows", payload.get("total_rows", "-")),
    ))]
    rows = payload.get("rows")
    if isinstance(rows, list) and rows:
        fields = tuple(str(key) for key in rows[0].keys()) if isinstance(rows[0], dict) else ("row",)
        table_rows = []
        for row in rows:
            table_rows.append({field: row.get(field, "") for field in fields} if isinstance(row, dict) else {"row": row})
        output.append(render_status_table("Rows", table_rows, columns=fields))
    return "\n\n".join(output)


def _render_replay_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel

    if payload.get("status") and "returned_rows" not in payload:
        issues = payload.get("issues")
        issue_codes = []
        if isinstance(issues, list):
            for issue in issues:
                if isinstance(issue, dict):
                    issue_codes.append(str(issue.get("code") or issue.get("message") or issue))
                else:
                    issue_codes.append(str(issue))
        return render_key_value_panel(title, (
            ("Dataset", payload.get("dataset", "-")),
            ("Status", payload.get("status", "-")),
            ("Issues", ", ".join(issue_codes) if issue_codes else "-"),
            ("Next Command", payload.get("next_command", "-")),
        ))

    output = [render_key_value_panel(title, (
        ("Dataset", payload.get("dataset", "-")),
        ("Returned Rows", payload.get("returned_rows", "-")),
        ("Total Rows", payload.get("total_rows", "-")),
    ))]
    rows = payload.get("rows")
    if isinstance(rows, list) and rows:
        output.append("Rows")
        output.extend(json.dumps(to_primitive(row), ensure_ascii=False, sort_keys=True) for row in rows)
    return "\n".join(output)


def _render_data_protocol_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel, render_status_table

    protocols = payload.get("protocols")
    if isinstance(protocols, list):
        rows = [
            {
                "kind": item.get("kind", ""),
                "interface": item.get("interface", ""),
                "used_by": item.get("used_by", ""),
            }
            for item in protocols
            if isinstance(item, dict)
        ]
        return render_status_table(title, rows, columns=("kind", "interface", "used_by"))

    output = [render_key_value_panel(title, (
        ("Kind", payload.get("kind", "-")),
        ("Status", payload.get("status", "-")),
        ("Source", payload.get("source", payload.get("file", "-"))),
        ("Rows", payload.get("row_count", "-")),
        ("Next Command", payload.get("next_command", "-")),
    ))]
    checks = payload.get("checks")
    if isinstance(checks, list) and checks:
        rows = []
        for item in checks:
            if isinstance(item, dict):
                rows.append({
                    "name": item.get("name", ""),
                    "passed": item.get("passed", ""),
                    "value": item.get("value", ""),
                })
        output.append(render_status_table("Checks", rows, columns=("name", "passed", "value")))
    template = payload.get("template")
    if isinstance(template, str) and template:
        output.append(template.rstrip())
    issues = payload.get("issues")
    if isinstance(issues, list) and issues:
        rows = []
        for item in issues:
            if isinstance(item, dict):
                rows.append({
                    "code": item.get("code", ""),
                    "message": item.get("message", ""),
                })
        output.append(render_status_table("Issues", rows, columns=("code", "message")))
    return "\n\n".join(output)


def _render_acquisition_plan_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel, render_status_table

    estimate = payload.get("estimate") if isinstance(payload.get("estimate"), dict) else {}
    selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else {}
    requested = payload.get("requested") if isinstance(payload.get("requested"), dict) else {}
    missing = payload.get("missing") if isinstance(payload.get("missing"), list) else []
    rows = (
        ("Dataset", payload.get("logical_key", "-")),
        ("Provider", selected.get("provider", "-") if selected else "-"),
        ("Venue", selected.get("venue", "-") if selected else "-"),
        ("Provider Access", "available" if payload.get("connector_available") else "unavailable"),
        ("Complete", payload.get("complete", False)),
        ("Missing Ranges", len(missing)),
        ("Estimated Requests", estimate.get("requests", "-") if estimate else "-"),
        ("Estimated Instruments", estimate.get("instruments", "-") if estimate else "-"),
        ("Cost Class", estimate.get("cost_class", "-") if estimate else "-"),
    )
    output = [render_key_value_panel(title, rows)]
    tasks = payload.get("provider_tasks")
    if isinstance(tasks, dict) and tasks:
        task_rows = (
            ("Provider", tasks.get("provider", "-")),
            ("Task Type", tasks.get("task_type", "-")),
            ("Universe", tasks.get("universe", "-")),
            ("Symbols", tasks.get("symbols", "-")),
            ("Total Tasks", tasks.get("total_tasks", "-")),
            ("Cached Tasks", tasks.get("cached_tasks", "-")),
            ("Uncached Tasks", tasks.get("uncached_tasks", "-")),
            ("Resume Supported", tasks.get("resume_supported", "-")),
        )
        output.append(render_key_value_panel("Provider Task Plan", task_rows))
        ranges = tasks.get("ranges")
        if isinstance(ranges, list) and ranges:
            output.append(render_status_table(
                "Task Ranges",
                [item for item in ranges if isinstance(item, dict)],
                columns=("start", "end", "tasks", "cached", "uncached"),
            ))
        matrix = tasks.get("matrix")
        if isinstance(matrix, list) and matrix:
            output.append(render_status_table(
                "Task Matrix",
                [item for item in matrix if isinstance(item, dict)],
                columns=("year", "month", "tasks", "cached_monthly", "cached_daily_files"),
            ))
    elif isinstance(requested, dict):
        output.append(render_key_value_panel("Requested Window", (
            ("Start", requested.get("start", "-")),
            ("End", requested.get("end", "-")),
        )))
    return "\n\n".join(item for item in output if item)


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
    from kairospy.data.preparation import DataPreparationService
    from kairospy.analytics.features import UsEquityMomentumDiagnostics

    start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("US equity momentum preparation requires timezone-aware increasing [start,end) timestamps")
    register_default_products(args.lake_root)
    register_configured_products(args.lake_root)
    providers = default_provider_registry(
        args.lake_root,
        progress=None if args.quiet else None,
    )
    client = DatasetClient(args.lake_root, providers=providers)
    prepared_raw = []
    raw_release_paths = []
    for raw_dataset in args.raw_dataset:
        raw = DataPreparationService(client).prepare(
            raw_dataset,
            start=start,
            end=end,
            minimum_quality=QualityLevel.WORKSPACE,
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
    readiness = UsEquityMomentumDiagnostics(args.lake_root).report(workspace=args.workspace, version=args.version)
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
        "readiness": readiness,
        "ready_for_workspace": readiness["ready_for_workspace"],
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
        ("reference.identity.equity.us.massive@latest-workspace",),
        DatasetStatus.APPROVED_FOR_WORKSPACE,
        QualityLevel.WORKSPACE,
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

    archive = MassiveVendorArchiveClient(lake_root, MassiveClient(_massive_marketdata_config()))
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
        ("reference.corporate_actions.equity.us.massive@latest-workspace",),
        DatasetStatus.APPROVED_FOR_WORKSPACE,
        QualityLevel.WORKSPACE,
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


def _vol(args: argparse.Namespace) -> int:
    client = DatasetClient(args.lake_root, run_mode=RunMode.WORKSPACE)
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
        from kairospy.surface.data_features import SurfaceFeaturePublisher
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


def _capture_normalized_series(args: argparse.Namespace) -> int:
    environment = Environment(args.environment)
    repository = ReferenceCatalogRepository(args.reference_catalog_path)
    if not repository.path.exists():
        raise SystemExit("catalog is missing; run 'kairospy catalog sync' first")
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
    from kairospy.reference import ReferenceCatalog
    from kairospy.reference.repository import ReferenceCatalogRepository
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
    from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore

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


def _accounts(args: argparse.Namespace) -> int:
    from kairospy.infrastructure.configuration import ConfigError, KairosProjectConfig
    from kairospy.integrations.config import CredentialResolver, resolve_account_binding

    try:
        config = KairosProjectConfig.discover(Path.cwd())
        binding = resolve_account_binding(config, args.account)
    except ConfigError as error:
        payload = {
            "product": "accounts",
            "operation": "doctor",
            "account": args.account,
            "status": "unknown_account",
            "issues": [{"code": "unknown_account", "message": str(error)}],
        }
        _emit_accounts_payload(args, payload)
        return 2
    resolver = CredentialResolver(config)
    credential_fields = _account_credential_fields(binding.credential, config.get(f"credentials.{binding.credential}", {}))
    credential_refs = []
    issues = []
    for field in credential_fields:
        ref = resolver.field(binding.credential, field)
        provided = ref.resolved not in (None, "")
        credential_refs.append({
            "credential": binding.credential,
            "field": field,
            "source": ref.source,
            "provided": provided,
        })
        if not provided:
            issues.append({
                "code": "missing_credential",
                "field": field,
                "source": ref.source,
            })
    if not binding.account_ref:
        issues.append({"code": "missing_account_ref"})
    if not binding.provider:
        issues.append({"code": "missing_provider"})
    if not binding.environment:
        issues.append({"code": "missing_environment"})
    if not binding.permissions:
        issues.append({"code": "missing_permissions"})
    payload = {
        "product": "accounts",
        "operation": "doctor",
        "account": binding.name,
        "status": "available" if not issues else "needs_configuration",
        "account_ref": binding.account_ref,
        "provider": binding.provider,
        "environment": binding.environment,
        "permissions": list(binding.permissions),
        "allowed_products": list(binding.allowed_products),
        "capital_scope": binding.capital_scope,
        "credential": binding.credential,
        "credential_refs": credential_refs,
        "checks": {
            "account_binding": bool(binding.account_ref and binding.provider and binding.environment),
            "credentials": not any(item["code"] == "missing_credential" for item in issues),
            "permissions": bool(binding.permissions),
            "account_query": "not_run",
        },
        "issues": issues,
    }
    _emit_accounts_payload(args, payload)
    return 0 if not issues else 2


def _account_credential_fields(name: str, raw: object) -> tuple[str, ...]:
    if isinstance(raw, dict):
        explicit = tuple(
            field for field in ("api_key", "api_secret", "passphrase", "host", "port", "client_id")
            if field in raw
        )
        if explicit:
            return explicit
        kind = str(raw.get("kind") or "")
        if kind == "api_key_secret_passphrase":
            return ("api_key", "api_secret", "passphrase")
        if kind == "api_key_secret":
            return ("api_key", "api_secret")
    if name.startswith("ibkr_"):
        return ("host", "port", "client_id")
    return ("api_key", "api_secret")


def _emit_accounts_payload(args: argparse.Namespace, payload: dict[str, object]) -> None:
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"Account: {payload.get('account')}")
    print(f"Status: {payload.get('status')}")
    if payload.get("account_ref"):
        print(f"Account Ref: {payload.get('account_ref')}")
    if payload.get("provider"):
        print(f"Provider: {payload.get('provider')}")
    for issue in payload.get("issues", ()):
        if isinstance(issue, dict):
            print(f"Issue: {issue.get('code')}")


def _runtime_l4_preflight(args: argparse.Namespace) -> dict[str, object]:
    import socket
    environment = Environment(args.environment)
    compatible_environment = (
        args.venue == "binance" and environment is Environment.TESTNET
        or args.venue == "ibkr" and environment is Environment.PAPER
    )
    strategy_id = str(args.strategy)
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
        try:
            _credentials(Environment.TESTNET)
            external_ready = True
            external_reason = "Binance testnet credential resolved from Kairos project config"
        except SystemExit as error:
            external_ready = False
            external_reason = str(error)
    else:
        host, port, _client_id = _ibkr_connection_settings()
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


def _submit_order_or_runtime_soak(args: argparse.Namespace) -> int:
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
    strategy_id = "manual-operations-v1" if manual_order else str(args.strategy)
    if manual_order:
        print(f"Manual operations intent: actor={args.actor} reason={args.reason}")
    catalog_repository = ReferenceCatalogRepository(args.reference_catalog_path)
    if not catalog_repository.path.exists():
        raise SystemExit("catalog is missing; run 'kairospy catalog sync' first")
    catalog = catalog_repository.load()
    definition = catalog.instruments.get(InstrumentId(args.instrument), datetime.now(timezone.utc))
    runtime_store, runtime_path = _authoritative_runtime_store(args)
    ledger = runtime_store.load_ledger()
    listings = catalog.active_listings(definition.instrument_id, datetime.now(timezone.utc))
    venue = listings[0].venue_id if args.venue == "simulated" else VenueId(args.venue)
    account = AccountRef(InstitutionId(args.venue), args.account_id, _account_type(args.product))
    if args.venue == "simulated":
        balances, positions = _local_state(ledger, account)
        execution_gateway = SimulatedExecutionAccountGateway(venue, account, balances, positions, environment)
        market_ready = True
    else:
        execution_gateway = _execution_account_gateway(args.venue, environment, args.product, definition, catalog, args.inverse)
        market_ready = args.market_data_ready
    reconciliation = ReconciliationService(ledger, execution_gateway, runtime_store=runtime_store)
    event_log = PersistentEventLog(args.event_log_path)
    from kairospy.execution.ingestion import DurableExecutionIngestionService
    from kairospy.execution.recovery import VenueOrderRecoveryService
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
    from kairospy.runtime.application import FunctionProbe, KairosApplication
    from kairospy.runtime.config import ApplicationConfig, RuntimePaths
    from kairospy.runtime.recovery import RuntimeRecoveryService
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
        from kairospy.runtime.supervisor import RecoveryBackgroundService, RuntimeSupervisor
        from kairospy.governance.observability import OperationalMonitor
        background_services = [RecoveryBackgroundService(order_recovery)] if order_recovery is not None else []
        if args.venue == "ibkr" and order_recovery is not None:
            from kairospy.integrations.connectors.ibkr.ingestion import IbkrDurableFillIngestion
            execution = getattr(execution_gateway, "execution", None)
            session = getattr(execution, "session", None)
            if session is not None:
                background_services = [IbkrDurableFillIngestion(session, order_recovery)]
        if args.venue == "binance" and args.product == "futures":
            from kairospy.integrations.connectors.binance.funding_settlement import BinanceFundingSettlementClient
            from kairospy.integrations.connectors.binance.funding_ingestion import BinanceDurableFundingBackfill
            from kairospy.execution.ingestion import DurableAccountingIngestionService
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
            from kairospy.runtime.supervisor import write_soak_artifact
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
    host, port, client_id = _ibkr_connection_settings()
    return IbkrSession(host, port, client_id, readonly)


def _ibkr_connection_settings() -> tuple[str, int, int]:
    from kairospy.infrastructure.configuration import load_project_config_or_none
    from kairospy.integrations.config import resolve_ibkr_trading_connection

    config = load_project_config_or_none()
    settings = resolve_ibkr_trading_connection(config)
    return settings.host, settings.port, settings.client_id


def _credentials(environment: Environment) -> tuple[str, str]:
    from kairospy.infrastructure.configuration import ConfigError, load_project_config_or_none
    from kairospy.integrations.config import resolve_binance_trading_credentials

    config = load_project_config_or_none()
    config_environment = "testnet" if environment is Environment.TESTNET else "live"
    if config is None:
        raise SystemExit("missing Kairos project config for Binance trading credentials")
    try:
        credentials = resolve_binance_trading_credentials(config, config_environment)
    except ConfigError as error:
        raise SystemExit(str(error)) from error
    return credentials.api_key, credentials.api_secret


def _account_gateway(venue: str, environment: Environment, account: AccountRef, ledger, product: str, catalog, inverse: bool):
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


def _account_key(venue: str, account_id: str, product: str) -> AccountRef:
    return AccountRef(InstitutionId(venue), account_id, _account_type(product))


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
