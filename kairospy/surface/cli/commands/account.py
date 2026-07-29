from __future__ import annotations

import json
from typing import Mapping

import typer

from kairospy.application.system.facade.account import AccountFacade
from kairospy.surface.cli.options import OutputFormat
from kairospy.surface.cli.output import write_cli_result


account_app = typer.Typer(no_args_is_help=True, help="Configured account commands")
_ACCOUNTS = AccountFacade()


@account_app.command("list")
def list_accounts(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _ACCOUNTS.list_accounts()
    write_cli_result(ctx, payload, output_format=output_format, text=_render_accounts)


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
    provider: str = typer.Argument(...),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.schema(provider)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, text=_render_schema)


@account_app.command("create")
def create_account(
    account_id: str = typer.Argument(...),
    provider: str = typer.Option(..., "--provider"),
    environment: str = typer.Option(..., "--environment"),
    venue: str | None = typer.Option(None, "--venue"),
    market: str | None = typer.Option(None, "--market"),
    currency: str = typer.Option("USD", "--currency"),
    cash: str | None = typer.Option(None, "--cash", help="Initial simulated cash; only written for non-live accounts"),
    fee_rate: str = typer.Option("0", "--fee-rate", help="Commission rate charged on filled notional, for example 0.001"),
    credential_kind: str | None = typer.Option(None, "--credential-kind"),
    credential: str | None = typer.Option(None, "--credential", help="Credential reference, for example env:okx_live"),
    api_key: str | None = typer.Option(None, "--api-key"),
    api_secret: str | None = typer.Option(None, "--api-secret"),
    passphrase: str | None = typer.Option(None, "--passphrase"),
    wallet_address: str | None = typer.Option(None, "--wallet-address"),
    private_key: str | None = typer.Option(None, "--private-key"),
    vault_address: str | None = typer.Option(None, "--vault-address"),
    field_values: list[str] | None = typer.Option(None, "--field", help="Extra account field as key=value"),
    credential_values: list[str] | None = typer.Option(None, "--credential-field", help="Extra credential field as key=value"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    try:
        path = _ACCOUNTS.create(
            account_id=account_id,
            provider=provider,
            environment=environment,
            venue=venue,
            market=market,
            currency=currency,
            cash=cash,
            fee_rate=fee_rate,
            credential_kind=credential_kind,
            credential=credential,
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


@account_app.command("balance")
def balance(
    ctx: typer.Context,
    account_id: str = typer.Argument(...),
    params_json: str | None = typer.Option(None, "--params-json"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ACCOUNTS.balance(account_id, params=_params(params_json))
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@account_app.command("open-orders")
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


@account_app.command("snapshot")
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
                    _display(account.get("provider")),
                    _display(account.get("environment")),
                    _display(account.get("venue")),
                    _display(account.get("market")),
                    _display(_account_value(account, "currency")),
                    _simulated_value(account, "cash"),
                    _simulated_value(account, "fee_rate"),
                    _credential_label(account),
                )
            )
    return "\n".join(["Accounts", *_table(("ID", "PROVIDER", "ENV", "VENUE", "MARKET", "CCY", "CASH", "FEE", "CREDENTIAL"), rows)])


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
            lines.append(
                f"  {schema['provider']:<12} venue={schema['venue']:<12} "
                f"market={schema['default_market']:<5} required={required} optional={optional}"
            )
    return "\n".join(lines)


def _render_schema(result: object) -> str:
    payload = _payload(result)
    return "\n".join([
        f"Account Schema {payload['provider']}",
        f"  venue             {payload['venue']}",
        f"  default_market    {payload['default_market']}",
        f"  credential.kind   {payload['credential_kind']}",
        f"  required          {', '.join(payload['required_credential_fields'])}",
        f"  optional          {', '.join(payload['optional_fields']) or '-'}",
    ])


def _render_show(result: object) -> str:
    payload = _payload(result)
    return "\n".join([
        f"Account {payload['account_id']}",
        f"  provider     {payload['provider']}",
        f"  environment  {payload['environment']}",
        f"  venue        {payload['venue']}",
        f"  market       {payload['market']}",
        f"  source       {payload['source_path']}",
    ])


def _render_doctor(result: object) -> str:
    payload = _payload(result)
    account = payload["account"]
    if not isinstance(account, Mapping):
        raise TypeError("account doctor renderer expected account mapping")
    lines = [f"Account Doctor {account['account_id']}", f"  valid {str(payload['valid']).lower()}"]
    lines.extend(f"  issue {issue}" for issue in payload["issues"])
    return "\n".join(lines)


def _payload(result: object) -> Mapping[str, object]:
    if not isinstance(result, Mapping):
        raise TypeError("account renderer expected mapping payload")
    return result


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
    credential = account.get("credential")
    if isinstance(credential, str) and credential.strip():
        return credential.strip()
    credential_values = account.get("credential_values")
    if isinstance(credential_values, Mapping) and credential_values:
        return "inline"
    return "-"


def _display(value: object) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


__all__ = ["account_app"]
