from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import json
from pathlib import Path
from typing import Mapping

import typer

from kairospy.application.system.workspace import AccountRecord, KairosWorkspace
from kairospy.config import ConfigError
from kairospy.surface.runtime import DriverName, ExchangeName, broker


order_app = typer.Typer(no_args_is_help=True, help="Order commands")


class OutputFormat(StrEnum):
    json = "json"
    text = "text"


@order_app.command("open")
def open_orders(
    account_id: str = typer.Option(..., "--account"),
    symbol: str | None = typer.Option(None, "--symbol"),
    limit: int | None = typer.Option(None, "--limit"),
    params_json: str | None = typer.Option(None, "--params-json"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    workspace, account = _account(account_id)
    client = _broker(account)
    rows = tuple(client.fetch_open_orders(symbol, limit=limit, params=_params(params_json)))
    payload = {"account": account.account_id, "orders": rows, "count": len(rows)}
    _write_journal(workspace, account, "open", {"symbol": symbol, "limit": limit}, payload)
    _echo(payload, output_format=output_format)


@order_app.command("list")
def list_orders(
    account_id: str = typer.Option(..., "--account"),
    symbol: str | None = typer.Option(None, "--symbol"),
    limit: int | None = typer.Option(None, "--limit"),
    params_json: str | None = typer.Option(None, "--params-json"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    open_orders(
        account_id=account_id,
        symbol=symbol,
        limit=limit,
        params_json=params_json,
        output_format=output_format,
    )


@order_app.command("place")
def place_order(
    account_id: str = typer.Option(..., "--account"),
    symbol: str = typer.Option(..., "--symbol"),
    side: str = typer.Option(..., "--side"),
    type_: str = typer.Option("limit", "--type"),
    amount: str = typer.Option(..., "--amount", "--qty"),
    price: str | None = typer.Option(None, "--price"),
    params_json: str | None = typer.Option(None, "--params-json"),
    submit: bool = typer.Option(False, "--submit"),
    confirm_live: bool = typer.Option(False, "--confirm-live"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    workspace, account = _account(account_id)
    request = {
        "account": account.account_id,
        "symbol": symbol,
        "side": side,
        "type": type_,
        "amount": amount,
        "price": price,
        "params": _params(params_json) or {},
    }
    if not submit:
        payload = {"dry_run": True, "request": request}
        _write_journal(workspace, account, "place_dry_run", request, payload)
        _echo(payload, output_format=output_format)
        return
    _require_live_confirmation(account, confirm_live=confirm_live)
    client = _broker(account)
    result = client.create_order(symbol, side=side, type=type_, amount=amount, price=price, params=request["params"])
    payload = {"dry_run": False, "request": request, "result": result}
    _write_journal(workspace, account, "place", request, payload)
    _echo(payload, output_format=output_format)


@order_app.command("cancel")
def cancel_order(
    account_id: str = typer.Option(..., "--account"),
    order_id: str = typer.Option(..., "--order-id"),
    symbol: str | None = typer.Option(None, "--symbol"),
    params_json: str | None = typer.Option(None, "--params-json"),
    submit: bool = typer.Option(False, "--submit"),
    confirm_live: bool = typer.Option(False, "--confirm-live"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    workspace, account = _account(account_id)
    request = {
        "account": account.account_id,
        "order_id": order_id,
        "symbol": symbol,
        "params": _params(params_json) or {},
    }
    if not submit:
        payload = {"dry_run": True, "request": request}
        _write_journal(workspace, account, "cancel_dry_run", request, payload)
        _echo(payload, output_format=output_format)
        return
    _require_live_confirmation(account, confirm_live=confirm_live)
    client = _broker(account)
    result = client.cancel_order(order_id, symbol=symbol, params=request["params"])
    payload = {"dry_run": False, "request": request, "result": result}
    _write_journal(workspace, account, "cancel", request, payload)
    _echo(payload, output_format=output_format)


@order_app.command("replace")
def replace_order(
    account_id: str = typer.Option(..., "--account"),
    order_id: str = typer.Option(..., "--order-id"),
    symbol: str = typer.Option(..., "--symbol"),
    side: str = typer.Option(..., "--side"),
    type_: str = typer.Option("limit", "--type"),
    amount: str = typer.Option(..., "--amount", "--qty"),
    price: str | None = typer.Option(None, "--price"),
    params_json: str | None = typer.Option(None, "--params-json"),
    submit: bool = typer.Option(False, "--submit"),
    confirm_live: bool = typer.Option(False, "--confirm-live"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    workspace, account = _account(account_id)
    request = {
        "account": account.account_id,
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "type": type_,
        "amount": amount,
        "price": price,
        "params": _params(params_json) or {},
    }
    if not submit:
        payload = {"dry_run": True, "request": request}
        _write_journal(workspace, account, "replace_dry_run", request, payload)
        _echo(payload, output_format=output_format)
        return
    _require_live_confirmation(account, confirm_live=confirm_live)
    client = _broker(account)
    cancel_result = client.cancel_order(order_id, symbol=symbol, params=request["params"])
    create_result = client.create_order(symbol, side=side, type=type_, amount=amount, price=price, params=request["params"])
    payload = {"dry_run": False, "request": request, "result": {"cancel": cancel_result, "create": create_result}}
    _write_journal(workspace, account, "replace", request, payload)
    _echo(payload, output_format=output_format)


@order_app.command("show")
def show_order(
    account_id: str = typer.Option(..., "--account"),
    order_id: str = typer.Option(..., "--order-id"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    workspace, account = _account(account_id)
    journal = workspace.workspace_root / "orders" / "journals" / f"{account.account_id}.jsonl"
    matches = [
        row
        for row in _read_jsonl(journal)
        if _contains_order_id(row, order_id)
    ]
    payload = {
        "account": account.account_id,
        "order_id": order_id,
        "journal": str(journal),
        "records": matches,
        "count": len(matches),
    }
    if not matches:
        raise typer.BadParameter(f"order was not found in local journal: {order_id}")
    _echo(payload, output_format=output_format)


@order_app.command("inspect")
def inspect_order(
    account_id: str = typer.Option(..., "--account"),
    order_id: str = typer.Option(..., "--order-id"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    show_order(account_id=account_id, order_id=order_id, output_format=output_format)


def _account(account_id: str) -> tuple[KairosWorkspace, AccountRecord]:
    workspace = KairosWorkspace.resolve()
    try:
        return workspace, workspace.accounts.get(account_id)
    except ConfigError as error:
        raise typer.BadParameter(str(error)) from error


def _broker(account: AccountRecord):
    return broker(_exchange(account), DriverName.ccxt, credential=account.credential)


def _exchange(account: AccountRecord) -> ExchangeName:
    value = (account.venue or account.provider).strip().lower()
    try:
        return ExchangeName(value)
    except ValueError as error:
        raise typer.BadParameter(f"unsupported order account venue/provider: {value}") from error


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


def _require_live_confirmation(account: AccountRecord, *, confirm_live: bool) -> None:
    if account.environment == "live" and not confirm_live:
        raise typer.BadParameter("live order submission requires --confirm-live")


def _write_journal(
    workspace: KairosWorkspace,
    account: AccountRecord,
    action: str,
    request: Mapping[str, object],
    payload: Mapping[str, object],
) -> None:
    root = workspace.workspace_root / "orders" / "journals"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{account.account_id}.jsonl"
    event = {
        "event_time": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "account": account.account_id,
        "request": request,
        "payload": _jsonable(payload),
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, sort_keys=True) + "\n")
    workspace.operations.append(
        f"order.{action}",
        target={"account": account.account_id},
        payload={"journal": path, "request": request, "dry_run": bool(payload.get("dry_run", False))},
    )


def _echo(payload: Mapping[str, object], *, output_format: OutputFormat) -> None:
    if output_format is OutputFormat.json:
        typer.echo(json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True))
        return
    typer.echo(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True))


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


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _contains_order_id(value: object, order_id: str) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_order_id(item, order_id) for item in value.values())
    if isinstance(value, (tuple, list)):
        return any(_contains_order_id(item, order_id) for item in value)
    return isinstance(value, str) and value == order_id


__all__ = ["order_app"]
