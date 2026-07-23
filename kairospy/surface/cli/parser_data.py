from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path
from typing import Callable

from kairospy import __version__
from kairospy.data.contracts import QualityLevel


def add_data_commands(
    commands,
    *,
    positive_int: Callable[[str], int],
    hide_subcommands: Callable[[object, set[str]], None],
    add_acquisition_limit_args: Callable[[argparse.ArgumentParser], None],
) -> None:
    _positive_int = positive_int
    _hide_subcommands = hide_subcommands
    _add_acquisition_limit_args = add_acquisition_limit_args
    data = commands.add_parser("data", help="manage Dataset Store data and built-in Data products")
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
    data_start.add_argument("--as", dest="as_dataset", help="Dataset ID for user file or connector sources")
    data_start.add_argument("--time", help="primary time field")
    data_start.add_argument("--start", dest="start_time", help="historical start timestamp")
    data_start.add_argument("--end", dest="end_time", help="historical end timestamp")
    data_start.add_argument("--account", help="account or credential reference for live data")
    data_start.add_argument("--instrument", action="append", default=[], help="instrument id or provider symbol; repeat as needed")
    data_start.add_argument("--channel", help="live channel name")
    data_start.add_argument("--market", help="provider market selector, for example spot, usdm, stocks, or perpetual")
    data_start.add_argument("--levels", type=int, choices=(5, 10, 20), help="order book depth levels for Binance orderbook")
    data_start.add_argument("--interval", help="provider interval selector")
    data_start.add_argument("--for", dest="for_use", choices=("workspace", "backtest", "shadow", "paper", "live"),
                            help="target use; defaults to workspace for historical data and shadow for live data")
    data_resolve = data_actions.add_parser("resolve", help="explain a Data Stream and its compatible Dataset/Product plan")
    data_resolve.add_argument("stream", help="Data Stream ID, Dataset ID, alias, or legacy Data Product key")
    data_import = data_actions.add_parser("import", help="import a file into a Data Stream")
    data_import.add_argument("stream", help="Data Stream ID to publish, for example my_research.momentum_1h")
    data_import.add_argument("source", type=Path, help="CSV file or user connector path")
    data_import.add_argument("--time", help="primary time field; inferred when omitted")
    data_read = data_actions.add_parser("read", help="read one Data Stream or a stream pattern")
    data_read.add_argument("stream", help="Data Stream ID, Dataset ID, alias, or glob pattern")
    data_read.add_argument("--start", help="inclusive ISO-8601 timestamp with timezone")
    data_read.add_argument("--end", help="exclusive ISO-8601 timestamp with timezone")
    data_read.add_argument("--field", action="append", default=[], help="field to return; repeatable")
    data_read.add_argument("--time", dest="time_field", help="override primary time field")
    data_read.add_argument("--limit", type=_positive_int, default=20)
    replace_stream_data = data_actions.add_parser("replace-window", help="replace historical rows inside one Data Stream time window")
    replace_stream_data.add_argument("stream", help="Data Stream ID, Dataset ID, or alias")
    replace_stream_data.add_argument("source", type=Path, help="CSV or Parquet replacement rows")
    replace_stream_data.add_argument("--start", required=True, help="inclusive ISO-8601 timestamp with timezone")
    replace_stream_data.add_argument("--end", required=True, help="exclusive ISO-8601 timestamp with timezone")
    replace_stream_data.add_argument("--time", dest="time_field", help="override primary time field")
    data_get = data_actions.add_parser("get", help="prepare historical data for a Data Stream")
    data_get.add_argument("stream", help="Data Stream ID, for example hyperliquid_perp_btc.ohlcv_1h")
    data_get.add_argument("--start", required=True, help="inclusive ISO-8601 timestamp with timezone")
    data_get.add_argument("--end", required=True, help="exclusive ISO-8601 timestamp with timezone")
    data_get.add_argument("--refresh", action="store_true")
    data_get.add_argument("--dry-run", action="store_true", help="show acquisition plan without downloading")
    data_probe = data_actions.add_parser("probe", help="probe a live Data Stream source")
    data_probe.add_argument("stream", help="Data Stream ID, for example binance_swap_btcusdt.orderbook")
    data_probe.add_argument("--limit", type=_positive_int, default=5, help="maximum rows to sample")
    data_probe.add_argument("--dry-run", action="store_true", help="show live source plan without connecting")
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
    data_connect.add_argument("--as", dest="as_dataset", help="Dataset ID for user LiveDataProtocol connector paths; not accepted for built-in products")
    data_connect.add_argument("--protocol", choices=("live",), default="live")
    data_connect.add_argument("--time", default="timestamp", help="primary time field; defaults to timestamp")
    data_connect.add_argument("--account", help="account or credential reference for this live source")
    data_connect.add_argument("--instrument", action="append", default=[], help="instrument id or provider symbol; repeat for a bounded universe")
    data_connect.add_argument("--channel", help="live channel name")
    data_connect.add_argument("--market", help="provider market selector, for example spot, usdm, stocks, or perpetual")
    data_connect.add_argument("--levels", type=int, choices=(5, 10, 20), help="order book depth levels for Binance orderbook")
    data_connect.add_argument("--interval", help="provider interval selector")
    data_connect.add_argument("--for", dest="for_use", choices=("shadow", "paper", "live"),
                              default="shadow", help="target use; defaults to shadow")
    data_connect.add_argument("--freshness-seconds", type=float, default=5.0, help="freshness max age required before paper/live use")
    data_sample = data_actions.add_parser("sample", help="sample rows from a live Data source")
    data_sample.add_argument("source", help="built-in live source key or user LiveDataProtocol connector path")
    data_sample.add_argument("--as", dest="as_dataset", help="temporary Dataset name for sample context")
    data_sample.add_argument("--instrument", action="append", default=[], help="instrument id or provider symbol; repeat as needed")
    data_sample.add_argument("--channel", help="live channel name")
    data_sample.add_argument("--market", help="provider market selector")
    data_sample.add_argument("--levels", type=int, choices=(5, 10, 20), help="order book depth levels for Binance orderbook")
    data_sample.add_argument("--interval", help="provider interval selector")
    data_sample.add_argument("--limit", type=_positive_int, default=5, help="maximum rows to sample")
    data_reconnect = data_actions.add_parser("reconnect", help="reconnect a configured live Dataset")
    data_reconnect.add_argument("dataset", help="Dataset name to reconnect")
    data_reconnect.add_argument("--account", help="override account or credential reference")
    data_reconnect.add_argument("--instrument", action="append", default=[], help="override instruments; repeat for a bounded universe")
    data_reconnect.add_argument("--channel", help="override live channel name")
    data_reconnect.add_argument("--market", help="override provider market selector")
    data_reconnect.add_argument("--levels", type=int, choices=(5, 10, 20), help="override order book depth levels for Binance orderbook")
    data_reconnect.add_argument("--interval", help="override provider interval selector")
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
        help="Live View manifest to update with soak channel diagnostics",
    )
    inspect_data = data_actions.add_parser("inspect", help="show schema, lineage and time coverage")
    inspect_data.add_argument("--dataset", required=True)
    list_data = data_actions.add_parser("list", help="list Datasets and readiness")
    list_data.add_argument("--dimension", action="append", default=[], help="key=value dimension; repeatable")
    releases_data = data_actions.add_parser("releases", help="removed: Data no longer has releases or versions")
    releases_data.add_argument("--dataset", help="Dataset ID or alias")
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
    diagnostics_data = data_actions.add_parser("diagnostics", help="diagnose Dataset Store entries")
    diagnostics_data.add_argument("--strict", action="store_true", help="return non-zero when errors exist")
    repair_index_data = data_actions.add_parser("repair-index", help="rebuild the optional Dataset Store index cache")
    repair_index_data.add_argument("--strict", action="store_true", help=argparse.SUPPRESS)
    clean_tmp_data = data_actions.add_parser("clean-tmp", help="remove Dataset Store temporary write directories")
    clean_tmp_data.add_argument("--dataset", help="Dataset ID or alias; defaults to all Datasets")
    clean_tmp_data.add_argument("--stream", help="Data Stream ID or alias; defaults to all Streams")
    delete_stream_data = data_actions.add_parser("delete-stream-data", help="delete historical rows from one Data Stream")
    delete_stream_data.add_argument("stream", help="Data Stream ID, Dataset ID, or alias")
    delete_stream_data.add_argument("--start", help="inclusive ISO-8601 timestamp with timezone")
    delete_stream_data.add_argument("--end", help="exclusive ISO-8601 timestamp with timezone")
    delete_stream_data.add_argument("--time", dest="time_field", help="override primary time field")
    delete_stream_data.add_argument("--all", dest="all_data", action="store_true", help="delete all historical data for the stream")
    us_equity_diagnostics = data_actions.add_parser("us-equity-momentum-diagnostics", help="audit the local US equity momentum data package")
    us_equity_diagnostics.add_argument("--workspace", default="us-equity-momentum")
    us_equity_diagnostics.add_argument("--version", default="1.0.0")
    us_equity_diagnostics.add_argument("--strict", action="store_true", help="return non-zero when diagnostics errors exist")
    validate_data = data_actions.add_parser("validate", help="inspect whether a Dataset has readable historical or live data")
    validate_data.add_argument("dataset_arg", nargs="?", help="Dataset name to validate")
    validate_data.add_argument("--dataset", dest="dataset", help="Dataset name to validate; kept for compatibility")
    prepare_data = data_actions.add_parser("prepare", help="removed: provider products now own ingestion")
    prepare_data.add_argument("--dataset", required=True)
    prepare_data.add_argument("--start", required=True)
    prepare_data.add_argument("--end", required=True)
    prepare_data.add_argument("--quality", choices=tuple(item.value for item in QualityLevel), default=QualityLevel.WORKSPACE.value)
    prepare_data.add_argument("--provider")
    prepare_data.add_argument("--venue")
    prepare_data.add_argument("--acquire-missing", action="store_true")
    _add_acquisition_limit_args(prepare_data)
    prepare_data.add_argument("--promote", action="store_true", help="removed; retained only so the removed command can explain the migration")
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
    freeze_data = data_actions.add_parser("freeze", help="removed: Dataset Store does not freeze workspace inputs")
    freeze_data.add_argument("--workspace", required=True)
    freeze_data.add_argument("--dataset", action="append", required=True)
    freeze_data.add_argument("--output", type=Path, required=True)
    freeze_data.add_argument("--code-version", default=__version__)
    compare_data = data_actions.add_parser("compare", help="removed: Data no longer has immutable releases")
    compare_data.add_argument("--first", required=True)
    compare_data.add_argument("--second", required=True)
    audit_artifact = data_actions.add_parser("audit-artifact", help="removed: release artifact audit is outside Data")
    audit_artifact.add_argument("--artifact", type=Path, required=True)
    audit_data = data_actions.add_parser("audit", help="removed: Dataset Store has no audit gate")
    audit_data.add_argument("dataset", help="Dataset name to audit")
    audit_data.add_argument("--verbose", action="store_true", help="ignored; Dataset Store has no hash audit gate")
    alias_data = data_actions.add_parser("alias", help="create a Dataset alias")
    alias_data.add_argument("dataset_arg", nargs="?", help="canonical Dataset ID")
    alias_data.add_argument("--dataset", dest="dataset", help="canonical Dataset ID")
    alias_data.add_argument("--alias", required=True)
    catalog_data = data_actions.add_parser("catalog", help="list Dataset Store entries")
    catalog_data.add_argument("--refresh", action="store_true", help="ignored; Dataset Store is discovered from files")
    copy_data = data_actions.add_parser("copy", help="removed: copy Dataset files directly or rebuild from provider ingestion")
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
    for action, help_text in (("plan", "removed: provider products now own ingestion planning"),
                              ("acquire", "removed: provider products now own ingestion")):
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
