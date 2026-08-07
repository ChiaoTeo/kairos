from __future__ import annotations

from pathlib import Path

import typer

from kairospy.application.system import (
    ComponentControlApplication,
    ComponentProcessApplication,
    NativeCliApplication,
)
from decimal import Decimal
from decimal import InvalidOperation
from kairospy.application.timeline import TimelineApplication
from kairospy.application.config import ConfigApplication
from kairospy.application.account import AccountAdminApplication, AccountCliApplication, CredentialApplication, TradeLeaseApplication
from kairospy.application.market import MarketCliApplication, MarketDataApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli.options import OutputFormat, render


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


def _decimal_payload(value: Decimal) -> tuple[int, int]:
    normalized = value.normalize()
    scale = max(0, -normalized.as_tuple().exponent)
    return int(normalized * (10 ** scale)), scale


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
    provider = str(account.get("broker") or account.get("venue") or "simulated")
    provider = "okx" if provider == "okex" else provider
    segment = str(account.get("product_family") or account.get("segment") or "spot")
    quantity_mantissa, quantity_scale = _decimal_payload(quantity)
    arguments = [
        "submit", "--order-id", order_id, "--account-id", account_id,
        "--segment-key", segment, "--instrument-id", instrument_id,
        "--quantity-mantissa", str(quantity_mantissa), "--quantity-scale", str(quantity_scale),
        "--side", side, "--order-type", order_type,
        "--provider", provider, "--product", segment,
    ]
    credential = account.get("credential")
    if credential:
        arguments.extend(("--credential-id", str(credential)))
    environment = str(account.get("environment") or "paper").lower()
    if environment == "live":
        arguments.append("--confirm-live")
    if limit_price is not None:
        mantissa, scale = _decimal_payload(limit_price)
        arguments.extend(("--limit-price-mantissa", str(mantissa), "--limit-price-scale", str(scale)))
    if intent_id:
        arguments.extend(("--intent-id", intent_id))
    if market_id:
        arguments.extend(("--market-id", market_id))
    return arguments


def _client(component: str, workspace: Path, *, account_id: str | None = None) -> ComponentControlApplication:
    owner = WorkspaceApplication().open(workspace)
    process = {"account": "account", "market": "market", "order": "execution"}.get(component)
    if process is None:
        raise typer.BadParameter(f"unsupported component: {component}")
    return ComponentProcessApplication(owner).ensure_running(process, account_id=account_id)


