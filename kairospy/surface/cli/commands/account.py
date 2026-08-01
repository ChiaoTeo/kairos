from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from typing import Mapping

import typer

from kairospy.application.support.launch.modes import RuntimeMode
from kairospy.application.support.system.browsing import ListQuery
from kairospy.application.support.system.facade.account import AccountFacade
from kairospy.application.support.launch.control.facade import DEFAULT_SYSTEM_LAUNCH_ID, LaunchFacade
from kairospy.surface.interactive.account import run_account_create_wizard
from kairospy.surface.tui import ResourceList, ResourceListBrowser
from kairospy.surface.cli.options import OutputFormat, resolve_output
from kairospy.surface.cli.output import write_cli_result


account_app = typer.Typer(no_args_is_help=True, help="Configured account commands")
account_credential_app = typer.Typer(no_args_is_help=True, help="Account credential commands")
account_query_app = typer.Typer(no_args_is_help=True, help="Account query commands")
account_trade_lock_app = typer.Typer(no_args_is_help=True, help="Account trade-lock commands")
account_app.add_typer(account_credential_app, name="credential")
account_app.add_typer(account_query_app, name="query")
account_app.add_typer(account_trade_lock_app, name="trade-lock")
_ACCOUNTS = AccountFacade()
_RUNS = LaunchFacade()


