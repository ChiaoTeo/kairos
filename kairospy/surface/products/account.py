from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import json
from pathlib import Path
from typing import Mapping

import typer

from kairospy.application.system.workspace import AccountRecord
from kairospy.application.system.workspace import KairosWorkspace
from kairospy.config import ConfigError
from kairospy.surface.runtime import DriverName, ExchangeName, broker


account_app = typer.Typer(no_args_is_help=True, help="Configured account commands")


class OutputFormat(StrEnum):
    json = "json"
    text = "text"


@account_app.command("list")
def list_accounts(output_format: OutputFormat = typer.Option(OutputFormat.text, "--format")) -> None:
    store = KairosWorkspace.resolve().accounts
    accounts = [account.to_dict() for account in store.list()]
    payload = {"accounts": accounts, "count": len(accounts), "root": str(store.root)}
    if output_format is OutputFormat.json:
        _echo(payload)
        return
    if not accounts:
        typer.echo(f"Accounts\n  none\n  root {store.root}")
        return
    lines = ["Accounts"]
    for account in accounts:
        lines.append(
            f"  {account['account_id']}  {account['provider']}:{account['environment']}  "
            f"venue={account['venue']} market={account['market']}"
        )
    typer.echo("\n".join(lines))


@account_app.command("create")
def create_account(
    account_id: str = typer.Argument(...),
    provider: str = typer.Option(..., "--provider"),
    environment: str = typer.Option(..., "--environment"),
    venue: str | None = typer.Option(None, "--venue"),
    market: str | None = typer.Option(None, "--market"),
    currency: str = typer.Option("USD", "--currency"),
    credential_kind: str | None = typer.Option(None, "--credential-kind"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    workspace = KairosWorkspace.resolve()
    path = workspace.accounts_root / f"{account_id}.toml"
    if path.exists() and not force:
        raise typer.BadParameter(f"account already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _account_template(
            account_id,
            provider=provider,
            environment=environment,
            venue=venue or provider,
            market=market,
            currency=currency,
            credential_kind=credential_kind,
        ),
        encoding="utf-8",
    )
    workspace.operations.append(
        "account.create",
        target={"account": account_id},
        payload={"path": path, "provider": provider, "environment": environment, "venue": venue or provider, "market": market},
    )
    typer.echo(str(path))


@account_app.command("show")
def show_account(
    account_id: str = typer.Argument(...),
    reveal_secrets: bool = typer.Option(False, "--reveal-secrets"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    try:
        account = KairosWorkspace.resolve().accounts.get(account_id)
    except ConfigError as error:
        raise typer.BadParameter(str(error)) from error
    payload = account.to_dict(include_secret_values=reveal_secrets)
    if output_format is OutputFormat.json:
        _echo(payload)
        return
    lines = [
        f"Account {payload['account_id']}",
        f"  provider     {payload['provider']}",
        f"  environment  {payload['environment']}",
        f"  venue        {payload['venue']}",
        f"  market       {payload['market']}",
        f"  source       {payload['source_path']}",
    ]
    typer.echo("\n".join(lines))


@account_app.command("balance")
def balance(
    account_id: str = typer.Argument(...),
    params_json: str | None = typer.Option(None, "--params-json"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    account = _account(account_id)
    payload = {
        "account": account.account_id,
        "balance": _broker(account).fetch_balance(params=_params(params_json)),
    }
    _echo(payload) if output_format is OutputFormat.json else typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


@account_app.command("open-orders")
def open_orders(
    account_id: str = typer.Argument(...),
    symbol: str | None = typer.Option(None, "--symbol"),
    limit: int | None = typer.Option(None, "--limit"),
    params_json: str | None = typer.Option(None, "--params-json"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    account = _account(account_id)
    orders = tuple(_broker(account).fetch_open_orders(symbol, limit=limit, params=_params(params_json)))
    payload = {"account": account.account_id, "orders": orders, "count": len(orders)}
    _echo(payload) if output_format is OutputFormat.json else typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


@account_app.command("snapshot")
def snapshot(
    account_id: str = typer.Argument(...),
    symbol: str | None = typer.Option(None, "--symbol"),
    params_json: str | None = typer.Option(None, "--params-json"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    workspace = KairosWorkspace.resolve()
    account = _account(account_id)
    client = _broker(account)
    params = _params(params_json)
    payload = {
        "account": account.account_id,
        "event_time": datetime.now(timezone.utc).isoformat(),
        "balance": client.fetch_balance(params=params),
        "open_orders": tuple(client.fetch_open_orders(symbol, params=params)),
    }
    path = workspace.workspace_root / "accounts" / "journals" / f"{account.account_id}.jsonl"
    _append_jsonl(path, payload)
    workspace.operations.append("account.snapshot", target={"account": account.account_id}, payload={"journal": path})
    _echo(payload) if output_format is OutputFormat.json else typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


@account_app.command("doctor")
def doctor(
    account_id: str = typer.Argument(...),
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--format"),
) -> None:
    account = _account(account_id)
    issues: list[str] = []
    if not account.provider:
        issues.append("provider is required")
    if account.environment == "live" and not account.credential_values and not account.credential:
        issues.append("live account has no credential metadata")
    if account.credential_values:
        kind = account.credential_values.get("kind")
        if kind == "api_key_secret":
            for key in ("api_key", "api_secret"):
                value = account.credential_values.get(key)
                if not isinstance(value, str) or not value.strip():
                    issues.append(f"credential.{key} is empty")
    payload = {"account": account.to_dict(), "valid": not issues, "issues": issues}
    if output_format is OutputFormat.json:
        _echo(payload)
    else:
        lines = [f"Account Doctor {account.account_id}", f"  valid {str(not issues).lower()}"]
        lines.extend(f"  issue {issue}" for issue in issues)
        typer.echo("\n".join(lines))
    if issues:
        raise typer.Exit(2)


def _account(account_id: str) -> AccountRecord:
    try:
        return KairosWorkspace.resolve().accounts.get(account_id)
    except ConfigError as error:
        raise typer.BadParameter(str(error)) from error


def _broker(account: AccountRecord):
    return broker(_exchange(account), DriverName.ccxt, credential=account.credential)


def _exchange(account: AccountRecord) -> ExchangeName:
    value = (account.venue or account.provider).strip().lower()
    try:
        return ExchangeName(value)
    except ValueError as error:
        raise typer.BadParameter(f"unsupported account venue/provider: {value}") from error


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


def _account_template(
    account_id: str,
    *,
    provider: str,
    environment: str,
    venue: str,
    market: str | None,
    currency: str,
    credential_kind: str | None,
) -> str:
    lines = [
        "[account]",
        f'id = "{account_id}"',
        f'provider = "{provider}"',
        f'environment = "{environment}"',
        f'venue = "{venue}"',
    ]
    if market is not None:
        lines.append(f'market = "{market}"')
    lines.append(f'currency = "{currency}"')
    if credential_kind is not None:
        lines.extend(
            [
                "",
                "[credential]",
                f'kind = "{credential_kind}"',
                'api_key = ""',
                'api_secret = ""',
                "ip_bound = true",
            ]
        )
    return "\n".join(lines) + "\n"


def _echo(payload: Mapping[str, object]) -> None:
    typer.echo(json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True))


def _append_jsonl(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(_jsonable(payload), sort_keys=True) + "\n")


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = ["account_app"]
