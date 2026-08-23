#!/usr/bin/env python3
"""Validate CLI product-boundary rules from docs/architecture/cli-boundary.md."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

CLI_COMMANDS = ROOT / "kairospy" / "surface" / "cli" / "commands"


@dataclass(frozen=True)
class CliBoundary:
    product_name: str
    python_command: str | None
    rust_cli: Path | None
    rust_cli_must_be_grouped: bool
    system_component: str | None
    launch_component: str | None
    cli_application_required: bool
    connected_application_required: bool


BUSINESS_BOUNDARIES = (
    CliBoundary(
        product_name="Account",
        python_command="account",
        rust_cli=ROOT
        / "crates"
        / "modules"
        / "account"
        / "src"
        / "bin"
        / "kairos-account-cli.rs",
        rust_cli_must_be_grouped=True,
        system_component=None,
        launch_component="account",
        cli_application_required=True,
        connected_application_required=True,
    ),
    CliBoundary(
        product_name="Market",
        python_command="market",
        rust_cli=ROOT
        / "crates"
        / "modules"
        / "market"
        / "src"
        / "bin"
        / "kairos-market-cli.rs",
        rust_cli_must_be_grouped=True,
        system_component="market",
        launch_component="market",
        cli_application_required=True,
        connected_application_required=True,
    ),
    CliBoundary(
        product_name="Reference",
        python_command="reference",
        rust_cli=ROOT
        / "crates"
        / "modules"
        / "reference"
        / "src"
        / "bin"
        / "kairos-reference-cli.rs",
        rust_cli_must_be_grouped=True,
        system_component="reference",
        launch_component="reference",
        cli_application_required=True,
        connected_application_required=True,
    ),
    CliBoundary(
        product_name="Execution",
        python_command="order",
        rust_cli=ROOT
        / "crates"
        / "modules"
        / "execution"
        / "src"
        / "bin"
        / "kairos-execution-cli.rs",
        rust_cli_must_be_grouped=True,
        system_component=None,
        launch_component="execution",
        cli_application_required=True,
        connected_application_required=True,
    ),
    CliBoundary(
        product_name="Risk",
        python_command="risk",
        rust_cli=ROOT
        / "crates"
        / "modules"
        / "risk"
        / "src"
        / "bin"
        / "kairos-risk-cli.rs",
        rust_cli_must_be_grouped=True,
        system_component="risk",
        launch_component="risk",
        cli_application_required=True,
        connected_application_required=True,
    ),
    CliBoundary(
        product_name="Capital",
        python_command="capital",
        rust_cli=ROOT
        / "crates"
        / "modules"
        / "capital"
        / "src"
        / "bin"
        / "kairos-capital-cli.rs",
        rust_cli_must_be_grouped=True,
        system_component="capital",
        launch_component="capital",
        cli_application_required=True,
        connected_application_required=True,
    ),
)

DECLARED_PYTHON_COMMANDS = {
    boundary.python_command
    for boundary in BUSINESS_BOUNDARIES
    if boundary.python_command is not None
}

FORBIDDEN_STANDALONE_IMPORTS = (
    "ComponentProcessApplication",
    "MarketSystemClient",
    "ReferenceSystemClient",
    "AccountSystemClient",
)

FORBIDDEN_ROOT_PRIVATE_BUSINESS_TOKENS = (
    "\naccount_app = typer.Typer",
    "\nmarket_app = typer.Typer",
    "\norder_app = typer.Typer",
    "system_account_app",
    "Private account command registry",
    "Private market command registry",
    "Order commands",
    "AccountAdminApplication",
    "AccountCliApplication",
    "CredentialApplication",
    "TradeLeaseApplication",
    "MarketCliApplication",
    "MarketDataApplication",
    "order_place",
    "order_cancel",
    "order_replace",
    "_execution_submit_args",
    "_order_query_action",
    "_socket_action",
)

REQUIRED_RUST_MODE_TOKENS = (
    "Standalone(",
    "Connected(",
    "enum StandaloneCommand",
    "enum ConnectedCommand",
)

FORBIDDEN_RUST_FLAT_MODE_TOKENS = (
    "command => command",
    "Self::Standalone(command) => command.into(),\n            Self::Connected(command) => command.into(),\n            command => command",
)

FORBIDDEN_RUST_MODE_NORMALIZATION_TOKENS = (
    "fn normalize(self)",
    "impl From<StandaloneCommand>",
    "impl From<ConnectedCommand>",
)

FORBIDDEN_RUST_RUNTIME_ASSEMBLY_BY_PRODUCT = {
    "Account": (
        "Conflux::new(",
        "AccountRpcService",
        "compose_binance_async_account_application",
        "compose_okx_async_account_application",
        "compose_ibkr_async_account_application",
        "compose_local_account_application_for_segments",
    ),
}

REQUIRED_RUST_CLI_APP_CALLS_BY_PRODUCT = {
    "Account": (
        ".list_accounts()",
        ".browse_accounts(",
        ".show_account(",
        ".switch_account_model(",
        ".register_account(",
        ".modify_account(",
        ".simulate_account(",
        ".remove_account(",
        ".list_credentials()",
        ".bind_credential_with_probe(",
        ".connect_account_from_provider(",
        ".create_credential(",
        ".delete_credential(",
        ".show_credential(",
        ".schemas()",
        ".schema(",
        ".doctor(",
        ".local_snapshot(",
        ".balances(",
        ".positions(",
        ".open_orders(",
    ),
    "Market": (
        ".validate_market(",
        ".reference_universe(",
        ".once(",
        ".replay(",
        ".download_historical(",
    ),
    "Reference": (
        ".catalog_status()",
        ".catalog_collection(",
        ".list_assets(",
        ".show_asset(",
        ".participant_entities(",
        ".markets(",
        ".option_chain(",
        ".lifecycle_events(",
        ".query(",
        ".search(",
        ".show_catalog_record(",
    ),
    "Risk": (
        ".schema(",
        ".doctor(",
        ".preview(",
    ),
    "Capital": (
        ".schema(",
        ".doctor(",
        ".preview(",
        ".plan(",
    ),
    "Execution": (
        ".open_orders(",
        ".history(",
        ".order(",
        ".fills(",
        ".submit(",
        ".cancel(",
        ".replace(",
    ),
}

FORBIDDEN_GENERIC_RUST_COMMAND_ENUMS = {
    "Account": ("enum AccountCommand",),
    "Execution": ("enum ExecutionCommand",),
    "Reference": ("enum ReferenceCommand",),
    "Risk": ("enum RiskCommand",),
    "Capital": ("enum CapitalCommand",),
}

FORBIDDEN_RUST_BIN_ACCOUNT_CLI_TOKENS = (
    "app.registry",
    "app.credential_store",
    "fn credential_probe_options",
    "inspect_account_credential",
    "default_rest_endpoint",
    "AccountOptions",
)

REQUIRED_ACCOUNT_CLI_GUARD_TOKENS = (
    "fn local_query_account(",
    "only support local paper/simulated accounts for now",
    "direct provider query service or a connected component",
)


def cli_application_name(product_name: str) -> str:
    return f"Cli{product_name}Application"


def connected_application_name(product_name: str) -> str:
    return f"Connected{product_name}Application"


def module_root(product_name: str) -> Path:
    name = "execution" if product_name == "Execution" else product_name.lower()
    return ROOT / "crates" / "modules" / name

FORBIDDEN_STANDALONE_VARIANTS_BY_PRODUCT = {
    "Account": {
        "Fill",
        "Refresh",
        "Reconcile",
    },
    "Execution": {
        "Backtest",
        "Snapshot",
        "Routes",
        "Orders",
        "Reconcile",
        "UnknownRemoteOrders",
        "LinkUnknown",
        "Status",
        "Events",
        "Trace",
        "Fill",
        "Inspect",
        "Journal",
        "Audit",
        "PreviewSubmit",
        "PreviewSubmitFile",
        "PreviewCancel",
        "PreviewCancelFile",
        "PreviewReplace",
        "PreviewReplaceFile",
    },
    "Risk": {
        "Status",
        "Snapshot",
        "PreTradeCheck",
        "AuthorizeReserve",
        "Reserve",
        "Release",
        "Consume",
        "Resize",
        "OpenCircuit",
        "CloseCircuit",
        "PublishPolicy",
        "AdvanceTime",
    },
    "Capital": {
        "Status",
        "Health",
        "Current",
        "Availabilities",
        "Availability",
        "Alerts",
        "Transfer",
        "PublishFundingObjective",
        "CancelFundingObjective",
        "ObserveDemand",
        "ObserveCapitalDemand",
        "ReconcilePlan",
    },
}

FORBIDDEN_CONNECTED_VARIANTS_BY_PRODUCT = {
    "Execution": {
        "Fill",
        "LinkUnknown",
    },
    "Risk": {
        "Status",
        "Snapshot",
    },
}


def _enum_body(text: str, enum_name: str) -> str | None:
    marker = f"enum {enum_name} {{"
    start = text.find(marker)
    if start < 0:
        return None
    index = start + len(marker)
    depth = 1
    while index < len(text):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + len(marker) : index]
        index += 1
    return None


def _top_level_variants(enum_body: str) -> set[str]:
    variants: set[str] = set()
    depth = 0
    pending_attrs = False
    for raw_line in enum_body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("#["):
            pending_attrs = True
            continue
        if depth == 0:
            token = line.split("{", 1)[0].split("(", 1)[0].split(",", 1)[0].strip()
            if token and not token.startswith("#"):
                variants.add(token)
                pending_attrs = False
        depth += raw_line.count("{") - raw_line.count("}")
        if depth == 0 and line.endswith(","):
            pending_attrs = False
    del pending_attrs
    return variants


def main() -> int:
    failures: list[str] = []

    for command_file in CLI_COMMANDS.glob("*.py"):
        if command_file.stem in {"__init__", "app", "data", "integration", "launch", "research", "root"}:
            continue
        if command_file.stem not in DECLARED_PYTHON_COMMANDS:
            failures.append(
                f"undeclared top-level business CLI file: {command_file.relative_to(ROOT)}"
            )

    for boundary in BUSINESS_BOUNDARIES:
        module = module_root(boundary.product_name)
        cli_facade = module / "src" / "application" / "cli.rs"
        cli_app = cli_application_name(boundary.product_name)
        if boundary.cli_application_required:
            if not cli_facade.exists():
                failures.append(
                    f"{boundary.product_name} is missing standalone CLI facade: "
                    f"{cli_facade.relative_to(ROOT)}"
                )
            else:
                cli_facade_text = cli_facade.read_text(encoding="utf-8")
                if f"struct {cli_app}" not in cli_facade_text and f"pub struct {cli_app}" not in cli_facade_text:
                    failures.append(
                        f"{cli_facade.relative_to(ROOT)} must define {cli_app}"
                    )
                if boundary.product_name == "Account":
                    for token in REQUIRED_ACCOUNT_CLI_GUARD_TOKENS:
                        if token not in cli_facade_text:
                            failures.append(
                                f"{cli_facade.relative_to(ROOT)} must guard "
                                "standalone live account queries until an "
                                f"Account-owned direct provider query service exists: {token}"
                            )

        connected_facade = module / "src" / "application" / "connected.rs"
        connected_app = connected_application_name(boundary.product_name)
        if boundary.connected_application_required:
            if not connected_facade.exists():
                failures.append(
                    f"{boundary.product_name} is missing runtime connected facade: "
                    f"{connected_facade.relative_to(ROOT)}"
                )
            else:
                connected_facade_text = connected_facade.read_text(encoding="utf-8")
                if (
                    f"struct {connected_app}" not in connected_facade_text
                    and f"pub struct {connected_app}" not in connected_facade_text
                    and f"type {connected_app}" not in connected_facade_text
                    and f"pub type {connected_app}" not in connected_facade_text
                ):
                    failures.append(
                        f"{connected_facade.relative_to(ROOT)} must define {connected_app}"
                    )

        if boundary.python_command is None:
            continue
        path = CLI_COMMANDS / f"{boundary.python_command}.py"
        if not path.exists():
            failures.append(
                f"{boundary.product_name} declares kairos {boundary.python_command}, "
                f"but {path.relative_to(ROOT)} does not exist"
            )
            continue
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_STANDALONE_IMPORTS:
            if token in text:
                failures.append(
                    f"business standalone CLI imports runtime component adapter "
                    f"{token}: {path.relative_to(ROOT)}"
                )
        if boundary.product_name == "Execution":
            for token in (
                "DIRECT_COMMANDS",
                '"open-orders"',
                '"history"',
                '"order"',
                '"fills"',
                '"submit"',
                '"cancel"',
                '"replace"',
                'arguments[0] in {"standalone", "connected"}',
            ):
                if token not in text:
                    failures.append(
                        f"{path.relative_to(ROOT)} must expose only account-scoped "
                        f"provider-direct order commands and reject explicit modes: {token}"
                    )
        if boundary.product_name == "Account":
            for token in (
                "balances",
                "positions",
                "open-orders",
                "AccountCliApplication",
                "[*account_selector, explicit_mode, *(arguments or",
            ):
                if token not in text:
                    failures.append(
                        f"{path.relative_to(ROOT)} must pass Account standalone "
                        f"local/direct queries through the owner CLI: {token}"
                    )
        if boundary.product_name == "Market":
            for token in (
                '"status"',
                '"sources"',
                '"snapshot"',
                '"subscribe"',
                '"unsubscribe"',
                '"recover"',
            ):
                if token not in text:
                    failures.append(
                        f"{path.relative_to(ROOT)} must reject connected Market "
                        f"runtime command from the standalone surface: {token}"
                    )
        if boundary.product_name == "Reference":
            for token in (
                '"status"',
                '"doctor"',
                '"logs"',
                '"coverage"',
                '"options-coverage"',
                "ReferenceCliApplication",
                "reference_passthrough",
                "CATALOG_MUTATION_COMMANDS",
                "mutates the Reference catalog",
            ):
                if token not in text:
                    failures.append(
                        f"{path.relative_to(ROOT)} must reject Reference option "
                        "coverage from the standalone surface"
                    )
            if "ReferenceClient" in text or "@reference_app.command" in text:
                failures.append(
                    f"{path.relative_to(ROOT)} must not implement a second "
                    "Reference catalog reader; top-level Reference uses the "
                    "owner Rust CLI passthrough"
                )
        if boundary.product_name == "Risk":
            for token in (
                "CONNECTED_COMMANDS",
                '"authorize-reserve"',
                '"publish-policy"',
                "schema",
                "doctor",
                "preview",
                "NativeCliApplication",
                '"risk", ["standalone"',
            ):
                if token not in text:
                    failures.append(
                        f"{path.relative_to(ROOT)} must expose only Risk "
                        f"standalone owner CLI passthrough: {token}"
                    )
        if boundary.product_name == "Capital":
            for token in (
                "CONNECTED_COMMANDS",
                '"publish-funding-objective"',
                '"transfer"',
                "schema",
                "doctor",
                "NativeCliApplication",
                '"capital", ["standalone"',
            ):
                if token not in text:
                    failures.append(
                        f"{path.relative_to(ROOT)} must expose only Capital "
                        f"standalone owner CLI passthrough: {token}"
                    )

    root_text = (CLI_COMMANDS / "root.py").read_text(encoding="utf-8")
    for token in FORBIDDEN_ROOT_PRIVATE_BUSINESS_TOKENS:
        if token in root_text:
            failures.append(f"root.py contains private business CLI token: {token}")
    launch_text = (CLI_COMMANDS / "launch.py").read_text(encoding="utf-8")
    for text, path in (
        (root_text, CLI_COMMANDS / "root.py"),
        (launch_text, CLI_COMMANDS / "launch.py"),
    ):
        if (
            "Account current projection does not expose open orders" in text
            or '"status": "unsupported"' in text
            and "open_orders" in text
        ):
            failures.append(
                f"{path.relative_to(ROOT)} must not expose Account open-orders "
                "as an unsupported half-entry; use observed-orders projection"
            )
    native_text = (ROOT / "kairospy" / "application" / "system" / "__init__.py").read_text(
        encoding="utf-8"
    )
    if '"account": "kairos-account-cli"' not in native_text:
        failures.append("NativeCliApplication must support kairos-account-cli")
    for token in (
        "system_component_account_app",
        "@system_component_account_app.command",
        "system_component_app.add_typer(system_component_account_app",
        "_workspace_account_client(",
        "_run_workspace_account_connected_command(",
        "_workspace_account_client(owner).refresh(",
        "_workspace_account_client(owner).reconcile(",
        "_workspace_account_client(owner).current_projection(",
        "_workspace_account_client(owner).observed_orders_projection(",
    ):
        if token in root_text:
            failures.append(
                "Account has no workspace-scoped system component; use "
                f"`kairos account ...` or launch instance component account instead: {token}"
            )
    for token in (
        '_run_workspace_market_connected_command(',
        'NativeCliApplication(owner).run("market"',
    ):
        if token not in root_text:
            failures.append(
                "system component market subscription/replay/recovery controls must "
                f"passthrough to the owner Rust CLI connected mode: {token}"
            )
    if '"market": "kairos-market-cli"' not in native_text:
        failures.append("NativeCliApplication must support kairos-market-cli")
    for token in (
        "_workspace_market_client(owner).recover(",
        "_workspace_market_client(owner).pause_replay(",
        "_workspace_market_client(owner).resume_replay(",
        "_workspace_market_client(owner).subscribe(",
        "_workspace_market_client(owner).unsubscribe(",
    ):
        if token in root_text:
            failures.append(
                "system component market subscription/replay/recovery controls must not "
                f"bypass the owner Rust CLI connected mode: {token}"
            )
    for token in (
        '_run_account_connected_command(',
        'NativeCliApplication(owner).run(\n        "account"',
    ):
        if token not in launch_text:
            failures.append(
                "launch component account runtime controls must passthrough "
                f"to the owner Rust CLI connected mode: {token}"
            )
    for token in (
        "client.refresh()",
        "client.reconcile()",
    ):
        if token in launch_text:
            failures.append(
                "launch component account runtime controls must not bypass "
                f"the owner Rust CLI connected mode: {token}"
            )

    if "launch_instance_component_execution_link_unknown" in launch_text:
        failures.append(
            "launch instance component execution exposes link-unknown before "
            "ExecutionControlRpc supports it"
        )
    for token in (
        '@system_component_market_app.command("status")',
        '@system_component_market_app.command("sources")',
        '@system_component_market_app.command("snapshot")',
        '@system_component_market_app.command("subscribe")',
        '@system_component_market_app.command("unsubscribe")',
        '@system_component_market_app.command("recover")',
        '@system_component_market_app.command("pause-replay")',
        '@system_component_market_app.command("resume-replay")',
        '@system_component_risk_app.command("health")',
        '@system_component_risk_app.command("latest")',
        '@system_component_risk_app.command("limits")',
        '@system_component_risk_app.command("reservations")',
        '@system_component_risk_app.command("circuits")',
        '@system_component_risk_app.command("pre-trade-check")',
        '@system_component_risk_app.command("authorize-reserve")',
        '@system_component_risk_app.command("release")',
        '@system_component_risk_app.command("consume")',
        '@system_component_risk_app.command("resize")',
        '@system_component_risk_app.command("open-circuit")',
        '@system_component_risk_app.command("close-circuit")',
        '@system_component_risk_app.command("publish-policy")',
        '@system_component_risk_app.command("advance-time")',
        '@system_component_capital_app.command("health")',
        '@system_component_capital_app.command("current")',
        '@system_component_capital_app.command("availabilities")',
        '@system_component_capital_app.command("objectives")',
        '@system_component_capital_app.command("demands")',
        '@system_component_capital_app.command("plans")',
        '@system_component_capital_app.command("routes")',
        '@system_component_capital_app.command("reservations")',
        '@system_component_capital_app.command("operations")',
        '@system_component_capital_app.command("alerts")',
        '@system_component_capital_app.command("publish-funding-objective")',
        '@system_component_capital_app.command("observe-demand")',
        '@system_component_capital_app.command("cancel-funding-objective")',
        '@system_component_capital_app.command("reconcile-plan")',
        '@system_component_reference_app.command("health")',
        '@system_component_reference_app.command("providers")',
        '@system_component_reference_app.command("catalog")',
        '@system_component_reference_app.command("validate")',
        '@system_component_reference_app.command("refresh")',
        '@system_component_reference_app.command("pause")',
        '@system_component_reference_app.command("resume")',
        '@system_component_reference_app.command("options-coverage")',
        '@system_component_reference_app.command("options-add")',
        '@system_component_reference_app.command("options-remove")',
    ):
        if token not in root_text:
            failures.append(
                f"system component command surface is missing connected command "
                f"command token: {token}"
            )
    for token in (
        '_run_workspace_capital_connected_command(',
        'NativeCliApplication(owner).run("capital"',
    ):
        if token not in root_text:
            failures.append(
                "system component capital runtime controls must passthrough "
                f"to the owner Rust CLI connected mode: {token}"
            )
    for token in (
        "_workspace_capital_client(owner).publish_funding_objective_request(",
        "_workspace_capital_client(owner).observe_capital_demand_request(",
        "_workspace_capital_client(owner).cancel_funding_objective_request(",
        "_workspace_capital_client(owner).reconcile_plan_request(",
        ".cancel_funding_objective(",
        ".reconcile_plan(",
    ):
        if token in root_text:
            failures.append(
                "system component capital runtime controls must not bypass "
                f"the owner Rust CLI connected mode: {token}"
            )
    for token in (
        '_run_workspace_risk_connected_command(',
        'NativeCliApplication(owner).run("risk"',
    ):
        if token not in root_text:
            failures.append(
                "system component risk runtime controls must passthrough to "
                f"the owner Rust CLI connected mode: {token}"
            )
    for token in (
        "_workspace_risk_client(owner).assess(",
        "_workspace_risk_client(owner).reserve(",
        "_workspace_risk_client(owner).release(",
        "_workspace_risk_client(owner).consume(",
        "_workspace_risk_client(owner).resize(",
        "_workspace_risk_client(owner).open_circuit(",
        "_workspace_risk_client(owner).close_circuit(",
        "_workspace_risk_client(owner).configure(",
        "_workspace_risk_client(owner).advance_time(",
    ):
        if token in root_text:
            failures.append(
                "system component risk runtime controls must not assemble "
                f"business requests in Python: {token}"
            )
    for token in (
        '@instance_component_account_app.command("snapshot")',
        '@instance_component_account_app.command("balances")',
        '@instance_component_account_app.command("positions")',
        '@instance_component_account_app.command("open-orders")',
        '@instance_component_account_app.command("refresh")',
        '@instance_component_account_app.command("reconcile")',
        '@instance_component_market_app.command("status")',
        '@instance_component_market_app.command("snapshot")',
        '@instance_component_risk_app.command("health")',
        '@instance_component_risk_app.command("latest")',
        '@instance_component_risk_app.command("limits")',
        '@instance_component_risk_app.command("reservations")',
        '@instance_component_risk_app.command("circuits")',
        '@instance_component_risk_app.command("pre-trade-check")',
        '@instance_component_risk_app.command("authorize-reserve")',
        '@instance_component_risk_app.command("release")',
        '@instance_component_risk_app.command("consume")',
        '@instance_component_risk_app.command("resize")',
        '@instance_component_risk_app.command("open-circuit")',
        '@instance_component_risk_app.command("close-circuit")',
        '@instance_component_risk_app.command("publish-policy")',
        '@instance_component_risk_app.command("advance-time")',
        '@instance_component_capital_app.command("health")',
        '@instance_component_capital_app.command("current")',
        '@instance_component_capital_app.command("availabilities")',
        '@instance_component_capital_app.command("objectives")',
        '@instance_component_capital_app.command("demands")',
        '@instance_component_capital_app.command("plans")',
        '@instance_component_capital_app.command("routes")',
        '@instance_component_capital_app.command("reservations")',
        '@instance_component_capital_app.command("operations")',
        '@instance_component_capital_app.command("alerts")',
        '@instance_component_capital_app.command("publish-funding-objective")',
        '@instance_component_capital_app.command("observe-demand")',
        '@instance_component_capital_app.command("cancel-funding-objective")',
        '@instance_component_capital_app.command("reconcile-plan")',
        '@instance_component_reference_app.command("health")',
        '@instance_component_reference_app.command("catalog")',
    ):
        if token not in launch_text:
            failures.append(
                f"launch instance component is missing connected runtime "
                f"control command token: {token}"
            )
    for token in (
        "_run_capital_connected_command(",
        'NativeCliApplication(instance_workspace).run(\n        "capital"',
    ):
        if token not in launch_text:
            failures.append(
                "launch component capital runtime controls must passthrough "
                f"to the owner Rust CLI connected mode: {token}"
            )
    for token in (
        ".publish_funding_objective_request(",
        ".observe_capital_demand_request(",
        ".cancel_funding_objective_request(",
        ".reconcile_plan_request(",
        ".cancel_funding_objective(",
        ".reconcile_plan(",
    ):
        if token in launch_text:
            failures.append(
                "launch component capital runtime controls must not bypass "
                f"the owner Rust CLI connected mode: {token}"
            )
    for token in (
        '@instance_component_execution_app.command(\n    "snapshot"',
        '@instance_component_execution_app.command(\n    "routes"',
        '@instance_component_execution_app.command(\n    "orders"',
        '@instance_component_execution_app.command(\n    "open-orders"',
        '@instance_component_execution_app.command(\n    "history"',
        '@instance_component_execution_app.command(\n    "fills"',
        '@instance_component_execution_app.command(\n    "events"',
        '@instance_component_execution_app.command(\n    "audit"',
        '@instance_component_execution_app.command(\n    "inspect"',
        '@instance_component_execution_app.command(\n    "trace"',
        '@instance_component_execution_app.command(\n    "journal"',
        '@instance_component_execution_app.command(\n    "reconcile"',
        '@instance_component_execution_app.command(\n    "unknown-remote-orders"',
        '@instance_component_execution_app.command(\n    "submit"',
        '@instance_component_execution_app.command(\n    "cancel"',
        '@instance_component_execution_app.command(\n    "replace"',
    ):
        if token not in launch_text:
            failures.append(
                f"launch instance component execution is missing connected "
                f"owner CLI passthrough command token: {token}"
            )
    for token in (
        '"--mode",\n            resolved_mode',
        'value.setdefault("instance_id", resolved_instance)',
        'value.setdefault("scope", "launch-instance")',
    ):
        if token not in launch_text:
            failures.append(
                "launch instance component execution must pass and expose the "
                f"resolved mode/instance scope: {token}"
            )
    for boundary in BUSINESS_BOUNDARIES:
        application_service = (
            module_root(boundary.product_name) / "src" / "application" / "service.rs"
        )
        if application_service.exists():
            failures.append(
                f"{application_service.relative_to(ROOT)} must be renamed; "
                "business application main facades belong in application/app.rs, "
                "while application/connected.rs is reserved for connected runtime facades"
            )
        if boundary.rust_cli_must_be_grouped:
            path = boundary.rust_cli
            if path is None or not path.exists():
                failures.append(
                    f"{boundary.product_name} declares a grouped Rust CLI, "
                    f"but {path.relative_to(ROOT) if path else '<missing>'} does not exist"
                )
            else:
                text = path.read_text(encoding="utf-8")
                for token in REQUIRED_RUST_MODE_TOKENS:
                    if token not in text:
                        failures.append(
                            f"{path.relative_to(ROOT)} is missing explicit {token}"
                        )
                command_body = _enum_body(text, "Command")
                if command_body is None:
                    failures.append(f"{path.relative_to(ROOT)} has no enum Command")
                else:
                    variants = _top_level_variants(command_body)
                    if variants != {"Standalone", "Connected"}:
                        failures.append(
                            f"{path.relative_to(ROOT)} enum Command exposes non-mode "
                            f"top-level variants: {sorted(variants)}"
                        )
                standalone_body = _enum_body(text, "StandaloneCommand")
                if standalone_body is not None:
                    variants = _top_level_variants(standalone_body)
                    forbidden = FORBIDDEN_STANDALONE_VARIANTS_BY_PRODUCT.get(
                        boundary.product_name, set()
                    )
                    leaked = variants & forbidden
                    if leaked:
                        failures.append(
                            f"{path.relative_to(ROOT)} standalone mode exposes runtime "
                            f"or projection commands: {sorted(leaked)}"
                        )
                    if boundary.product_name == "Risk":
                        for token in ("Schema", "Doctor", "Preview"):
                            if token not in variants:
                                failures.append(
                                    f"{path.relative_to(ROOT)} must expose Risk "
                                    f"standalone {token} through CliRiskApplication"
                                )
                    if boundary.product_name == "Capital":
                        for token in ("Schema", "Doctor", "Preview", "Plan"):
                            if token not in variants:
                                failures.append(
                                    f"{path.relative_to(ROOT)} must expose Capital "
                                    f"standalone {token} through CliCapitalApplication"
                                )
                    if boundary.product_name == "Execution":
                        for token in (
                            "OpenOrders",
                            "History",
                            "Order",
                            "Fills",
                            "Submit",
                            "Cancel",
                            "Replace",
                        ):
                            if token not in variants:
                                failures.append(
                                    f"{path.relative_to(ROOT)} must expose Execution "
                                    f"standalone provider-direct {token} through "
                                    "CliExecutionApplication"
                                )
                        for token in (
                            "connected_result(",
                            '"launch-instance"',
                            "&connected.launch_id",
                            "&connected.instance_id",
                        ):
                            if token not in text:
                                failures.append(
                                    f"{path.relative_to(ROOT)} must preserve concrete "
                                    f"connected Execution identity and scope: {token}"
                                )
                        for token in (
                            'LaunchId::new("cli")',
                            'InstanceId::new("cli")',
                            'default_value = "paper"',
                            '#[command(alias = "list")]',
                            '#[command(alias = "open")]',
                            '#[command(alias = "closed")]',
                            '#[command(alias = "reconcile-remote")]',
                            '#[command(alias = "show")]',
                            '#[command(alias = "place")]',
                        ):
                            if token in text:
                                failures.append(
                                    f"{path.relative_to(ROOT)} must not fabricate/default "
                                    f"connected identity or retain removed aliases: {token}"
                                )
                connected_body = _enum_body(text, "ConnectedCommand")
                if connected_body is not None:
                    variants = _top_level_variants(connected_body)
                    forbidden = FORBIDDEN_CONNECTED_VARIANTS_BY_PRODUCT.get(
                        boundary.product_name, set()
                    )
                    leaked = variants & forbidden
                    if leaked:
                        failures.append(
                            f"{path.relative_to(ROOT)} connected mode exposes removed "
                            f"or owner-misaligned commands: {sorted(leaked)}"
                        )
                    if boundary.product_name == "Execution":
                        execution_server_text = (
                            module_root(boundary.product_name)
                            / "src"
                            / "application"
                            / "connected.rs"
                        ).read_text(encoding="utf-8")
                        for token in (
                            "Snapshot",
                            "Routes",
                            "Orders",
                            "OpenOrders",
                            "History",
                            "Reconcile",
                            "UnknownRemoteOrders",
                            "Status",
                            "Inspect",
                            "Events",
                            "Trace",
                            "Audit",
                            "Journal",
                            "Fills",
                            "Submit",
                            "Cancel",
                            "Replace",
                        ):
                            if token not in variants:
                                failures.append(
                                    f"{path.relative_to(ROOT)} must expose Execution "
                                    f"connected {token} as a real contract/projection command"
                                )
                        for token in (
                            "dry_run",
                            'order_type: Option<String>',
                            "option: Vec<String>",
                            "not part of ExecutionControlRpc yet",
                        ):
                            if token in text:
                                failures.append(
                                    f"{path.relative_to(ROOT)} exposes an Execution "
                                    f"connected half-entry or half-parameter: {token}"
                                )
                        for token in (
                            "pub struct ConnectedExecutionApplication",
                            "install_execution_connection(",
                            "execution_client(",
                            "current_execution(&self.identity)",
                            "ExecutionControlRpcClient::routes",
                            "ExecutionControlRpcClient::reconcile",
                            "ExecutionControlRpcClient::submit_intent",
                            "ExecutionControlRpcClient::cancel_order",
                            "ExecutionControlRpcClient::replace_order",
                            "pub fn snapshot(",
                            "pub fn orders(",
                            "pub fn open_orders(",
                            "pub fn history(",
                            "pub fn unknown_remote_orders(",
                            "pub fn order_status(",
                            "pub fn events(",
                            "pub fn trace(",
                            "pub fn audit(",
                            "pub fn fills(",
                        ):
                            if token not in execution_server_text:
                                failures.append(
                                    "Execution connected runtime contract/view logic "
                                    "must live in application/connected.rs: "
                                    f"{token}"
                                )
                    if boundary.product_name == "Account":
                        account_server_text = (
                            module_root(boundary.product_name)
                            / "src"
                            / "application"
                            / "connected.rs"
                        ).read_text(encoding="utf-8")
                        for token in (
                            "Fill",
                            "Snapshot",
                            "Balances",
                            "Positions",
                            "OpenOrders",
                            "Refresh",
                            "Reconcile",
                        ):
                            if token not in variants:
                                failures.append(
                                    f"{path.relative_to(ROOT)} must expose Account "
                                    f"connected {token} as a real contract/projection command"
                                )
                        for token in (
                            "pub struct ConnectedAccountApplication",
                            "observed_orders(",
                            "account_current(",
                            "ViewCompleteness::COMPLETE",
                            "AccountControlRpcClient::apply_simulated_settlement",
                            "AccountControlRpcClient::refresh",
                            "AccountControlRpcClient::reconcile",
                            "pub fn snapshot(",
                            "pub fn balances(",
                            "pub fn positions(",
                            "pub fn observed_orders(",
                            "pub async fn refresh(",
                            "pub async fn reconcile(",
                        ):
                            if token not in account_server_text:
                                failures.append(
                                    "Account connected runtime contract/view logic "
                                    "must live in application/connected.rs: "
                                    f"{token}"
                                )
                    if boundary.product_name == "Risk":
                        risk_server_text = (
                            module_root(boundary.product_name)
                            / "src"
                            / "application"
                            / "connected.rs"
                        ).read_text(encoding="utf-8")
                        for token in (
                            "Health",
                            "Latest",
                            "Limits",
                            "Reservations",
                            "Circuits",
                            "PreTradeCheck",
                            "AuthorizeReserve",
                            "Release",
                            "Consume",
                            "Resize",
                            "OpenCircuit",
                            "CloseCircuit",
                            "PublishPolicy",
                            "AdvanceTime",
                        ):
                            if token not in variants:
                                failures.append(
                                    f"{path.relative_to(ROOT)} must expose Risk "
                                    f"connected {token} as real contract/projection commands"
                                )
                        for token in (
                            "pub struct ConnectedRiskApplication",
                            ".control().health().await",
                            ".control().pre_trade_check(",
                            ".control().authorize_and_reserve(",
                            ".control().release_reservation(",
                            ".control().consume_reservation(",
                            ".control().resize_reservation(",
                            ".control().open_circuit(",
                            ".control().close_circuit(",
                            ".control().publish_policy(",
                            ".control().advance_time(",
                            "fn latest_snapshot_json(",
                            "fn limits_snapshot_json(",
                            "fn reservations_snapshot_json(",
                            "fn circuits_snapshot_json(",
                        ):
                            if token not in risk_server_text:
                                failures.append(
                                    "Risk connected runtime contract/view logic "
                                    "must live in application/connected.rs: "
                                    f"{token}"
                                )
                    if boundary.product_name == "Market":
                        market_server_text = (
                            module_root(boundary.product_name)
                            / "src"
                            / "application"
                            / "connected.rs"
                        ).read_text(encoding="utf-8")
                        for token in (
                            "Status",
                            "Sources",
                            "Snapshot",
                            "Freshness",
                            "Subscribe",
                            "Unsubscribe",
                            "Recover",
                            "PauseReplay",
                            "ResumeReplay",
                        ):
                            if token not in variants:
                                failures.append(
                                    f"{path.relative_to(ROOT)} must expose Market "
                                    f"connected {token} as a real owner command"
                                )
                        for token in (
                            "pub struct ConnectedMarketApplication",
                            ".control().health().await",
                            ".data_sources(query)",
                            ".control().subscribe(",
                            ".control().unsubscribe(",
                            ".control().recover().await",
                            ".control().pause_replay().await",
                            ".control().resume_replay().await",
                            "pub fn quote_snapshot(",
                            "pub fn bar_snapshot(",
                            "pub fn greeks_snapshot(",
                            "pub fn freshness_snapshot(",
                            "fn snapshot_json(",
                            "fn view_metadata_json(",
                        ):
                            if token not in market_server_text:
                                failures.append(
                                    "Market connected runtime contract/view logic "
                                    "must live in application/connected.rs: "
                                    f"{token}"
                                )
                    if boundary.product_name == "Reference":
                        reference_server_text = (
                            module_root(boundary.product_name)
                            / "src"
                            / "application"
                            / "connected.rs"
                        ).read_text(encoding="utf-8")
                        for token in (
                            "Status",
                            "Providers",
                            "Doctor",
                            "Logs",
                            "Coverage",
                            "Refresh",
                            "Sync",
                            "Publish",
                            "Assets",
                            "Instruments",
                            "Listings",
                        ):
                            if token not in variants:
                                failures.append(
                                    f"{path.relative_to(ROOT)} must expose Reference "
                                    f"connected {token} as a real owner command"
                                )
                        for token in (
                            "enum ConnectedAssetCommand",
                            "struct AddAssetArgs",
                            "struct AddInstrumentArgs",
                            "struct AddListingArgs",
                        ):
                            if token not in text:
                                failures.append(
                                    f"{path.relative_to(ROOT)} must keep Reference "
                                    f"catalog mutation in connected owner commands: {token}"
                                )
                        for token in (
                            "pub struct ConnectedReferenceApplication",
                            "ReferenceControlRpcClient::status",
                            "ReferenceControlRpcClient::refresh",
                            "ReferenceControlRpcClient::publish",
                            "ReferenceControlRpcClient::upsert_asset",
                            "ReferenceControlRpcClient::upsert_instrument",
                            "ReferenceControlRpcClient::upsert_listing",
                            "pub fn summarize_providers(",
                            "pub fn summarize_doctor(",
                            "pub fn summarize_coverage(",
                        ):
                            if token not in reference_server_text:
                                failures.append(
                                    "Reference connected runtime contract calls "
                                    "must live in application/connected.rs: "
                                    f"{token}"
                                )
                    if boundary.product_name == "Capital":
                        capital_server_text = (
                            module_root(boundary.product_name)
                            / "src"
                            / "application"
                            / "connected.rs"
                        ).read_text(encoding="utf-8")
                        for token in (
                            "Health",
                            "Current",
                            "Objectives",
                            "Demands",
                            "Availabilities",
                            "Availability",
                            "Routes",
                            "Plans",
                            "Reservations",
                            "Operations",
                            "Alerts",
                            "PublishFundingObjective",
                            "ObserveDemand",
                            "CancelFundingObjective",
                            "ReconcilePlan",
                        ):
                            if token not in variants:
                                failures.append(
                                    f"{path.relative_to(ROOT)} must expose Capital "
                                    f"connected {token} as a real contract/projection command"
                                )
                        for token in (
                            "pub struct ConnectedCapitalApplication",
                            ".control().health().await",
                            ".query_capital_availability(",
                            ".publish_funding_objective(",
                            ".observe_capital_demand(",
                            ".cancel_funding_objective(",
                            ".reconcile_capital_plan(",
                            "fn current_snapshot_json(",
                            "fn availabilities_snapshot_json(",
                            "fn objectives_snapshot_json(",
                            "fn demands_snapshot_json(",
                            "fn plans_snapshot_json(",
                            "fn routes_snapshot_json(",
                            "fn reservations_snapshot_json(",
                            "fn operations_snapshot_json(",
                            "fn alerts_snapshot_json(",
                        ):
                            if token not in capital_server_text:
                                failures.append(
                                    "Capital connected runtime contract/view logic "
                                    "must live in application/connected.rs: "
                                    f"{token}"
                                )
                for token in FORBIDDEN_RUST_FLAT_MODE_TOKENS:
                    if token in text:
                        failures.append(
                            f"{path.relative_to(ROOT)} keeps a flat command fallback: {token}"
                        )
                for token in FORBIDDEN_RUST_MODE_NORMALIZATION_TOKENS:
                    if token in text:
                        failures.append(
                            f"{path.relative_to(ROOT)} collapses standalone/connected "
                            f"mode into a shared command model: {token}"
                        )
                for token in FORBIDDEN_RUST_RUNTIME_ASSEMBLY_BY_PRODUCT.get(
                    boundary.product_name, ()
                ):
                    if token in text:
                        failures.append(
                            f"{path.relative_to(ROOT)} assembles runtime application "
                            f"inside a one-shot CLI boundary: {token}"
                        )
                if boundary.product_name == "Account":
                    for token in FORBIDDEN_RUST_BIN_ACCOUNT_CLI_TOKENS:
                        if token in text:
                            failures.append(
                                f"{path.relative_to(ROOT)} keeps Account standalone "
                                f"provider/config business logic in bin: {token}"
                            )
                    for token in (
                        "AccountControlRpcClient",
                        "AccountClient",
                        "ViewCompleteness",
                        "Decimal64",
                        "install_account_connection(",
                        "account_client(",
                        ".observed_orders(format!",
                        ".account_current(format!",
                        "fn decimal_text(",
                        "fn optional_decimal(",
                    ):
                        if token in text:
                            failures.append(
                                f"{path.relative_to(ROOT)} keeps Account connected "
                                f"runtime contract/view implementation in bin: {token}"
                            )
                if boundary.product_name == "Reference":
                    for token in (
                        "fn read_catalog_command(",
                        "fn read_participants(",
                        "fn read_markets(",
                        "fn read_events(",
                        "fn read_option_chain(",
                        "fn read_query(",
                        "fn snapshot_collections(",
                        "fn json_records(",
                        "fn filter_catalog_records(",
                        "fn find_record(",
                    ):
                        if token in text:
                            failures.append(
                                f"{path.relative_to(ROOT)} keeps Reference standalone "
                                f"catalog/query implementation in bin: {token}"
                            )
                    for token in (
                        "ReferenceControlRpcClient::status",
                        "ReferenceControlRpcClient::refresh",
                        "ReferenceControlRpcClient::publish",
                        "ReferenceControlRpcClient::upsert_asset",
                        "ReferenceControlRpcClient::upsert_instrument",
                        "ReferenceControlRpcClient::upsert_listing",
                        "fn summarize_providers(",
                        "fn summarize_doctor(",
                        "fn summarize_coverage(",
                    ):
                        if token in text:
                            failures.append(
                                f"{path.relative_to(ROOT)} keeps Reference connected "
                                f"runtime contract implementation in bin: {token}"
                            )
                if boundary.product_name == "Market":
                    for token in (
                        "MarketControlRpcClient",
                        "MarketDataSourcesQuery",
                        "SnapshotEnvelopeMetadata",
                        "ViewMetadata",
                        "fn connected_market_client(",
                        "fn snapshot_json(",
                        "fn view_metadata_json(",
                    ):
                        if token in text:
                            failures.append(
                                f"{path.relative_to(ROOT)} keeps Market connected "
                                f"runtime contract/view implementation in bin: {token}"
                            )
                if boundary.product_name == "Execution":
                    for token in (
                        "ExecutionControlRpcClient",
                        "ExecutionCommandStatus",
                        "ExecutionReconcileResponse",
                        "ExecutionRoutesResponse",
                        "install_execution_connection(",
                        "execution_client(",
                        "client.current_execution(",
                        "fn filter_orders(",
                        "fn filter_events(",
                        "read_current_execution_view",
                    ):
                        if token in text:
                            failures.append(
                                f"{path.relative_to(ROOT)} keeps Execution connected "
                                f"runtime contract/view implementation in bin: {token}"
                            )
                if boundary.product_name == "Risk":
                    for token in (
                        "RiskControlRpcClient::health",
                        "RiskControlRpcClient::pre_trade_check",
                        "RiskControlRpcClient::authorize_and_reserve",
                        "RiskControlRpcClient::release_reservation",
                        "RiskControlRpcClient::consume_reservation",
                        "RiskControlRpcClient::resize_reservation",
                        "RiskControlRpcClient::open_circuit",
                        "RiskControlRpcClient::close_circuit",
                        "RiskControlRpcClient::publish_policy",
                        "RiskControlRpcClient::advance_time",
                        "fn latest_snapshot_json(",
                        "fn limits_snapshot_json(",
                        "fn reservations_snapshot_json(",
                        "fn circuits_snapshot_json(",
                        "fn limit_json(",
                        "fn reservation_json(",
                    ):
                        if token in text:
                            failures.append(
                                f"{path.relative_to(ROOT)} keeps Risk connected "
                                f"runtime contract/view implementation in bin: {token}"
                            )
                if boundary.product_name == "Capital":
                    for token in (
                        "CapitalControlRpcClient::health",
                        "CapitalControlRpcClient::query_capital_availability",
                        "CapitalControlRpcClient::publish_funding_objective",
                        "CapitalControlRpcClient::observe_capital_demand",
                        "CapitalControlRpcClient::cancel_funding_objective",
                        "CapitalControlRpcClient::reconcile_capital_plan",
                        "fn current_snapshot_json(",
                        "fn availabilities_snapshot_json(",
                        "fn objectives_snapshot_json(",
                        "fn demands_snapshot_json(",
                        "fn plans_snapshot_json(",
                        "fn routes_snapshot_json(",
                        "fn reservations_snapshot_json(",
                        "fn operations_snapshot_json(",
                        "fn alerts_snapshot_json(",
                        "fn objective_json(",
                        "fn demand_json(",
                        "fn availability_json(",
                        "fn route_json(",
                        "fn plan_json(",
                        "fn reservation_json(",
                    ):
                        if token in text:
                            failures.append(
                                f"{path.relative_to(ROOT)} keeps Capital connected "
                                f"runtime contract/view implementation in bin: {token}"
                            )
                for token in REQUIRED_RUST_CLI_APP_CALLS_BY_PRODUCT.get(
                    boundary.product_name, ()
                ):
                    if token not in text:
                        failures.append(
                            f"{path.relative_to(ROOT)} must route standalone "
                            f"local reads through {cli_app}: missing {token}"
                        )
                for token in FORBIDDEN_GENERIC_RUST_COMMAND_ENUMS.get(
                    boundary.product_name, ()
                ):
                    if token in text:
                        failures.append(
                            f"{path.relative_to(ROOT)} defines generic {token}; "
                            "CLI modes must dispatch directly through explicit "
                            "StandaloneCommand/ConnectedCommand"
                        )
                if f"struct {cli_app}" in text or f"pub struct {cli_app}" in text:
                    failures.append(
                        f"{path.relative_to(ROOT)} defines {cli_app} in bin; "
                        "CLI application facades belong in src/application/cli.rs"
                    )
        elif boundary.rust_cli is not None and boundary.rust_cli.exists():
            failures.append(
                f"{boundary.product_name} has an undeclared Rust CLI: "
                f"{boundary.rust_cli.relative_to(ROOT)}"
            )

        if boundary.system_component is not None:
            token = f"system_component_{boundary.system_component}_app"
            if token not in root_text:
                failures.append(
                    f"system component {boundary.system_component} is missing "
                    f"an explicit Typer namespace in root.py"
                )
        if boundary.launch_component is not None:
            token = f"instance_component_{boundary.launch_component}_app"
            if token not in launch_text:
                failures.append(
                    f"launch instance component {boundary.launch_component} is "
                    f"missing an explicit Typer namespace in launch.py"
                )

    rust_cli_paths = {
        boundary.rust_cli
        for boundary in BUSINESS_BOUNDARIES
        if boundary.rust_cli is not None
    }
    for path in ROOT.glob("crates/modules/*/src/bin/kairos-*-cli.rs"):
        if path not in rust_cli_paths:
            failures.append(f"undeclared Rust business CLI: {path.relative_to(ROOT)}")

    for path in (boundary.rust_cli for boundary in BUSINESS_BOUNDARIES if boundary.rust_cli):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if (
            "ConnectedCommand" in text
            and "socket" not in text
            and "not implemented" not in text
            and "enum ConnectedCommand {}" not in text
        ):
            failures.append(
                f"{path.relative_to(ROOT)} declares connected mode without a visible "
                "server selector or explicit unimplemented guard"
            )

    if failures:
        print("CLI boundary checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("CLI boundary checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
