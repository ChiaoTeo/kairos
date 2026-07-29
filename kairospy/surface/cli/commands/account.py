from __future__ import annotations

import json
from typing import Mapping

import typer

from kairospy.application.system.facade.account import AccountFacade
from kairospy.surface.cli.options import OutputFormat, resolve_output
from kairospy.surface.rendering.writer import write_result


account_app = typer.Typer(no_args_is_help=True, help="Configured account commands")
_ACCOUNTS = AccountFacade()


@account_app.command("list")
def list_accounts(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _ACCOUNTS.list_accounts()
    write_result(payload, output=resolve_output(ctx, output_format), text=_render_accounts)


@account_app.command("schemas")
def schemas(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _ACCOUNTS.schemas()
    write_result(payload, output=resolve_output(ctx, output_format), text=_render_schemas)


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
    write_result(payload, output=resolve_output(ctx, output_format), text=_render_schema)


@account_app.command("create")
def create_account(
    account_id: str = typer.Argument(...),
    provider: str = typer.Option(..., "--provider"),
    environment: str = typer.Option(..., "--environment"),
    venue: str | None = typer.Option(None, "--venue"),
    market: str | None = typer.Option(None, "--market"),
    currency: str = typer.Option("USD", "--currency"),
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
    write_result(payload, output=resolve_output(ctx, output_format, default=OutputFormat.json), text=_render_show)


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
    write_result(payload, output=resolve_output(ctx, output_format, default=OutputFormat.json))


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
    write_result(payload, output=resolve_output(ctx, output_format, default=OutputFormat.json))


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
    write_result(payload, output=resolve_output(ctx, output_format, default=OutputFormat.json))


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
    write_result(payload, output=resolve_output(ctx, output_format), text=_render_doctor)
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


def _echo(payload: Mapping[str, object]) -> None:
    write_result(payload, output=OutputFormat.json)


def _render_accounts(result: object) -> str:
    payload = _payload(result)
    accounts = payload["accounts"]
    if not accounts:
        return f"Accounts\n  none\n  root {payload['root']}"
    if not isinstance(accounts, list):
        raise TypeError("account list renderer expected account list")
    lines = ["Accounts"]
    for account in accounts:
        if isinstance(account, Mapping):
            lines.append(
                f"  {account['account_id']}  {account['provider']}:{account['environment']}  "
                f"venue={account['venue']} market={account['market']}"
            )
    return "\n".join(lines)


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


__all__ = ["account_app"]
