from __future__ import annotations

from pathlib import Path
import time
from typing import cast

import typer

from kairospy.application.system import (
    ComponentControlApplication,
    ComponentProcessApplication,
    NativeCliApplication,
    SystemRuntimeSupervisor,
)
from decimal import Decimal
from decimal import InvalidOperation
from kairospy.application.config import ConfigApplication
from kairospy.application.account import (
    AccountAdminApplication,
    AccountCliApplication,
    CredentialApplication,
    TradeLeaseApplication,
)
from kairospy.application.market import MarketCliApplication, MarketDataApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli.options import OutputFormat, effective_output, render


def _emit(value: object, output: OutputFormat) -> None:
    typer.echo(render(value, output))


def _decimal_option(value: str | None, name: str) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise typer.BadParameter(f"{name} must be a decimal") from error
    if not parsed.is_finite():
        raise typer.BadParameter(f"{name} must be finite")
    return parsed


def _required_decimal(value: str, name: str) -> Decimal:
    parsed = _decimal_option(value, name)
    if parsed is None:
        raise typer.BadParameter(f"{name} is required")
    return parsed


def _execution_submit_args(
    account: dict[str, object],
    *,
    order_id: str,
    account_id: str,
    instrument_id: str,
    quantity: Decimal,
    side: str,
    order_type: str,
    limit_price: Decimal | None,
    intent_id: str | None = None,
    market_id: str | None = None,
) -> list[str]:
    provider = str(account.get("broker") or account.get("exchange") or "simulated")
    provider = "okx" if provider == "okex" else provider
    segment = str(account.get("product_family") or account.get("segment") or "spot")
    arguments = [
        "submit",
        "--order-id",
        order_id,
        "--account-id",
        account_id,
        "--segment-key",
        segment,
        "--instrument-id",
        instrument_id,
        "--quantity",
        format(quantity, "f"),
        "--side",
        side,
        "--order-type",
        order_type,
        "--provider",
        provider,
        "--product",
        segment,
    ]
    credential = account.get("credential")
    if credential:
        arguments.extend(("--credential-id", str(credential)))
    environment = str(account.get("environment") or "paper").lower()
    if environment == "live":
        arguments.append("--confirm-live")
    if limit_price is not None:
        arguments.extend(("--limit-price", format(limit_price, "f")))
    if intent_id:
        arguments.extend(("--intent-id", intent_id))
    if market_id:
        arguments.extend(("--market-id", market_id))
    return arguments


