from __future__ import annotations

import argparse
from decimal import Decimal
import os
from pathlib import Path
import sys

from kairospy.analytics.pricing import PricingModel
from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT
from kairospy.reference.contracts import OptionRight
from kairospy.surface.cli.parser_data import add_data_commands
from kairospy.surface.cli.parser_run import add_run_commands


def _program_name() -> str:
    executable = Path(sys.argv[0]).name
    if executable in {"kairospy", "kairos"}:
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


def build_parser() -> argparse.ArgumentParser:
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
    workspace_actions.add_parser("list", help="list project Workspaces")
    workspace_create = workspace_actions.add_parser("create", help="create or open one Workspace")
    workspace_create.add_argument("name")
    workspace_attach = workspace_actions.add_parser("attach", aliases=("add",), help="attach a Data Stream to a Workspace")
    workspace_attach.add_argument("workspace")
    workspace_attach.add_argument("stream_arg", nargs="?", help="Data Stream ID; accepted by `workspace add <workspace> <stream>`")
    workspace_attach.add_argument("--name", help="workspace-local attachment name; defaults to the stream id")
    workspace_attach.add_argument("--stream", help="Data Stream ID")
    workspace_attach.add_argument("--dataset", help="Dataset ID or alias; compatibility path")
    workspace_attach.add_argument("--view", choices=("history", "live", "both"), default="both")
    workspace_attach.add_argument("--instrument", action="append", default=[], help="instrument selector; repeatable")
    workspace_attach.add_argument("--field", action="append", default=[], help="field selector; repeatable")
    workspace_attach.add_argument("--freshness-seconds", type=float)
    workspace_inspect = workspace_actions.add_parser("inspect", help="inspect Workspace bindings")
    workspace_inspect.add_argument("name")
    workspace_inspect_code = workspace_actions.add_parser("inspect-code", help="inspect a Workspace code entrypoint")
    workspace_inspect_code.add_argument("entrypoint", help="module:callable returning a WorkspaceProjection")
    workspace_inspect_code.add_argument("--param", action="append", default=[], help="workspace parameter key=value; repeatable")
    workspace_inspect_code.add_argument("--mode", choices=("inspect", "backtest", "historical-simulation", "paper", "live"), default="inspect")
    add_data_commands(
        commands,
        positive_int=_positive_int,
        hide_subcommands=_hide_subcommands,
        add_acquisition_limit_args=_add_acquisition_limit_args,
    )
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

    add_run_commands(commands)
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