@account_app.command("list")
def list_accounts(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _ACCOUNTS.list_accounts()
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
    payload = _ACCOUNTS.schemas()
    write_cli_result(ctx, payload, output_format=output_format, text=_render_schemas)


@account_app.command("schema")
def schema(
    ctx: typer.Context,
    broker_name: str = typer.Argument(..., metavar="broker"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.schema(broker_name)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, text=_render_schema)


@account_app.command("create")
def create_account(
    account_id: str | None = typer.Argument(None),
    broker_name: str | None = typer.Option(None, "--broker", "--provider"),
    environment: str | None = typer.Option(None, "--environment"),
    venue: str | None = typer.Option(None, "--venue"),
    market: str | None = typer.Option(None, "--book", "--market", help="Default account book/product, for example spot, equity, or swap."),
    currency: str = typer.Option("USD", "--currency"),
    cash: str | None = typer.Option(None, "--cash", help="Initial simulated cash; only written for non-live accounts"),
    fee_rate: str = typer.Option("0", "--fee-rate", help="Commission rate charged on filled notional, for example 0.001"),
    credential_kind: str | None = typer.Option(None, "--credential-kind"),
    credential: str | None = typer.Option(None, "--credential", help="Credential id, for example okx_live"),
    credential_role: str = typer.Option("readonly", "--credential-role", help="Credential role when API fields are provided: readonly or trade"),
    api_key: str | None = typer.Option(None, "--api-key"),
    api_secret: str | None = typer.Option(None, "--api-secret"),
    passphrase: str | None = typer.Option(None, "--passphrase"),
    wallet_address: str | None = typer.Option(None, "--wallet-address"),
    private_key: str | None = typer.Option(None, "--private-key"),
    vault_address: str | None = typer.Option(None, "--vault-address"),
    field_values: list[str] | None = typer.Option(None, "--field", help="Extra account field as key=value"),
    credential_values: list[str] | None = typer.Option(None, "--credential-field", help="Extra credential field as key=value"),
    force: bool = typer.Option(False, "--force"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Create the account through an interactive guide."),
    direct: bool = typer.Option(False, "--direct", help="Run the explicit argv form instead of the interactive guide."),
) -> None:
    if interactive and direct:
        raise typer.BadParameter("--interactive and --direct cannot be used together")
    if interactive or (not direct and account_id is None and sys.stdin.isatty() and sys.stdout.isatty()):
        raise typer.Exit(
            run_account_create_wizard(
                prompt=lambda message: typer.prompt(message, prompt_suffix="", default="", show_default=False),
                echo=typer.echo,
                facade=_ACCOUNTS,
            )
        )
    if account_id is None:
        raise typer.BadParameter("account_id is required for direct creation; use --interactive for guided setup")
    if broker_name is None:
        raise typer.BadParameter("--broker is required for direct creation; use --interactive for guided setup")
    if environment is None:
        raise typer.BadParameter("--environment is required for direct creation; use --interactive for guided setup")
    try:
        path = _ACCOUNTS.create(
            account_id=account_id,
            broker=broker_name,
            environment=environment,
            venue=venue,
            market=market,
            currency=currency,
            cash=cash,
            fee_rate=fee_rate,
            credential_kind=credential_kind,
            credential=credential,
            credential_role=credential_role,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            wallet_address=wallet_address,
            private_key=private_key,
            vault_address=vault_address,
            field_values=field_values,
            credential_values=credential_values,
            force=force,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(path)


def add_credential(
    account_id: str = typer.Argument(...),
    name: str = typer.Argument(...),
    ref: str = typer.Option(..., "--ref", help="Credential id, for example okx_trade"),
    check: bool = typer.Option(True, "--check/--no-check", help="Validate credential permission and account identity before saving."),
    force: bool = typer.Option(False, "--force"),
) -> None:
    try:
        typer.echo(_ACCOUNTS.add_credential(account_id, name=name, ref=ref, check=check, force=force))
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
    from kairospy.application.support.system.facade.credential import CredentialFacade

    payload = CredentialFacade().list_credentials()
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
    from kairospy.application.support.system.facade.credential import CredentialFacade

    try:
        typer.echo(
            CredentialFacade().create(
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
    from kairospy.application.support.system.facade.credential import CredentialFacade

    try:
        payload = CredentialFacade().show(credential_id, reveal_secrets=reveal_secrets)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@account_credential_app.command("delete")
def delete_account_credential(credential_id: str = typer.Argument(...), force: bool = typer.Option(False, "--force")) -> None:
    from kairospy.application.support.system.facade.credential import CredentialFacade

    try:
        typer.echo(CredentialFacade().delete(credential_id, force=force))
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
    currency: str | None = typer.Option(None, "--currency"),
    cash: str | None = typer.Option(None, "--cash"),
    fee_rate: str | None = typer.Option(None, "--fee-rate"),
    credential: str | None = typer.Option(None, "--credential", help="Credential id, for example okx_live"),
    clear_credential: bool = typer.Option(False, "--clear-credential"),
    field_values: list[str] | None = typer.Option(None, "--field", help="Extra account field as key=value"),
) -> None:
    try:
        typer.echo(
            _ACCOUNTS.modify(
                account_id,
                broker=broker_name,
                environment=environment,
                venue=venue,
                currency=currency,
                cash=cash,
                fee_rate=fee_rate,
                credential=credential,
                clear_credential=clear_credential,
                field_values=field_values,
            )
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@account_app.command("delete")
def delete_account(account_id: str = typer.Argument(...), force: bool = typer.Option(False, "--force")) -> None:
    try:
        typer.echo(_ACCOUNTS.delete(account_id, force=force))
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
        payload = _ACCOUNTS.show(account_id, reveal_secrets=reveal_secrets)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_show)


@account_query_app.command("balance")
def balance(
    ctx: typer.Context,
    account_id: str = typer.Argument(...),
    books: list[str] | None = typer.Option(None, "--book", help="Account book to query; repeat for multiple books. Defaults to all supported books."),
    include_zero: bool = typer.Option(False, "--include-zero", help="Include assets whose free/used/total balances are all zero."),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int = typer.Option(50, "--page-size", min=1),
    params_json: str | None = typer.Option(None, "--params-json"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    resolved_output = resolve_output(ctx, output_format, default=OutputFormat.text)
    progress = _balance_progress_reporter() if resolved_output is OutputFormat.text else None
    try:
        payload = _ACCOUNTS.balance(
            account_id,
            books=books,
            include_zero=include_zero,
            page=page,
            page_size=page_size,
            params=_params(params_json),
            progress=progress,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=resolved_output, default=OutputFormat.text, text=_render_balance)


@account_query_app.command("current")
def current(
    ctx: typer.Context,
    launch: str | None = typer.Option(None, "--launch", help="Registered launch name or launch id for the launched system session."),
    mode: RuntimeMode | None = typer.Option(None, "--mode", hidden=True),
    launch_id: str | None = typer.Option(None, "--launch-id", hidden=True),
    root: Path | None = typer.Option(None, "--root", hidden=True),
    account_key: str | None = typer.Option(None, "--account", help="Account alias/id when the launch exposes multiple accounts."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _runtime_account_query(
            "account.current",
            launch=launch,
            mode=mode,
            launch_id=launch_id,
            root=root,
            account_key=account_key,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@account_query_app.command("balances")
def balances(
    ctx: typer.Context,
    launch: str | None = typer.Option(None, "--launch", help="Registered launch name or launch id for the launched system session."),
    mode: RuntimeMode | None = typer.Option(None, "--mode", hidden=True),
    launch_id: str | None = typer.Option(None, "--launch-id", hidden=True),
    root: Path | None = typer.Option(None, "--root", hidden=True),
    account_key: str | None = typer.Option(None, "--account", help="Account alias/id when the launch exposes multiple accounts."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _runtime_account_query(
            "account.balances",
            launch=launch,
            mode=mode,
            launch_id=launch_id,
            root=root,
            account_key=account_key,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@account_query_app.command("positions")
def positions(
    ctx: typer.Context,
    launch: str | None = typer.Option(None, "--launch", help="Registered launch name or launch id for the launched system session."),
    mode: RuntimeMode | None = typer.Option(None, "--mode", hidden=True),
    launch_id: str | None = typer.Option(None, "--launch-id", hidden=True),
    root: Path | None = typer.Option(None, "--root", hidden=True),
    account_key: str | None = typer.Option(None, "--account", help="Account alias/id when the launch exposes multiple accounts."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _runtime_account_query(
            "account.positions",
            launch=launch,
            mode=mode,
            launch_id=launch_id,
            root=root,
            account_key=account_key,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


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
    params_json: str | None = typer.Option(None, "--params-json"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.open_orders(account_id, symbol=symbol, limit=limit, params=_params(params_json))
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@account_query_app.command("snapshot")
def snapshot(
    ctx: typer.Context,
    account_id: str = typer.Argument(...),
    symbol: str | None = typer.Option(None, "--symbol"),
    params_json: str | None = typer.Option(None, "--params-json"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.snapshot(account_id, symbol=symbol, params=_params(params_json))
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@account_trade_lock_app.command("list")
def locks(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _ACCOUNTS.locks()
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_locks)


@account_trade_lock_app.command("show")
def lock(
    ctx: typer.Context,
    account_id: str = typer.Argument(...),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.lock(account_id)
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
        payload = _ACCOUNTS.release_lock(account_id, stale_only=stale_only, force=force)
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
        payload = _ACCOUNTS.doctor(account_id)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, text=_render_doctor)
    if payload["issues"]:
        raise typer.Exit(2)


def _params(value: str | None) -> Mapping[str, object] | None:
    if value is None:
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise typer.BadParameter(f"--params-json must be a JSON object: {error}") from error
    if not isinstance(payload, Mapping):
        raise typer.BadParameter("--params-json must be a JSON object")
    return payload


def _balance_progress_reporter():
    started_at: dict[str, float] = {}

    def report(event: Mapping[str, object]) -> None:
        name = str(event.get("event") or "")
        if name == "start":
            books = ", ".join(str(item) for item in event.get("books", ()))
            typer.echo(f"Querying balances for {event.get('account')} ({event.get('total')} books): {books}", err=True)
            return
        if name == "book_start":
            book = str(event.get("book"))
            started_at[book] = time.monotonic()
            typer.echo(f"  [{event.get('index')}/{event.get('total')}] {book} ...", err=True)
            return
        if name in {"book_done", "book_error"}:
            book = str(event.get("book"))
            elapsed = time.monotonic() - started_at.get(book, time.monotonic())
            if name == "book_done":
                typer.echo(f"  [{event.get('index')}/{event.get('total')}] {book} done ({event.get('rows')} rows, {elapsed:.1f}s)", err=True)
            else:
                typer.echo(
                    f"  [{event.get('index')}/{event.get('total')}] {book} failed "
                    f"({elapsed:.1f}s, {event.get('diagnostic_id')}): {event.get('error')}",
                    err=True,
                )

    return report


def _runtime_account_query(
    kind: str,
    *,
    launch: str | None,
    mode: RuntimeMode | None,
    launch_id: str | None,
    root: Path | None,
    account_key: str | None,
    timeout_seconds: float,
) -> dict[str, object]:
    command_payload = {} if account_key is None else {"account": account_key}
    if launch is None and mode is None and launch_id is None:
        return _RUNS.system_command(
            kind=kind,
            payload=command_payload,
            root=root,
            wait=True,
            timeout_seconds=timeout_seconds,
        )
    return _RUNS.submit_command(
        target=launch,
        root=root,
        launch_id=launch_id,
        mode=mode,
        kind=kind,
        payload=command_payload,
        wait=True,
        timeout_seconds=timeout_seconds,
    )


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
                    _display(account.get("market")),
                    _display(_account_value(account, "currency")),
                    _simulated_value(account, "cash"),
                    _simulated_value(account, "fee_rate"),
                    _credential_label(account),
                    _lock_label(account.get("lock")),
                )
            )
    return "\n".join(["Accounts", *_table(("ID", "BROKER", "ENV", "VENUE", "MARKET", "CCY", "CASH", "FEE", "CREDENTIAL", "TRADE_LOCK"), rows)])


def _render_schemas(result: object) -> str:
    payload = _payload(result)
    schemas = payload["schemas"]
    if not isinstance(schemas, Mapping):
        raise TypeError("account schemas renderer expected schemas mapping")
    lines = ["Account Schemas"]
    for schema in schemas.values():
        if isinstance(schema, Mapping):
            required = ", ".join(schema["required_credential_fields"])
            optional = ", ".join(schema["optional_fields"]) or "-"
            books = ", ".join(str(book) for book in schema.get("balance_books", ())) or "-"
            lines.append(
                f"  {schema.get('broker', schema.get('provider')):<12} venue={schema['venue']:<12} "
                f"balance_books={books:<70} required={required} optional={optional}"
            )
    return "\n".join(lines)


def _render_schema(result: object) -> str:
    payload = _payload(result)
    return "\n".join([
        f"Account Schema {payload.get('broker', payload.get('provider'))}",
        f"  venue             {payload['venue']}",
        f"  balance_books     {', '.join(str(book) for book in payload.get('balance_books', ())) or '-'}",
        f"  credential.kind   {payload['credential_kind']}",
        f"  required          {', '.join(payload['required_credential_fields'])}",
        f"  optional          {', '.join(payload['optional_fields']) or '-'}",
    ])


def _render_show(result: object) -> str:
    payload = _payload(result)
    lines = [
        f"Account {payload['account_id']}",
        f"  broker       {payload.get('broker', payload.get('provider'))}",
        f"  environment  {payload['environment']}",
        f"  venue        {payload['venue']}",
        f"  source       {payload['source_path']}",
    ]
    if payload.get("market"):
        lines.insert(4, f"  legacy_book  {payload['market']}")
    credentials = payload.get("credentials")
    if isinstance(credentials, list) and credentials:
        lines.append("  credentials")
        for credential in credentials:
            if isinstance(credential, Mapping):
                lines.append(f"    {_display(credential.get('name')):<8} {_display(credential.get('ref'))}")
    return "\n".join(lines)


def _render_balance(result: object) -> str:
    payload = _payload(result)
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise TypeError("account balance renderer expected rows list")
    page = payload.get("page")
    if not isinstance(page, Mapping):
        raise TypeError("account balance renderer expected page mapping")
    title = f"Balances  {payload.get('account')}  books={', '.join(str(item) for item in payload.get('books', []))}"
    lines = [title]
    if rows:
        table_rows = []
        for row in rows:
            if isinstance(row, Mapping):
                table_rows.append(
                    (
                        _display(row.get("book")),
                        _display(row.get("asset")),
                        _display(row.get("free")),
                        _display(row.get("used")),
                        _display(row.get("total")),
                    )
                )
        lines.extend(_table(("BOOK", "ASSET", "FREE", "USED", "TOTAL"), table_rows))
    else:
        lines.append("  none")
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        error_rows = []
        for error in errors:
            if isinstance(error, Mapping):
                error_rows.append(
                    (
                        _display(error.get("book")),
                        _display(error.get("error_type")),
                        _display(error.get("duration_ms")),
                        _display(error.get("diagnostic_id")),
                        _display(error.get("error")),
                    )
                )
        lines.extend(["", "Balance Errors", *_table(("BOOK", "TYPE", "MS", "DIAGNOSTIC", "ERROR"), error_rows)])
    lines.append(
        "page {}/{}  rows {}/{}".format(
            page.get("page"),
            page.get("total_pages"),
            len(rows),
            page.get("total_rows"),
        )
    )
    return "\n".join(lines)


def _render_doctor(result: object) -> str:
    payload = _payload(result)
    account = payload["account"]
    if not isinstance(account, Mapping):
        raise TypeError("account doctor renderer expected account mapping")
    lines = [f"Account Doctor {account['account_id']}", f"  valid {str(payload['valid']).lower()}"]
    lines.extend(f"  issue {issue}" for issue in payload["issues"])
    return "\n".join(lines)


def _render_locks(result: object) -> str:
    payload = _payload(result)
    locks = payload["locks"]
    if not locks:
        return f"Account Trade Locks\n  none\n  root {payload['root']}"
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
    return "\n".join(["Account Trade Locks", *_table(("ACCOUNT", "MODE", "LAUNCH", "INSTANCE", "STATE", "HEARTBEAT"), rows)])


def _render_lock(result: object) -> str:
    payload = _payload(result)
    lock = payload.get("lock")
    if not isinstance(lock, Mapping):
        return f"Account Trade Lock {payload['account']}\n  free"
    return "\n".join(
        [
            f"Account Trade Lock {payload['account']}",
            f"  state       {'stale' if lock.get('stale') else 'active'}",
            f"  launch      {lock.get('launch_id')}",
            f"  instance    {lock.get('launch_instance_id')}",
            f"  mode        {lock.get('mode')}",
            f"  heartbeat   {lock.get('heartbeat_at')}",
        ]
    )


def _render_release_lock(result: object) -> str:
    payload = _payload(result)
    return f"Account Trade Lock {payload['account']}\n  released {str(payload['released']).lower()}"


def _payload(result: object) -> Mapping[str, object]:
    if not isinstance(result, Mapping):
        raise TypeError("account renderer expected mapping payload")
    return result


def _account_rows(facade: AccountFacade) -> tuple[Mapping[str, object], ...]:
    payload = facade.list_accounts()
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
