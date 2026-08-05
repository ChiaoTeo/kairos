from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
import time
from typing import Mapping

import typer

from kairospy.application.support.launch.application.control import RuntimeMode
from kairospy.application.support.query.browsing import ListQuery
from kairospy.application.support.composition.application.cli import AccountCommandServices
from kairospy.application.usecases.account.application.results import AccountBalanceResult, AccountPositionsResult
from kairospy.application.support.composition.application.cli import build_account_command
from kairospy.application.support.launch.application.control.facade import DEFAULT_SYSTEM_LAUNCH_ID
from kairospy.application.support.composition.application.launch import launch_application
from kairospy.surface.tui import ResourceList, ResourceListBrowser
from kairospy.surface.cli.options import OutputFormat, resolve_output
from kairospy.surface.cli.output import write_cli_result


account_app = typer.Typer(no_args_is_help=True, help="Configured account commands")
account_credential_app = typer.Typer(no_args_is_help=True, help="ExternalAccount credential commands")
account_query_app = typer.Typer(no_args_is_help=True, help="ExternalAccount query commands")
account_model_app = typer.Typer(no_args_is_help=True, help="ExternalAccount model commands")
account_trade_lock_app = typer.Typer(no_args_is_help=True, help="ExternalAccount trade-lock commands")
account_app.add_typer(account_credential_app, name="credential")
account_app.add_typer(account_query_app, name="query")
account_app.add_typer(account_model_app, name="model")
account_app.add_typer(account_trade_lock_app, name="trade-lock")
_ACCOUNTS = build_account_command()
_RUNS = launch_application()