def _order_query_action(action: str):
    def command(
        order_id: str | None = typer.Option(None, "--order-id", "--id"),
        account_id: str | None = typer.Option(None, "--account-id"),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        arguments = ["status", "--order-id", order_id] if order_id else ["orders"]
        if account_id and not order_id:
            arguments.extend(("--account-id", account_id))
        value = NativeCliApplication(owner).run("execution", arguments)
        _emit(value, output)

    command.__name__ = f"order_query_{action}"
    return command


def _status_command(component: str):
    """Retained only for the private command registry in this module.

    The public account/market surfaces are registered as canonical passthrough
    commands from ``app.py``. Order status remains a cross-module input adapter.
    """

    def status(
        workspace: Path = typer.Option(None, "--workspace"),
        account_id: str | None = typer.Option(None, "--account-id"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        if component != "order":
            raise typer.BadParameter(
                "account and market commands are canonical passthroughs"
            )
        owner = WorkspaceApplication().open(workspace)
        arguments = ["snapshot"]
        if account_id:
            arguments = ["--account-id", account_id, "snapshot"]
        _emit(NativeCliApplication(owner).run("execution", arguments), output)

    status.__name__ = f"{component}_status"
    return status


def _socket_action(component: str, action: str):
    """Fail clearly if an unregistered process command is reached."""

    def command() -> None:
        raise typer.BadParameter(
            f"{component} process control belongs to kairos system; use system commands"
        )

    command.__name__ = f"{component}_{action}"
    return command


def _add_group(
    parent: typer.Typer, name: str, commands: tuple[str, ...]
) -> typer.Typer:
    group = typer.Typer(no_args_is_help=True, help=f"{name} commands")
    parent.add_typer(group, name=name)
    del commands
    return group


project_app = typer.Typer(no_args_is_help=True, help="Project commands")
config_app = typer.Typer(no_args_is_help=True, help="Configuration commands")
account_app = typer.Typer(no_args_is_help=True, help="Private account command registry")
market_app = typer.Typer(no_args_is_help=True, help="Private market command registry")
order_app = typer.Typer(no_args_is_help=True, help="Order commands")
system_app = typer.Typer(no_args_is_help=True, help="System runtime commands")


@project_app.command(
    "init", help="Create a Kairos project, optionally with a runnable starter."
)
def project_init(
    root: Path | None = typer.Argument(
        None, help="Project directory (prompted when omitted)"
    ),
    workspace_id: str | None = typer.Option(None, "--id"),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Do not prompt; require the project directory and --id",
    ),
    template: str | None = typer.Option(
        None,
        "--template",
        help="Install a runnable starter; currently supported: backtest.",
    ),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    template_name = template.strip().lower() if template is not None else None
    if root is None:
        if non_interactive:
            raise typer.BadParameter(
                "project directory is required with --non-interactive"
            )
        root = Path(typer.prompt("项目目录", default="."))
    else:
        root = Path(root)

    default_id = root.expanduser().resolve().name
    if workspace_id is None:
        if non_interactive:
            raise typer.BadParameter("--id is required with --non-interactive")
        workspace_id = typer.prompt("项目名", default=default_id)

    try:
        workspace = WorkspaceApplication().init_project(
            root, workspace_id=workspace_id, template=template_name
        )
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="--template") from error
    next_steps = (
        [
            "cd " + str(workspace.paths.project_root),
            "kairos launch start demo-backtest",
            "kairos launch wait demo-backtest",
        ]
        if template_name == "backtest"
        else ["add a launch config under .kairos/config/launches"]
    )
    _emit(
        {
            "status": "initialized",
            "workspace_id": workspace.workspace_id,
            "root": str(workspace.paths.root),
            "template": template_name,
            "next_steps": next_steps,
        },
        output,
    )


@project_app.command("status", help="Show the resolved project and workspace paths.")
def project_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    value = WorkspaceApplication().open(workspace)
    _emit({"workspace_id": value.workspace_id, "root": str(value.paths.root)}, output)


@project_app.command("scaffold")
def project_scaffold(
    template: str = typer.Option(
        "backtest", "--template", help="Starter to install; currently: backtest."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Install a runnable starter into an existing project."""

    owner = WorkspaceApplication().open(workspace)
    try:
        created = WorkspaceApplication().install_template(owner, template=template)
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error), param_hint="--template") from error
    _emit(
        {
            "status": "scaffolded",
            "template": template.strip().lower(),
            "created": [str(path) for path in created],
            "next_steps": [
                "kairos launch start demo-backtest",
                "kairos launch wait demo-backtest",
            ],
        },
        output,
    )


@project_app.command("doctor", help="Check project readiness and show the next action.")
def project_doctor(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).doctor(), output)


# Keep the established top-level names as thin input adapters. Module use
# cases are invoked through their application-owned CLI/application paths.
for _app, _name in ((order_app, "order"),):
    _app.command("status")(_status_command(_name))


def _market_remote_action(action: str):
    def command(
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        client = ComponentProcessApplication(owner).ensure_running("market")
        operation = {
            "status": client.status,
            "refresh": client.refresh,
            "recover": client.recover,
            "stop": client.stop,
        }.get(action)
        if operation is None:
            raise typer.BadParameter(f"unsupported remote market action: {action}")
        value = operation()
        _emit(value, output)

    command.__name__ = f"market_{action}"
    return command


for _command_name in ("status", "refresh", "recover", "stop"):
    market_app.command(_command_name)(_market_remote_action(_command_name))


@market_app.command("validate")
def market_validate(
    market_id: str | None = typer.Option(None, "--market-id"),
    instrument_id: str | None = typer.Option(None, "--instrument-id"),
    exchange_id: str = typer.Option("binance", "--exchange-id"),
    market_type: str = typer.Option("spot", "--market-type"),
    source_symbol: str = typer.Option("BTCUSDT", "--source-symbol"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    value = MarketCliApplication().run(
        cast(
            list[str],
            [
                "validate",
                "--market-id",
                market_id,
                "--instrument-id",
                instrument_id,
                "--exchange-id",
                exchange_id,
                "--market-type",
                market_type,
                "--source-symbol",
                source_symbol,
            ],
        )
    )
    _emit(value, output)


@market_app.command("once")
def market_once(
    provider: str | None = typer.Option(None, "--provider"),
    endpoint: str | None = typer.Option(None, "--endpoint"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    arguments = ["once"]
    if provider is not None:
        arguments.extend(("--provider", provider))
    if endpoint is not None:
        arguments.extend(("--endpoint", endpoint))
    value = MarketCliApplication().run(arguments)
    _emit(value, output)


@market_app.command("replay")
def market_replay(
    file: Path = typer.Option(..., "--file"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    value = MarketCliApplication().run(["replay", "--file", str(file)])
    _emit(value, output)


def _account_admin(action: str):
    def command(
        account_id: str | None = typer.Option(None, "--account-id", "--id"),
        workspace: Path = typer.Option(None, "--workspace"),
        broker: str | None = typer.Option(None, "--broker"),
        segment: str | None = typer.Option(None, "--segment"),
        environment: str | None = typer.Option(None, "--environment"),
        credential: str | None = typer.Option(None, "--credential"),
        credential_role: str = typer.Option("readonly", "--credential-role"),
        alias: str | None = typer.Option(None, "--alias"),
        product_family: str | None = typer.Option(None, "--product-family"),
        account_model: str | None = typer.Option(None, "--account-model"),
        balance: list[str] = typer.Option(
            [], "--balance", help="Initial simulated asset quantity, e.g. USDT=10000."
        ),
        fee_rate: str | None = typer.Option(None, "--fee-rate"),
        force: bool = typer.Option(False, "--force"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        app = AccountAdminApplication(WorkspaceApplication().open(workspace))
        if action in {"list", "browse"}:
            value = app.list()
        elif action == "schemas":
            value = app.schemas()
        elif action == "schema":
            value = app.schema(broker or "binance")
        elif action in {"show", "inspect"}:
            if not account_id:
                raise typer.BadParameter("--account-id is required")
            value = app.show(account_id)
        elif action == "connect":
            if not account_id:
                raise typer.BadParameter("--account-id is required")
            value = app.connect(
                account_id,
                broker=broker or "binance",
                segment=segment or "spot",
                environment=environment or "live",
                credential=credential,
                credential_role=credential_role,
                alias=alias,
                product_family=product_family,
                account_model=account_model,
                force=force,
            )
        elif action == "simulate":
            if not account_id:
                raise typer.BadParameter("--account-id is required")
            value = app.simulate(
                account_id,
                broker=broker or "paper",
                segment=segment or "spot",
                environment=environment or "paper",
                account_model=account_model,
                initial_balances=tuple(balance),
                fee_rate=fee_rate or "0",
                force=force,
            )
        elif action == "modify":
            if not account_id:
                raise typer.BadParameter("--account-id is required")
            changes = {
                key: value
                for key, value in {
                    "broker": broker,
                    "segment": segment,
                    "environment": environment,
                    "credential": credential,
                    "credential_role": credential_role,
                    "alias": alias,
                    "product_family": product_family,
                    "account_model": account_model,
                    "fee_rate": fee_rate,
                    "initial_balances": balance or None,
                }.items()
                if value is not None
            }
            value = app.modify(account_id, **changes)
        elif action in {"delete", "remove"}:
            if not account_id:
                raise typer.BadParameter("--account-id is required")
            value = app.delete(account_id, force=force)
        elif action == "doctor":
            value = {"configured_accounts": len(app.list()), "path": str(app.path)}
        else:
            raise typer.BadParameter(f"unsupported account admin operation: {action}")
        _emit(value, output)

    command.__name__ = f"account_{action}"
    return command


for _command_name in (
    "list",
    "browse",
    "schemas",
    "schema",
    "inspect",
    "connect",
    "simulate",
    "modify",
    "delete",
    "remove",
    "show",
    "doctor",
):
    account_app.command(_command_name)(_account_admin(_command_name))
account_credential_app = _add_group(
    account_app, "credential", ("add", "list", "create", "show", "delete", "remove")
)
account_query_app = _add_group(
    account_app, "query", ("balance", "positions", "open-orders", "snapshot")
)
account_trade_lock_app = _add_group(
    account_app, "trade-lock", ("status", "list", "show", "release")
)
account_model_app = _add_group(account_app, "model", ("switch",))


def _account_query(view: str):
    def command(
        workspace: Path = typer.Option(None, "--workspace"),
        account_id: str | None = typer.Option(None, "--account-id"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        arguments = {
            "balance": "balances",
            "positions": "positions",
            "open-orders": "open-orders",
            "snapshot": "snapshot",
        }[view]
        if account_id:
            arguments = ["--account-id", account_id, arguments]
        else:
            arguments = [arguments]
        _emit(AccountCliApplication(owner).run(arguments), output)

    command.__name__ = f"account_query_{view.replace('-', '_')}"
    return command


for _view in ("balance", "positions", "open-orders", "snapshot"):
    account_query_app.command(_view)(_account_query(_view))


def _trade_lock(action: str):
    def command(
        workspace: Path = typer.Option(None, "--workspace"),
        owner: str = typer.Option("cli", "--owner"),
        broker: str = typer.Option("binance", "--broker"),
        account_id: str | None = typer.Option(None, "--account-id"),
        environment: str = typer.Option("live", "--environment"),
        launch_id: str = typer.Option("cli", "--launch-id"),
        launch_instance_id: str = typer.Option("cli", "--launch-instance-id"),
        mode: str = typer.Option("live", "--mode"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        app = TradeLeaseApplication(WorkspaceApplication().open(workspace))
        account = account_id or owner
        key = app.account_key(broker, account)
        if action in {"status", "list"}:
            value = app.list()
        elif action == "show":
            value = app.for_account(account_id or owner)
        elif action == "acquire":
            value = app.acquire(
                broker=broker,
                account_id=account,
                environment=environment,
                launch_id=launch_id,
                launch_instance_id=launch_instance_id,
                mode=mode,
            )
        elif action == "heartbeat":
            value = app.heartbeat(key, launch_instance_id=launch_instance_id)
        else:
            value = app.release(key, force=True)
        _emit(value, output)

    command.__name__ = f"trade_lock_{action.replace('-', '_')}"
    return command


for _action in ("status", "list", "show", "acquire", "heartbeat", "release"):
    account_trade_lock_app.command(_action)(_trade_lock(_action))


def _credential(action: str):
    def command(
        credential_id: str | None = typer.Option(None, "--credential-id", "--id"),
        provider: str = typer.Option("binance", "--provider"),
        kind: str | None = typer.Option(None, "--kind"),
        api_key: str | None = typer.Option(
            None,
            "--api-key",
            help="Secret value is never persisted; use an external secret store.",
        ),
        api_secret: str | None = typer.Option(
            None,
            "--api-secret",
            help="Secret value is never persisted; use an external secret store.",
        ),
        passphrase: str | None = typer.Option(
            None,
            "--passphrase",
            help="Secret value is never persisted; use an external secret store.",
        ),
        field: list[str] = typer.Option([], "--field"),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        app = CredentialApplication(WorkspaceApplication().open(workspace))
        if action == "list":
            value = app.list()
        elif action in {"add", "create"}:
            if not credential_id:
                raise typer.BadParameter("--credential-id is required")
            secret_fields = tuple(field) + tuple(
                name
                for name, secret in (
                    ("api_key", api_key),
                    ("api_secret", api_secret),
                    ("passphrase", passphrase),
                )
                if secret is not None
            )
            value = app.add(
                credential_id, provider=provider, kind=kind, fields=secret_fields
            )
        elif action == "show":
            if not credential_id:
                raise typer.BadParameter("--credential-id is required")
            value = app.show(credential_id)
        else:
            if not credential_id:
                raise typer.BadParameter("--credential-id is required")
            value = app.delete(credential_id)
        _emit(value, output)

    command.__name__ = f"credential_{action}"
    return command


for _action in ("add", "list", "create", "show", "delete", "remove"):
    account_credential_app.command(_action)(_credential(_action))


@account_model_app.command("switch")
def account_model_switch(
    account_id: str = typer.Option(..., "--account-id"),
    model: str = typer.Option(..., "--model"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    value = AccountAdminApplication(
        WorkspaceApplication().open(workspace)
    ).switch_model(account_id, model)
    _emit(value, output)


market_source_app = _add_group(
    market_app, "source", ("capabilities", "check", "doctor")
)
market_data_app = _add_group(market_app, "data", ("download", "prefetch"))
market_dataset_app = _add_group(
    market_app, "dataset", ("list", "inspect", "alias", "prune", "read")
)
market_stream_app = _add_group(market_app, "stream", ("replay", "watch", "persist"))


def _market_source(action: str):
    def command(
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        value = ComponentProcessApplication(owner).ensure_running("market").status()
        value["operation"] = action
        _emit(value, output)

    command.__name__ = f"market_source_{action}"
    return command


for _action in ("capabilities", "check", "doctor"):
    market_source_app.command(_action)(_market_source(_action))


def _market_data_ingest(action: str):
    def command(
        name: str = typer.Option(..., "--name"),
        source_file: Path = typer.Option(..., "--source-file"),
        storage_format: str | None = typer.Option(
            None,
            "--storage-format",
            help="jsonl or parquet (defaults to source suffix)",
        ),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        value = MarketDataApplication(owner.paths.state / "market").ingest(
            name, source_file, format=storage_format
        )
        value["operation"] = action
        _emit(value, output)

    command.__name__ = f"market_data_{action}"
    return command


market_data_app.command("prefetch")(_market_data_ingest("prefetch"))


@market_data_app.command("download")
def market_data_download(
    provider: str = typer.Option("binance", "--provider"),
    symbol: str = typer.Option(..., "--symbol"),
    start: int = typer.Option(..., "--start", help="Unix milliseconds, inclusive"),
    end: int = typer.Option(..., "--end", help="Unix milliseconds, exclusive"),
    name: str = typer.Option("market-history", "--name"),
    file: Path = typer.Option(..., "--file"),
    market_id: str | None = typer.Option(None, "--market-id"),
    instrument_id: str | None = typer.Option(None, "--instrument-id"),
    interval: str = typer.Option("1m", "--interval"),
    api_key: str | None = typer.Option(None, "--api-key"),
    endpoint: str | None = typer.Option(None, "--endpoint"),
    storage_format: str = typer.Option("parquet", "--storage-format"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Download normalized bars and register a workspace dataset."""
    owner = WorkspaceApplication().open(workspace)
    arguments = [
        "download",
        "--provider",
        provider,
        "--symbol",
        symbol,
        "--start",
        str(start),
        "--end",
        str(end),
        "--dataset-id",
        name,
        "--file",
        str(file),
        "--interval",
        interval,
    ]
    if market_id:
        arguments.extend(("--market-id", market_id))
    if instrument_id:
        arguments.extend(("--instrument-id", instrument_id))
    if api_key:
        arguments.extend(("--api-key", api_key))
    if endpoint:
        arguments.extend(("--endpoint", endpoint))
    result = MarketCliApplication(owner).run(arguments)
    if storage_format not in {"parquet", "jsonl"}:
        raise typer.BadParameter("--storage-format must be parquet or jsonl")
    entry = MarketDataApplication(owner.paths.state / "market").ingest(
        name,
        Path(result["path"]),
        format=storage_format,
        metadata={
            "provider": result.get("source", provider),
            "symbol": result.get("symbol", symbol),
            "market_id": result.get("market_id", market_id),
            "instrument_id": result.get("instrument_id", instrument_id),
            "observation_type": result.get("data_kind", "bar"),
            "timeframe": result.get("interval", interval),
            "start_time_unix_millis": result.get("start_time_unix_millis", start),
            "end_time_unix_millis": result.get("end_time_unix_millis", end),
        },
    )
    result = {**result, "dataset": entry, "storage_format": storage_format}
    _emit(result, output)


def _market_dataset(action: str):
    def command(
        name: str | None = typer.Option(None, "--name"),
        alias: str | None = typer.Option(None, "--alias"),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        app = MarketDataApplication(
            WorkspaceApplication().open(workspace).paths.state / "market"
        )
        if action == "list":
            value = app.list()
        elif action == "inspect":
            if not name:
                raise typer.BadParameter("--name is required")
            value = app.inspect(name)
        elif action == "alias":
            if not name or not alias:
                raise typer.BadParameter("--name and --alias are required")
            value = app.alias(name, alias)
        elif action == "prune":
            if not name:
                raise typer.BadParameter("--name is required")
            value = app.prune(name)
        else:
            if not name:
                raise typer.BadParameter("--name is required")
            value = {"name": name, "content": app.read(name)}
        _emit(value, output)

    command.__name__ = f"market_dataset_{action}"
    return command


for _action in ("list", "inspect", "alias", "prune", "read"):
    market_dataset_app.command(_action)(_market_dataset(_action))


def _market_stream(action: str):
    def command(
        name: str = typer.Option(..., "--name"),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        app = MarketDataApplication(
            WorkspaceApplication().open(workspace).paths.state / "market"
        )
        _emit({"operation": action, "name": name, "content": app.read(name)}, output)

    command.__name__ = f"market_stream_{action}"
    return command


for _action in ("replay", "watch", "persist"):
    market_stream_app.command(_action)(_market_stream(_action))
for _command_name in (
    "events",
    "trace",
    "open",
    "list",
    "browse",
    "history",
    "closed",
    "show",
    "inspect",
):
    order_app.command(_command_name)(_order_query_action(_command_name))
for _command_name in ("place", "cancel", "replace"):
    if _command_name == "place":

        @order_app.command("place")
        def order_place(
            order_id: str = typer.Option(..., "--order-id", "--id"),
            account_id: str = typer.Option(..., "--account-id"),
            instrument_id: str = typer.Option(..., "--instrument-id"),
            quantity: str = typer.Option(..., "--quantity"),
            side: str = typer.Option("buy", "--side"),
            order_type: str = typer.Option("market", "--order-type"),
            limit_price: str | None = typer.Option(None, "--limit-price"),
            intent_id: str | None = typer.Option(None, "--intent-id"),
            market_id: str | None = typer.Option(None, "--market-id"),
            workspace: Path = typer.Option(None, "--workspace"),
            output: OutputFormat = typer.Option(
                OutputFormat.TEXT, "--output", "--format"
            ),
        ) -> None:
            owner = WorkspaceApplication().open(workspace)
            account = AccountAdminApplication(owner).show(account_id)
            value = NativeCliApplication(owner).run(
                "execution",
                _execution_submit_args(
                    account,
                    order_id=order_id,
                    account_id=account_id,
                    instrument_id=instrument_id,
                    quantity=_required_decimal(quantity, "quantity"),
                    side=side,
                    order_type=order_type,
                    limit_price=_decimal_option(limit_price, "limit-price"),
                    intent_id=intent_id,
                    market_id=market_id,
                ),
            )
            _emit(value, output)
    elif _command_name == "cancel":

        @order_app.command("cancel")
        def order_cancel(
            order_id: str = typer.Option(..., "--order-id", "--id"),
            workspace: Path = typer.Option(None, "--workspace"),
            reason: str = typer.Option("cli cancel", "--reason"),
            output: OutputFormat = typer.Option(
                OutputFormat.TEXT, "--output", "--format"
            ),
        ) -> None:
            owner = WorkspaceApplication().open(workspace)
            value = NativeCliApplication(owner).run(
                "execution", ["cancel", "--order-id", order_id, "--reason", reason]
            )
            _emit(value, output)
    else:

        @order_app.command("replace")
        def order_replace(
            old_order_id: str = typer.Option(..., "--old-order-id"),
            order_id: str = typer.Option(..., "--order-id", "--id"),
            account_id: str = typer.Option(..., "--account-id"),
            instrument_id: str = typer.Option(..., "--instrument-id"),
            quantity: str = typer.Option(..., "--quantity"),
            side: str = typer.Option("buy", "--side"),
            order_type: str = typer.Option("market", "--order-type"),
            limit_price: str | None = typer.Option(None, "--limit-price"),
            workspace: Path = typer.Option(None, "--workspace"),
            output: OutputFormat = typer.Option(
                OutputFormat.TEXT, "--output", "--format"
            ),
        ) -> None:
            owner = WorkspaceApplication().open(workspace)
            account = AccountAdminApplication(owner).show(account_id)
            replacement = _execution_submit_args(
                account,
                order_id=order_id,
                account_id=account_id,
                instrument_id=instrument_id,
                quantity=_required_decimal(quantity, "quantity"),
                side=side,
                order_type=order_type,
                limit_price=_decimal_option(limit_price, "limit-price"),
            )
            value = NativeCliApplication(owner).run(
                "execution", ["replace", "--order-id", old_order_id, *replacement[1:]]
            )
            _emit(value, output)


system_account_app = _add_group(
    system_app,
    "account",
    (
        "trade-status",
        "current",
        "balances",
        "positions",
        "trade-acquire",
        "trade-release",
    ),
)


@system_app.command("inspect")
def system_inspect(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status(component), output)


@system_app.command("attach")
def system_attach(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(
        {"component": component, "socket": str(owner.paths.process_socket(component))},
        output,
    )


@system_app.command("command")
def system_command(
    component: str = typer.Option(..., "--component"),
    command: str = typer.Option(..., "--command"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    control = ComponentProcessApplication(owner).ensure_running("control")
    _emit(control.command(component, {"type": command}), output)


def _system_account(action: str):
    def command(
        workspace: Path = typer.Option(None, "--workspace"),
        account_id: str | None = typer.Option(None, "--account-id"),
        owner_id: str = typer.Option("cli", "--owner"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        workspace_owner = WorkspaceApplication().open(workspace)
        if action in {"trade-acquire", "trade-release", "trade-status"}:
            lock = TradeLeaseApplication(workspace_owner)
            account = account_id or owner_id
            key = f"binance.{account}"
            if action == "trade-status":
                value = lock.list()
            elif action == "trade-acquire":
                value = lock.acquire(
                    broker="binance",
                    account_id=account,
                    environment="live",
                    launch_id="system",
                    launch_instance_id=owner_id,
                    mode="live",
                )
            else:
                value = lock.release(key, force=True)
        else:
            arguments = ["snapshot"]
            if account_id:
                arguments = ["--account-id", account_id, "snapshot"]
            value = AccountCliApplication(workspace_owner).run(arguments)
        _emit(value, output)

    command.__name__ = f"system_account_{action.replace('-', '_')}"
    return command


for _action in (
    "trade-status",
    "current",
    "balances",
    "positions",
    "trade-acquire",
    "trade-release",
):
    system_account_app.command(_action)(_system_account(_action))


@system_app.command("up")
def system_up(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    account_id: str | None = typer.Option(None, "--account-id"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    if component not in {"reference", "market"}:
        raise typer.BadParameter(
            "system up manages only workspace services: reference and market; "
            "launch starts instance-owned components"
        )
    owner = WorkspaceApplication().open(workspace)
    process = ComponentProcessApplication(owner)
    control = process.ensure_running(
        component,
        account_id=account_id,
        stream_startup_logs=component == "reference"
        and effective_output(output) is OutputFormat.TEXT,
    )
    supervisor = SystemRuntimeSupervisor(process)
    supervisor.register(component, {"account_id": account_id} if account_id else {})
    supervisor.start_background()
    _emit(control.status(), output)


@system_app.command("down")
def system_down(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    if component not in {"reference", "market"}:
        raise typer.BadParameter(
            "system down manages only workspace services: reference and market; "
            "use launch stop for instance-owned components"
        )
    owner = WorkspaceApplication().open(workspace)
    process = ComponentProcessApplication(owner)
    supervisor = SystemRuntimeSupervisor(process)
    supervisor.unregister(component)
    _emit(process.stop(component), output)


@system_app.command("restart")
def system_restart(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    account_id: str | None = typer.Option(None, "--account-id"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    if component not in {"reference", "market"}:
        raise typer.BadParameter(
            "system restart manages only workspace services: reference and market; "
            "use launch start/stop for instance-owned components"
        )
    owner = WorkspaceApplication().open(workspace)
    process = ComponentProcessApplication(owner)
    text_output = effective_output(output) is OutputFormat.TEXT
    control = process.restart(
        component,
        account_id=account_id,
        stream_startup_logs=component == "reference" and text_output,
        progress=typer.echo if text_output else None,
    )
    supervisor = SystemRuntimeSupervisor(process)
    supervisor.register(component, {"account_id": account_id} if account_id else {})
    supervisor.start_background()
    _emit(control.status(), output)


@config_app.command("paths")
def config_paths(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).paths(), output)


@config_app.command("manifest")
def config_manifest(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).manifest(), output)


@config_app.command("show")
def config_show(
    workspace: Path = typer.Option(None, "--workspace"),
    name: str | None = typer.Option(None, "--name"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).show(name), output)


@config_app.command("doctor")
def config_doctor(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).doctor(), output)


@config_app.command("explain")
def config_explain(
    name: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(
        ConfigApplication(WorkspaceApplication().open(workspace)).explain(name), output
    )


@config_app.command("operations")
def config_operations(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(
        ConfigApplication(WorkspaceApplication().open(workspace)).operations(), output
    )


profile_app = typer.Typer(no_args_is_help=True, help="Configuration profiles")
config_app.add_typer(profile_app, name="profile")


@profile_app.command("list")
def profile_list(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).profiles(), output)


@profile_app.command("create")
def profile_create(
    name: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(
        {
            "path": str(
                ConfigApplication(
                    WorkspaceApplication().open(workspace)
                ).create_profile(name)
            )
        },
        output,
    )


@profile_app.command("use")
def profile_use(
    name: str,
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(
        {
            "path": str(
                ConfigApplication(WorkspaceApplication().open(workspace)).use_profile(
                    name
                )
            ),
            "profile": name,
        },
        output,
    )


@config_app.command("status")
def config_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(
        {"workspace_id": owner.workspace_id, "config": str(owner.paths.config)}, output
    )


@system_app.command("status")
def system_status(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    account_id: str | None = typer.Option(None, "--account-id"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status(component), output)


@system_app.command("list")
def system_list(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """List workspace-scoped system components without starting them."""
    output = effective_output(output)
    owner = WorkspaceApplication().open(workspace)
    value = ComponentProcessApplication(owner).list_status()
    if output is OutputFormat.JSON:
        _emit(value, output)
        return
    _emit(
        [
            {
                "component": component,
                "status": status.get("status", "unknown"),
                "pid": status.get("pid", ""),
                "pid_alive": status.get("pid_alive", ""),
                "control_socket": status.get("control_socket", ""),
                "log_file": status.get("log_file", ""),
            }
            for component, status in value.items()
        ],
        OutputFormat.TABLE,
    )


@system_app.command("logs")
def system_logs(
    component_argument: str | None = typer.Argument(
        None,
        metavar="COMPONENT",
        help="Component name (legacy positional form).",
    ),
    component: str | None = typer.Option(
        None,
        "--component",
        help="Component name, for example account or execution.",
    ),
    lines: int = typer.Option(
        100, "--lines", min=0, help="Number of recent lines to show."
    ),
    follow: bool = typer.Option(
        False, "-f", "--follow", help="Continue printing new output."
    ),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Show a component's combined stdout/stderr log."""
    if (
        component is not None
        and component_argument is not None
        and component != component_argument
    ):
        raise typer.BadParameter(
            "component was specified twice with different values; "
            "use --component COMPONENT"
        )
    component = component or component_argument
    if component is None:
        raise typer.BadParameter("component is required; use --component COMPONENT")
    components = {
        "reference",
        "market",
        "account",
        "risk",
        "execution",
        "aeron",
        "system-supervisor",
    }
    if component not in components:
        raise typer.BadParameter(f"unsupported component: {component}")
    if follow and effective_output(output) is not OutputFormat.TEXT:
        raise typer.BadParameter("--follow currently supports text output only")
    owner = WorkspaceApplication().open(workspace)
    path = owner.paths.logs / "processes" / f"{component}.log"
    if effective_output(output) is OutputFormat.JSON:
        value = {
            "component": component,
            "path": str(path),
            "exists": path.is_file(),
            "lines": path.read_text(encoding="utf-8", errors="replace").splitlines()[
                -lines:
            ]
            if path.is_file() and lines
            else [],
        }
        _emit(value, output)
        return
    if path.is_file() and lines:
        typer.echo(
            "\n".join(
                path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
            )
        )
    elif not path.is_file():
        typer.echo(f"log file does not exist: {path}")
    if not follow:
        return
    position = path.stat().st_size if path.is_file() else 0
    try:
        while True:
            if path.is_file():
                with path.open("r", encoding="utf-8", errors="replace") as stream:
                    stream.seek(position)
                    for line in stream:
                        typer.echo(line.rstrip("\n"), color=False)
                    position = stream.tell()
            time.sleep(0.25)
    except KeyboardInterrupt:
        return


@system_app.command("doctor")
def system_doctor(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Diagnose sockets, health files, locks, and unresponsive components."""
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).doctor(), output)


@system_app.command("repair")
def system_repair(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Remove only confirmed stale runtime resources."""
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).repair(), output)


@system_app.command("supervise")
def system_supervise(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    account_id: str | None = typer.Option(None, "--account-id"),
    interval: float = typer.Option(1.0, "--interval", min=0.1),
    once: bool = typer.Option(False, "--once"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    """Run the workspace runtime reconciler for one desired component."""
    if component not in {"reference", "market"}:
        raise typer.BadParameter(
            "system supervise manages only workspace services: reference and market; "
            "launch owns instance components"
        )
    owner = WorkspaceApplication().open(workspace)
    desired: dict[str, object] = {}
    if component == "account" and account_id:
        desired["account_id"] = account_id
    supervisor = SystemRuntimeSupervisor(
        ComponentProcessApplication(owner),
        desired={component: desired},
    )
    value = supervisor.reconcile_once()
    if once:
        _emit(value[component], output)
        return
    supervisor.run_forever(interval=interval)