def _status_command(component: str):
    def status(
        workspace: Path = typer.Option(None, "--workspace"),
        account_id: str | None = typer.Option(None, "--account-id"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        native = "execution" if component == "order" else "account"
        arguments = ["snapshot"]
        if native == "account" and account_id:
            arguments = ["--account-id", account_id, "snapshot"]
        value = AccountCliApplication(owner).run(arguments) if native == "account" else NativeCliApplication(owner).run(native, arguments)
        _emit(value, output)

    status.__name__ = f"{component}_status"
    return status


def _socket_action(component: str, action: str):
    def command(
        workspace: Path = typer.Option(None, "--workspace"),
        account_id: str | None = typer.Option(None, "--account-id"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        client = _client(component, workspace, account_id=account_id)
        operation = {
            "snapshot": client.snapshot,
            "refresh": client.refresh,
            "stop": client.stop,
        }.get(action)
        if operation is None:
            raise typer.BadParameter(f"unsupported component action: {action}")
        _emit(operation(), output)

    command.__name__ = f"component_{action}"
    return command


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


def _add_group(parent: typer.Typer, name: str, commands: tuple[str, ...]) -> typer.Typer:
    group = typer.Typer(no_args_is_help=True, help=f"{name} commands")
    parent.add_typer(group, name=name)
    del commands
    return group


project_app = typer.Typer(no_args_is_help=True, help="Project commands")
config_app = typer.Typer(no_args_is_help=True, help="Configuration commands")
account_app = typer.Typer(no_args_is_help=True, help="Account commands")
market_app = typer.Typer(no_args_is_help=True, help="Market commands")
order_app = typer.Typer(no_args_is_help=True, help="Order commands")
system_app = typer.Typer(no_args_is_help=True, help="System runtime commands")
timeline_app = typer.Typer(no_args_is_help=True, help="Timeline commands")


@project_app.command("init")
def project_init(
    root: Path | None = typer.Argument(None, help="Project directory (prompted when omitted)"),
    workspace_id: str | None = typer.Option(None, "--id"),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Do not prompt; require the project directory and --id",
    ),
) -> None:
    if root is None:
        if non_interactive:
            raise typer.BadParameter("project directory is required with --non-interactive")
        root = Path(typer.prompt("项目目录", default="."))
    else:
        root = Path(root)

    default_id = root.expanduser().resolve().name
    if workspace_id is None:
        if non_interactive:
            raise typer.BadParameter("--id is required with --non-interactive")
        workspace_id = typer.prompt("项目名", default=default_id)

    workspace = WorkspaceApplication().init_project(root, workspace_id=workspace_id)
    _emit({"status": "initialized", "workspace_id": workspace.workspace_id, "root": str(workspace.paths.root)}, OutputFormat.JSON)


@project_app.command("status")
def project_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    value = WorkspaceApplication().open(workspace)
    _emit({"workspace_id": value.workspace_id, "root": str(value.paths.root)}, output)


@project_app.command("doctor")
def project_doctor(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).doctor(), output)


# Keep the established top-level names as thin input adapters. Module use
# cases are invoked through their application-owned CLI/application paths.
for _app, _name in (
    (account_app, "account"), (order_app, "order"),
):
    _app.command("status")(_status_command(_name))

for _command_name in ("snapshot", "refresh", "stop"):
    account_app.command(_command_name)(_socket_action("account", _command_name))


def _market_remote_action(action: str):
    def command(
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        client = ComponentProcessApplication(owner).ensure_running("market")
        operation = {
            "status": client.status,
            "snapshot": client.snapshot,
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


for _command_name in ("status", "snapshot", "refresh", "recover", "stop"):
    market_app.command(_command_name)(_market_remote_action(_command_name))


@market_app.command("validate")
def market_validate(
    market_id: str = typer.Option("market:binance:spot:BTCUSDT", "--market-id"),
    instrument_id: str = typer.Option("instrument:binance:spot:BTCUSDT", "--instrument-id"),
    venue_id: str = typer.Option("binance", "--venue-id"),
    market_type: str = typer.Option("spot", "--market-type"),
    source_symbol: str = typer.Option("BTCUSDT", "--source-symbol"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    value = MarketCliApplication().run([
        "validate", "--market-id", market_id, "--instrument-id", instrument_id,
        "--venue-id", venue_id, "--market-type", market_type, "--source-symbol", source_symbol,
    ])
    _emit(value, output)


@market_app.command("once")
def market_once(
    provider: str = typer.Option("binance-spot-rest", "--provider"),
    endpoint: str = typer.Option("https://api.binance.com", "--endpoint"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    value = MarketCliApplication().run(["once", "--provider", provider, "--endpoint", endpoint])
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
        balance: list[str] = typer.Option([], "--balance", help="Initial simulated asset quantity, e.g. USDT=10000."),
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
            value = app.connect(account_id, broker=broker or "binance", segment=segment or "spot", environment=environment or "live", credential=credential, credential_role=credential_role, alias=alias, product_family=product_family, account_model=account_model, force=force)
        elif action == "simulate":
            if not account_id:
                raise typer.BadParameter("--account-id is required")
            value = app.simulate(account_id, broker=broker or "paper", segment=segment or "spot", environment=environment or "paper", account_model=account_model, initial_balances=tuple(balance), fee_rate=fee_rate or "0", force=force)
        elif action == "modify":
            if not account_id:
                raise typer.BadParameter("--account-id is required")
            changes = {key: value for key, value in {"broker": broker, "segment": segment, "environment": environment, "credential": credential, "credential_role": credential_role, "alias": alias, "product_family": product_family, "account_model": account_model, "fee_rate": fee_rate, "initial_balances": balance or None}.items() if value is not None}
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


for _command_name in ("list", "browse", "schemas", "schema", "inspect", "connect", "simulate", "modify", "delete", "remove", "show", "doctor"):
    account_app.command(_command_name)(_account_admin(_command_name))
account_credential_app = _add_group(account_app, "credential", ("add", "list", "create", "show", "delete", "remove"))
account_query_app = _add_group(account_app, "query", ("balance", "positions", "open-orders", "snapshot"))
account_trade_lock_app = _add_group(account_app, "trade-lock", ("status", "list", "show", "release"))
account_model_app = _add_group(account_app, "model", ("switch",))


def _account_query(view: str):
    def command(
        workspace: Path = typer.Option(None, "--workspace"),
        account_id: str | None = typer.Option(None, "--account-id"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        arguments = {"balance": "balances", "positions": "positions", "open-orders": "open-orders", "snapshot": "snapshot"}[view]
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
            value = app.acquire(broker=broker, account_id=account, environment=environment, launch_id=launch_id, launch_instance_id=launch_instance_id, mode=mode)
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
        api_key: str | None = typer.Option(None, "--api-key", help="Secret value is never persisted; use an external secret store."),
        api_secret: str | None = typer.Option(None, "--api-secret", help="Secret value is never persisted; use an external secret store."),
        passphrase: str | None = typer.Option(None, "--passphrase", help="Secret value is never persisted; use an external secret store."),
        field: list[str] = typer.Option([], "--field"),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        app = CredentialApplication(WorkspaceApplication().open(workspace))
        if action == "list": value = app.list()
        elif action in {"add", "create"}:
            if not credential_id: raise typer.BadParameter("--credential-id is required")
            secret_fields = tuple(field) + tuple(name for name, secret in (("api_key", api_key), ("api_secret", api_secret), ("passphrase", passphrase)) if secret is not None)
            value = app.add(credential_id, provider=provider, kind=kind, fields=secret_fields)
        elif action == "show":
            if not credential_id: raise typer.BadParameter("--credential-id is required")
            value = app.show(credential_id)
        else:
            if not credential_id: raise typer.BadParameter("--credential-id is required")
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
    value = AccountAdminApplication(WorkspaceApplication().open(workspace)).switch_model(account_id, model)
    _emit(value, output)

market_source_app = _add_group(market_app, "source", ("capabilities", "check", "doctor"))
market_data_app = _add_group(market_app, "data", ("download", "prefetch"))
market_dataset_app = _add_group(market_app, "dataset", ("list", "inspect", "alias", "prune", "read"))
market_stream_app = _add_group(market_app, "stream", ("replay", "watch", "persist"))
def _market_source(action: str):
    def command(workspace: Path = typer.Option(None, "--workspace"), output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format")) -> None:
        owner = WorkspaceApplication().open(workspace)
        value = ComponentProcessApplication(owner).ensure_running("market").status()
        value["operation"] = action
        _emit(value, output)
    command.__name__ = f"market_source_{action}"
    return command


for _action in ("capabilities", "check", "doctor"):
    market_source_app.command(_action)(_market_source(_action))


def _market_data(action: str):
    def command(
        name: str = typer.Option(..., "--name"),
        source_file: Path = typer.Option(..., "--source-file"),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        owner = WorkspaceApplication().open(workspace)
        value = MarketDataApplication(owner.paths.state / "market").ingest(name, source_file)
        value["operation"] = action
        _emit(value, output)
    command.__name__ = f"market_data_{action}"
    return command


for _action in ("download", "prefetch"):
    market_data_app.command(_action)(_market_data(_action))


def _market_dataset(action: str):
    def command(
        name: str | None = typer.Option(None, "--name"),
        alias: str | None = typer.Option(None, "--alias"),
        workspace: Path = typer.Option(None, "--workspace"),
        output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
    ) -> None:
        app = MarketDataApplication(WorkspaceApplication().open(workspace).paths.state / "market")
        if action == "list":
            value = app.list()
        elif action == "inspect":
            if not name: raise typer.BadParameter("--name is required")
            value = app.inspect(name)
        elif action == "alias":
            if not name or not alias: raise typer.BadParameter("--name and --alias are required")
            value = app.alias(name, alias)
        elif action == "prune":
            if not name: raise typer.BadParameter("--name is required")
            value = app.prune(name)
        else:
            if not name: raise typer.BadParameter("--name is required")
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
        app = MarketDataApplication(WorkspaceApplication().open(workspace).paths.state / "market")
        _emit({"operation": action, "name": name, "content": app.read(name)}, output)
    command.__name__ = f"market_stream_{action}"
    return command


for _action in ("replay", "watch", "persist"):
    market_stream_app.command(_action)(_market_stream(_action))
for _command_name in ("events", "trace", "open", "list", "browse", "history", "closed", "show", "inspect"):
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
            output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
        ) -> None:
            owner = WorkspaceApplication().open(workspace)
            account = AccountAdminApplication(owner).show(account_id)
            value = NativeCliApplication(owner).run("execution", _execution_submit_args(
                account, order_id=order_id, account_id=account_id, instrument_id=instrument_id,
                quantity=_required_decimal(quantity, "quantity"), side=side, order_type=order_type,
                limit_price=_decimal_option(limit_price, "limit-price"), intent_id=intent_id,
                market_id=market_id,
            ))
            _emit(value, output)
    elif _command_name == "cancel":
        @order_app.command("cancel")
        def order_cancel(
            order_id: str = typer.Option(..., "--order-id", "--id"),
            workspace: Path = typer.Option(None, "--workspace"),
            reason: str = typer.Option("cli cancel", "--reason"),
            output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
        ) -> None:
            owner = WorkspaceApplication().open(workspace)
            value = NativeCliApplication(owner).run("execution", ["cancel", "--order-id", order_id, "--reason", reason])
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
            output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
        ) -> None:
            owner = WorkspaceApplication().open(workspace)
            account = AccountAdminApplication(owner).show(account_id)
            replacement = _execution_submit_args(
                account, order_id=order_id, account_id=account_id, instrument_id=instrument_id,
                quantity=_required_decimal(quantity, "quantity"), side=side, order_type=order_type,
                limit_price=_decimal_option(limit_price, "limit-price"),
            )
            value = NativeCliApplication(owner).run("execution", ["replace", "--order-id", old_order_id, *replacement[1:]])
            _emit(value, output)
system_account_app = _add_group(system_app, "account", ("trade-status", "current", "balances", "positions", "trade-acquire", "trade-release"))


@system_app.command("inspect")
def system_inspect(component: str = typer.Option(..., "--component"), workspace: Path = typer.Option(None, "--workspace"), output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format")) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status(component), output)


@system_app.command("attach")
def system_attach(component: str = typer.Option(..., "--component"), workspace: Path = typer.Option(None, "--workspace"), output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format")) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit({"component": component, "socket": str(owner.paths.process_socket(component))}, output)


@system_app.command("command")
def system_command(component: str = typer.Option(..., "--component"), command: str = typer.Option(..., "--command"), workspace: Path = typer.Option(None, "--workspace"), output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format")) -> None:
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
                value = lock.acquire(broker="binance", account_id=account, environment="live", launch_id="system", launch_instance_id=owner_id, mode="live")
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


for _action in ("trade-status", "current", "balances", "positions", "trade-acquire", "trade-release"):
    system_account_app.command(_action)(_system_account(_action))


@system_app.command("up")
def system_up(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    account_id: str | None = typer.Option(None, "--account-id"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    control = ComponentProcessApplication(owner).ensure_running(component, account_id=account_id)
    _emit(control.status(), output)


@system_app.command("down")
def system_down(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).stop(component), output)


@system_app.command("restart")
def system_restart(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    account_id: str | None = typer.Option(None, "--account-id"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    process = ComponentProcessApplication(owner)
    try:
        process.stop(component)
    except (OSError, RuntimeError, ValueError):
        pass
    _emit(process.ensure_running(component, account_id=account_id).status(), output)
@config_app.command("paths")
def config_paths(workspace: Path = typer.Option(None, "--workspace"), output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format")) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).paths(), output)


@config_app.command("manifest")
def config_manifest(workspace: Path = typer.Option(None, "--workspace"), output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format")) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).manifest(), output)


@config_app.command("show")
def config_show(workspace: Path = typer.Option(None, "--workspace"), name: str | None = typer.Option(None, "--name"), output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format")) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).show(name), output)


@config_app.command("doctor")
def config_doctor(workspace: Path = typer.Option(None, "--workspace"), output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format")) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).doctor(), output)


@config_app.command("explain")
def config_explain(name: str, workspace: Path = typer.Option(None, "--workspace"), output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format")) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).explain(name), output)


@config_app.command("operations")
def config_operations(workspace: Path = typer.Option(None, "--workspace"), output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format")) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).operations(), output)


profile_app = typer.Typer(no_args_is_help=True, help="Configuration profiles")
config_app.add_typer(profile_app, name="profile")


@profile_app.command("list")
def profile_list(workspace: Path = typer.Option(None, "--workspace"), output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format")) -> None:
    _emit(ConfigApplication(WorkspaceApplication().open(workspace)).profiles(), output)


@profile_app.command("create")
def profile_create(name: str, workspace: Path = typer.Option(None, "--workspace"), output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format")) -> None:
    _emit({"path": str(ConfigApplication(WorkspaceApplication().open(workspace)).create_profile(name))}, output)


@profile_app.command("use")
def profile_use(name: str, workspace: Path = typer.Option(None, "--workspace"), output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format")) -> None:
    _emit({"path": str(ConfigApplication(WorkspaceApplication().open(workspace)).use_profile(name)), "profile": name}, output)


@config_app.command("status")
def config_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit({"workspace_id": owner.workspace_id, "config": str(owner.paths.config)}, output)


@timeline_app.command("status")
def timeline_status(
    workspace: Path = typer.Option(None, "--workspace"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit({"workspace_id": owner.workspace_id, "timeline_root": str(owner.paths.run)}, output)


@system_app.command("status")
def system_status(
    component: str = typer.Option(..., "--component"),
    workspace: Path = typer.Option(None, "--workspace"),
    account_id: str | None = typer.Option(None, "--account-id"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    owner = WorkspaceApplication().open(workspace)
    _emit(ComponentProcessApplication(owner).status(component), output)
@timeline_app.command("list")
def timeline_list(
    file: Path = typer.Option(..., "--file"),
    limit: int | None = typer.Option(None, "--limit"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit(TimelineApplication().list(file, limit=limit), output)


@timeline_app.command("export")
def timeline_export(
    file: Path = typer.Option(..., "--file"),
    destination: Path = typer.Option(..., "--destination", "--output-file"),
    output: OutputFormat = typer.Option(OutputFormat.TEXT, "--output", "--format"),
) -> None:
    _emit({"destination": str(TimelineApplication().export(file, destination))}, output)