@account_app.command("list")
def list_accounts(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _ACCOUNTS.administration.list_accounts()
    write_cli_result(ctx, payload, output_format=output_format, text=_render_accounts)


@account_app.command("browse")
def browse_accounts(
    page_size: int = typer.Option(20, "--page-size", min=1),
    query: str | None = typer.Option(None, "--query", help="JMESPath expression returning a list of objects."),
) -> None:
    ResourceListBrowser(
        ResourceList.from_rows(
            _account_rows(_ACCOUNTS),
            title="Accounts",
            query=ListQuery(page_size=page_size, expression=query),
        )
    ).run()


@account_app.command("schemas")
def schemas(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _ACCOUNTS.administration.schemas()
    write_cli_result(ctx, payload, output_format=output_format, text=_render_schemas)


@account_app.command("schema")
def schema(
    ctx: typer.Context,
    broker_name: str = typer.Argument(..., metavar="broker"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.administration.schema(broker_name)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, text=_render_schema)


@account_app.command("inspect")
def inspect_account(
    ctx: typer.Context,
    account_id: str = typer.Argument(...),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.connection.inspect(account_id)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_inspect)


@account_app.command("connect")
def connect_account(
    ctx: typer.Context,
    broker_name: str = typer.Option(..., "--broker", "--provider"),
    environment: str = typer.Option(..., "--environment"),
    credential: str = typer.Option(..., "--credential", help="Credential reference; the remote account is discovered from it."),
    credential_role: str = typer.Option("readonly", "--credential-role", help="Permission role for this credential: readonly or trade."),
    alias: str | None = typer.Option(None, "--alias", help="Optional local binding label; this does not create a broker account."),
    product_family: str | None = typer.Option(None, "--product-family", help="Initial discovery route, for example spot or usd_m_futures."),
    account_model: str | None = typer.Option(None, "--account-model", help="Optional local expectation; actual model must be reconciled from the broker."),
    force: bool = typer.Option(False, "--force"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.connection.connect(
            broker=broker_name,
            environment=environment,
            credential=credential,
            credential_role=credential_role,
            alias=alias,
            product_family=product_family,
            account_model=account_model,
            force=force,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@account_app.command("simulate")
def simulate_account(
    account_id: str = typer.Argument(..., help="Local simulation binding id."),
    broker_name: str = typer.Option("paper", "--broker", "--provider"),
    environment: str = typer.Option("paper", "--environment"),
    product_family: str = typer.Option("spot", "--product-family"),
    account_model: str | None = typer.Option(None, "--account-model"),
    balance: list[str] = typer.Option([], "--balance", help="Initial asset quantity, repeatable: USDT=10000 or BTC=0.5."),
    fee_rate: str = typer.Option("0", "--fee-rate"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    try:
        path = _ACCOUNTS.simulation.provision(
            account_id=account_id,
            broker=broker_name,
            environment=environment,
            venue=broker_name,
            product_family=product_family,
            account_model=account_model,
            initial_balances=balance,
            fee_rate=fee_rate,
            credential_kind=None,
            credential=None,
            credential_role=None,
            api_key=None,
            api_secret=None,
            passphrase=None,
            wallet_address=None,
            private_key=None,
            vault_address=None,
            field_values=None,
            credential_values=None,
            force=force,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(path.path)


def add_credential(
    account_id: str = typer.Argument(...),
    name: str = typer.Argument(...),
    ref: str = typer.Option(..., "--ref", help="Credential id, for example okx_trade"),
    check: bool = typer.Option(True, "--check/--no-check", help="Validate credential permission and account identity before saving."),
    force: bool = typer.Option(False, "--force"),
) -> None:
    try:
        typer.echo(_ACCOUNTS.connection.add_credential(account_id, name=name, ref=ref, check=check, force=force).path)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@account_credential_app.command("add")
def add_account_credential(
    account_id: str = typer.Argument(...),
    name: str = typer.Argument(...),
    ref: str = typer.Option(..., "--ref", help="Credential id, for example okx_trade"),
    check: bool = typer.Option(True, "--check/--no-check", help="Validate credential permission and account identity before saving."),
    force: bool = typer.Option(False, "--force"),
) -> None:
    add_credential(account_id=account_id, name=name, ref=ref, check=check, force=force)


@account_credential_app.command("list")
def list_account_credentials(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    from kairospy.application.usecases.account.application.credentials import CredentialAdminApplication

    payload = CredentialAdminApplication().list_credentials()
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@account_credential_app.command("create")
def create_account_credential(
    credential_id: str = typer.Argument(...),
    broker_name: str = typer.Option(..., "--broker", "--provider"),
    kind: str | None = typer.Option(None, "--kind"),
    api_key: str | None = typer.Option(None, "--api-key"),
    api_secret: str | None = typer.Option(None, "--api-secret"),
    passphrase: str | None = typer.Option(None, "--passphrase"),
    password: str | None = typer.Option(None, "--password"),
    wallet_address: str | None = typer.Option(None, "--wallet-address"),
    private_key: str | None = typer.Option(None, "--private-key"),
    vault_address: str | None = typer.Option(None, "--vault-address"),
    field_values: list[str] | None = typer.Option(None, "--field", help="Extra credential field as key=value"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    from kairospy.application.usecases.account.application.credentials import CredentialAdminApplication

    try:
        typer.echo(
            CredentialAdminApplication().create(
                credential_id=credential_id,
                broker=broker_name,
                kind=kind,
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                password=password,
                wallet_address=wallet_address,
                private_key=private_key,
                vault_address=vault_address,
                field_values=field_values,
                force=force,
            )
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@account_credential_app.command("show")
def show_account_credential(
    ctx: typer.Context,
    credential_id: str = typer.Argument(...),
    reveal_secrets: bool = typer.Option(False, "--reveal-secrets"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    from kairospy.application.usecases.account.application.credentials import CredentialAdminApplication

    try:
        payload = CredentialAdminApplication().show(credential_id, reveal_secrets=reveal_secrets)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@account_credential_app.command("delete")
def delete_account_credential(credential_id: str = typer.Argument(...), force: bool = typer.Option(False, "--force")) -> None:
    from kairospy.application.usecases.account.application.credentials import CredentialAdminApplication

    try:
        typer.echo(CredentialAdminApplication().delete(credential_id, force=force))
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@account_credential_app.command("remove")
def remove_account_credential(credential_id: str = typer.Argument(...), force: bool = typer.Option(False, "--force")) -> None:
    delete_account_credential(credential_id=credential_id, force=force)


@account_app.command("modify")
def modify_account(
    account_id: str = typer.Argument(...),
    broker_name: str | None = typer.Option(None, "--broker", "--provider"),
    environment: str | None = typer.Option(None, "--environment"),
    venue: str | None = typer.Option(None, "--venue"),
    fee_rate: str | None = typer.Option(None, "--fee-rate"),
    credential: str | None = typer.Option(None, "--credential", help="Credential id, for example okx_live"),
    clear_credential: bool = typer.Option(False, "--clear-credential"),
    field_values: list[str] | None = typer.Option(None, "--field", help="Extra account field as key=value"),
) -> None:
    try:
        typer.echo(
            _ACCOUNTS.administration.modify(
                account_id,
                broker=broker_name,
                environment=environment,
                venue=venue,
                fee_rate=fee_rate,
                credential=credential,
                clear_credential=clear_credential,
                field_values=field_values,
            ).path
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@account_app.command("delete")
def delete_account(account_id: str = typer.Argument(...), force: bool = typer.Option(False, "--force")) -> None:
    try:
        typer.echo(_ACCOUNTS.administration.delete(account_id, force=force).path)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@account_app.command("remove")
def remove_account(account_id: str = typer.Argument(...), force: bool = typer.Option(False, "--force")) -> None:
    delete_account(account_id=account_id, force=force)


@account_app.command("show")
def show_account(
    ctx: typer.Context,
    account_id: str = typer.Argument(...),
    reveal_secrets: bool = typer.Option(False, "--reveal-secrets"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.administration.show(account_id, reveal_secrets=reveal_secrets)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_show)


@account_query_app.command("balance")
def balance(
    ctx: typer.Context,
    account_id: str = typer.Argument(...),
    segments: list[str] | None = typer.Option(None, "--segment", help="ExternalAccount segment to query; repeat for multiple segments. Defaults to all configured segments."),
    include_zero: bool = typer.Option(False, "--include-zero", help="Include assets whose free/used/total balances are all zero."),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int = typer.Option(50, "--page-size", min=1),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    resolved_output = resolve_output(ctx, output_format, default=OutputFormat.text)
    progress = _balance_progress_reporter() if resolved_output is OutputFormat.text else None
    try:
        payload = _ACCOUNTS.queries.balance(
            account_id,
            segments=segments,
            include_zero=include_zero,
            page=page,
            page_size=page_size,
            progress=progress,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=resolved_output, default=OutputFormat.text, text=_render_balance)


@account_query_app.command("positions")
def positions(
    ctx: typer.Context,
    account_id: str = typer.Argument(...),
    segments: list[str] | None = typer.Option(None, "--segment", help="ExternalAccount segment to query; repeat for multiple segments."),
    symbol: str | None = typer.Option(None, "--symbol"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.queries.positions(account_id, segments=segments, symbol=symbol)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_positions)


@account_trade_lock_app.command("status")
def trade_status(
    ctx: typer.Context,
    account_key: str | None = typer.Argument(None, help="Optional account id or broker.account key"),
    root: Path | None = typer.Option(None, "--root", hidden=True),
    launch_id: str = typer.Option(DEFAULT_SYSTEM_LAUNCH_ID, "--launch-id", hidden=True),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for the system runtime response."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _system_account_trade_command(
            "account.trade-status",
            account_key=account_key,
            root=root,
            launch_id=launch_id,
            wait=wait,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@account_query_app.command("open-orders")
def open_orders(
    ctx: typer.Context,
    account_id: str = typer.Argument(...),
    symbol: str | None = typer.Option(None, "--symbol"),
    limit: int | None = typer.Option(None, "--limit"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.queries.open_orders(account_id, symbol=symbol, limit=limit)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@account_query_app.command("snapshot")
def snapshot(
    ctx: typer.Context,
    account_id: str = typer.Argument(...),
    symbol: str | None = typer.Option(None, "--symbol"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.queries.snapshot(account_id, symbol=symbol)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@account_model_app.command("switch")
def switch_account_model(
    ctx: typer.Context,
    account_id: str = typer.Argument(...),
    target: str = typer.Option(..., "--target", help="Target AccountModel, for example unified or contract."),
    reason: str = typer.Option("", "--reason"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.model.switch(account_id, target=target, reason=reason)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@account_trade_lock_app.command("list")
def locks(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _ACCOUNTS.leases.list()
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_locks)


@account_trade_lock_app.command("show")
def lock(
    ctx: typer.Context,
    account_id: str = typer.Argument(...),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.leases.status(account_id)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_lock)


@account_trade_lock_app.command("release")
def release_lock(
    ctx: typer.Context,
    account_id: str = typer.Argument(...),
    stale_only: bool = typer.Option(True, "--stale-only/--any"),
    force: bool = typer.Option(False, "--force"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.leases.release(account_id, stale_only=stale_only, force=force)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_release_lock)


@account_app.command("doctor")
def doctor(
    ctx: typer.Context,
    account_id: str = typer.Argument(...),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.administration.doctor(account_id)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, text=_render_doctor)
    if _payload(payload)["issues"]:
        raise typer.Exit(2)


def _balance_progress_reporter():
    started_at: dict[str, float] = {}

    def report(event: Mapping[str, object]) -> None:
        name = str(event.get("event") or "")
        if name == "start":
            segments = ", ".join(str(item) for item in event.get("segments", ()))
            typer.echo(f"Querying balances for {event.get('account')} ({event.get('total')} segments): {segments}", err=True)
            return
        if name == "segment_start":
            segment = str(event.get("segment"))
            started_at[segment] = time.monotonic()
            typer.echo(f"  [{event.get('index')}/{event.get('total')}] {segment} ...", err=True)
            return
        if name in {"segment_done", "segment_error"}:
            segment = str(event.get("segment"))
            elapsed = time.monotonic() - started_at.get(segment, time.monotonic())
            if name == "segment_done":
                typer.echo(f"  [{event.get('index')}/{event.get('total')}] {segment} done ({event.get('rows')} rows, {elapsed:.1f}s)", err=True)
            else:
                typer.echo(
                    f"  [{event.get('index')}/{event.get('total')}] {segment} failed "
                    f"({elapsed:.1f}s, {event.get('diagnostic_id')}): {event.get('error')}",
                    err=True,
                )

    return report


def _system_account_trade_command(
    kind: str,
    *,
    account_key: str | None,
    root: Path | None,
    launch_id: str,
    wait: bool,
    timeout_seconds: float,
) -> dict[str, object]:
    command_payload = {} if account_key is None else {"account": account_key}
    return _RUNS.system_command(
        kind=kind,
        payload=command_payload,
        root=root,
        launch_id=launch_id,
        wait=wait,
        timeout_seconds=timeout_seconds,
    )


def _render_accounts(result: object) -> str:
    payload = _payload(result)
    accounts = payload["accounts"]
    if not accounts:
        return f"Accounts\n  none\n  root {payload['root']}"
    if not isinstance(accounts, list):
        raise TypeError("account list renderer expected account list")
    rows = []
    for account in accounts:
        if isinstance(account, Mapping):
            rows.append(
                (
                    _display(account.get("account_id")),
                    _display(account.get("broker", account.get("provider"))),
                    _display(account.get("environment")),
                    _display(account.get("venue")),
                    _display(account.get("default_segment") or _first_segment_label(account)),
                    _display(account.get("initial_balances") or "-"),
                    _simulated_value(account, "fee_rate"),
                    _credential_label(account),
                    _lock_label(account.get("lock")),
                )
            )
    return "\n".join(["Accounts", *_table(("ID", "BROKER", "ENV", "VENUE", "SCOPE", "ASSETS", "FEE", "CREDENTIAL", "TRADE_LOCK"), rows)])


def _render_schemas(result: object) -> str:
    payload = _payload(result)
    schemas = payload["schemas"]
    if not isinstance(schemas, Mapping):
        raise TypeError("account schemas renderer expected schemas mapping")
    lines = ["ExternalAccount Schemas"]
    for schema in schemas.values():
        if isinstance(schema, Mapping):
            required = ", ".join(schema["required_credential_fields"])
            optional = ", ".join(schema["optional_fields"]) or "-"
            segments = ", ".join(str(segment) for segment in schema.get("balance_segments", ())) or "-"
            lines.append(
                f"  {schema.get('broker', schema.get('provider')):<12} venue={schema['venue']:<12} "
                f"balance_segments={segments:<70} required={required} optional={optional}"
            )
    return "\n".join(lines)


def _render_schema(result: object) -> str:
    payload = _payload(result)
    return "\n".join([
        f"ExternalAccount Schema {payload.get('broker', payload.get('provider'))}",
        f"  venue             {payload['venue']}",
        f"  balance_segments    {', '.join(str(segment) for segment in payload.get('balance_segments', ())) or '-'}",
        f"  credential.kind   {payload['credential_kind']}",
        f"  required          {', '.join(payload['required_credential_fields'])}",
        f"  optional          {', '.join(payload['optional_fields']) or '-'}",
    ])


def _render_show(result: object) -> str:
    payload = _payload(result)
    lines = [
        f"ExternalAccount {payload['account_id']}",
        f"  broker       {payload.get('broker', payload.get('provider'))}",
        f"  environment  {payload['environment']}",
        f"  venue        {payload['venue']}",
        f"  source       {payload['source_path']}",
    ]
    if payload.get("default_segment"):
        lines.insert(4, f"  default_segment  {payload.get('default_segment')}")
    credentials = payload.get("credentials")
    if isinstance(credentials, list) and credentials:
        lines.append("  credentials")
        for credential in credentials:
            if isinstance(credential, Mapping):
                lines.append(f"    {_display(credential.get('name')):<8} {_display(credential.get('ref'))}")
    return "\n".join(lines)


def _render_balance(result: object) -> str:
    if isinstance(result, AccountBalanceResult):
        title = f"Balances  {result.account_id}  segments={', '.join(str(segment.segment_id) for segment in result.segments)}"
        lines = [title]
        table_rows = [
            (
                str(row.segment.segment_id),
                row.balance.currency,
                _display(row.balance.free),
                _display(row.balance.locked),
                _display(row.balance.total),
            )
            for row in result.rows
        ]
        lines.extend(_table(("SCOPE", "ASSET", "FREE", "USED", "TOTAL"), table_rows) if table_rows else ["  none"])
        if result.errors:
            error_rows = [
                (str(error.segment.segment_id), error.error_type, str(error.duration_ms), _display(error.diagnostic_id), error.message)
                for error in result.errors
            ]
            lines.extend(["", "Balance Errors", *_table(("SCOPE", "TYPE", "MS", "DIAGNOSTIC", "ERROR"), error_rows)])
        page = result.page
        lines.append(f"page {page.page}/{page.total_pages}  rows {len(result.rows)}/{page.total_rows}")
        return "\n".join(lines)
    payload = _payload(result)
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise TypeError("account balance renderer expected rows list")
    page = payload.get("page")
    if not isinstance(page, Mapping):
        raise TypeError("account balance renderer expected page mapping")
    title = f"Balances  {payload.get('account')}  segments={', '.join(str(item) for item in payload.get('segments', []))}"
    lines = [title]
    if rows:
        table_rows = []
        for row in rows:
            if isinstance(row, Mapping):
                table_rows.append(
                    (
                        _display(row.get("segment")),
                        _display(row.get("asset")),
                        _display(row.get("free")),
                        _display(row.get("used")),
                        _display(row.get("total")),
                    )
                )
        lines.extend(_table(("SCOPE", "ASSET", "FREE", "USED", "TOTAL"), table_rows))
    else:
        lines.append("  none")
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        error_rows = []
        for error in errors:
            if isinstance(error, Mapping):
                error_rows.append(
                    (
                        _display(error.get("segment")),
                        _display(error.get("error_type")),
                        _display(error.get("duration_ms")),
                        _display(error.get("diagnostic_id")),
                        _display(error.get("error")),
                    )
                )
        lines.extend(["", "Balance Errors", *_table(("SCOPE", "TYPE", "MS", "DIAGNOSTIC", "ERROR"), error_rows)])
    lines.append(
        "page {}/{}  rows {}/{}".format(
            page.get("page"),
            page.get("total_pages"),
            len(rows),
            page.get("total_rows"),
        )
    )
    return "\n".join(lines)


def _render_positions(result: object) -> str:
    if not isinstance(result, AccountPositionsResult):
        return str(_payload(result))
    lines = [f"Positions  {result.account_id}  segments={', '.join(str(segment.segment_id) for segment in result.segments)}"]
    rows = [
        (
            str(row.segment.segment_id),
            str(row.position.instrument_id),
            _display(row.position.quantity),
            _display(row.position.average_price),
            _display(row.position.unrealized_pnl),
            _display(row.position.margin_mode),
        )
        for row in result.rows
    ]
    lines.extend(_table(("SEGMENT", "INSTRUMENT", "QUANTITY", "AVERAGE", "UPNL", "MARGIN"), rows) if rows else ["  none"])
    if result.errors:
        lines.extend(["", "Position Errors", *_table(("SEGMENT", "TYPE", "MS", "DIAGNOSTIC", "ERROR"), [(str(error.segment.segment_id), error.error_type, str(error.duration_ms), _display(error.diagnostic_id), error.message) for error in result.errors])])
    return "\n".join(lines)


def _render_doctor(result: object) -> str:
    payload = _payload(result)
    account = payload["account"]
    if not isinstance(account, Mapping):
        raise TypeError("account doctor renderer expected account mapping")
    lines = [f"ExternalAccount Doctor {account['account_id']}", f"  valid {str(payload['valid']).lower()}"]
    lines.extend(f"  issue {issue}" for issue in payload["issues"])
    return "\n".join(lines)


def _render_inspect(result: object) -> str:
    payload = _payload(result)
    lines = [
        f"ExternalAccount Inspect {payload.get('account_id')}",
        f"  broker             {_display(payload.get('broker'))}",
        f"  remote_identity    {_display(payload.get('remote_identity'))}",
        f"  account_type       {_display(payload.get('account_type'))}",
        f"  observed_model     {_display(payload.get('observed_model'))}",
        f"  permissions        {_display(', '.join(str(item) for item in payload.get('permissions', [])))}",
        f"  configured         {_display(', '.join(str(item) for item in payload.get('configured_segments', [])))}",
        f"  discovered         {_display(', '.join(str(item) for item in payload.get('discovered_segments', [])))}",
    ]
    return "\n".join(lines)


def _render_locks(result: object) -> str:
    payload = _payload(result)
    locks = payload["locks"]
    if not locks:
        return f"ExternalAccount Trade Locks\n  none\n  root {payload['root']}"
    if not isinstance(locks, list):
        raise TypeError("account locks renderer expected list")
    rows = []
    for lock in locks:
        if isinstance(lock, Mapping):
            rows.append(
                (
                    _display(lock.get("account_key")),
                    _display(lock.get("mode")),
                    _display(lock.get("launch_id")),
                    _display(lock.get("launch_instance_id")),
                    "stale" if lock.get("stale") else "active",
                    _display(lock.get("heartbeat_at")),
                )
            )
    return "\n".join(["ExternalAccount Trade Locks", *_table(("ACCOUNT", "MODE", "LAUNCH", "INSTANCE", "STATE", "HEARTBEAT"), rows)])


def _render_lock(result: object) -> str:
    payload = _payload(result)
    lock = payload.get("lock")
    if not isinstance(lock, Mapping):
        return f"ExternalAccount Trade Lock {payload['account']}\n  free"
    return "\n".join(
        [
            f"ExternalAccount Trade Lock {payload['account']}",
            f"  state       {'stale' if lock.get('stale') else 'active'}",
            f"  launch      {lock.get('launch_id')}",
            f"  instance    {lock.get('launch_instance_id')}",
            f"  mode        {lock.get('mode')}",
            f"  heartbeat   {lock.get('heartbeat_at')}",
        ]
    )


def _render_release_lock(result: object) -> str:
    payload = _payload(result)
    return f"ExternalAccount Trade Lock {payload['account']}\n  released {str(payload['released']).lower()}"


def _payload(result: object) -> Mapping[str, object]:
    if is_dataclass(result):
        value = asdict(result)
        if isinstance(value, Mapping):
            return value
    if not isinstance(result, Mapping):
        raise TypeError("account renderer expected mapping payload")
    return result


def _account_rows(facade: AccountCommandServices) -> tuple[Mapping[str, object], ...]:
    payload = _payload(facade.administration.list_accounts())
    rows = payload.get("accounts", ())
    if not isinstance(rows, (tuple, list)):
        return ()
    return tuple(row for row in rows if isinstance(row, Mapping))


def _table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    lines = [
        "  " + "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)),
        "  " + "  ".join("-" * width for width in widths),
    ]
    lines.extend("  " + "  ".join(value.ljust(widths[index]) for index, value in enumerate(row)) for row in rows)
    return lines


def _account_value(account: Mapping[str, object], key: str) -> object:
    values = account.get("values")
    if isinstance(values, Mapping) and key in values:
        return values[key]
    return account.get(key)


def _first_segment_label(account: Mapping[str, object]) -> str:
    segments = account.get("segments")
    if not isinstance(segments, (tuple, list)) or not segments:
        return "-"
    first = segments[0]
    if not isinstance(first, Mapping):
        return "-"
    return str(first.get("product_family") or first.get("model") or "-")


def _simulated_value(account: Mapping[str, object], key: str) -> str:
    environment = str(account.get("environment") or "").strip().lower()
    value = _account_value(account, key)
    if environment == "live" and value in (None, ""):
        return "-"
    return _display(value)


def _credential_label(account: Mapping[str, object]) -> str:
    credentials = account.get("credentials")
    if isinstance(credentials, list) and credentials:
        labels = []
        for credential in credentials:
            if not isinstance(credential, Mapping):
                continue
            name = str(credential.get("name") or "").strip()
            ref = str(credential.get("ref") or "").strip()
            if name and ref:
                labels.append(f"{name}:{ref}")
            elif ref:
                labels.append(ref)
        if labels:
            return ",".join(labels)
    credential = account.get("credential")
    if isinstance(credential, str) and credential.strip():
        return credential.strip()
    credential_values = account.get("credential_values")
    if isinstance(credential_values, Mapping) and credential_values:
        return "inline"
    return "-"


def _lock_label(value: object) -> str:
    if not isinstance(value, Mapping):
        return "free"
    state = "stale" if value.get("stale") else "locked"
    launch = value.get("launch_id")
    return state if not launch else f"{state}:{launch}"


def _display(value: object) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


__all__ = ["account_app"]
