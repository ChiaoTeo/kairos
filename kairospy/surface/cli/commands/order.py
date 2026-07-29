from __future__ import annotations

import json
from typing import Mapping

import typer

from kairospy.application.system.facade.order import OrderFacade
from kairospy.surface.cli.options import OutputFormat, resolve_output
from kairospy.surface.rendering.writer import write_result


order_app = typer.Typer(no_args_is_help=True, help="Order commands")
_ORDERS = OrderFacade()


@order_app.command("open")
def open_orders(
    ctx: typer.Context,
    account_id: str = typer.Option(..., "--account"),
    symbol: str | None = typer.Option(None, "--symbol"),
    limit: int | None = typer.Option(None, "--limit"),
    params_json: str | None = typer.Option(None, "--params-json"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ORDERS.open_orders(account_id=account_id, symbol=symbol, limit=limit, params=_params(params_json))
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_result(payload, output=resolve_output(ctx, output_format, default=OutputFormat.json))


@order_app.command("list")
def list_orders(
    ctx: typer.Context,
    account_id: str = typer.Option(..., "--account"),
    symbol: str | None = typer.Option(None, "--symbol"),
    limit: int | None = typer.Option(None, "--limit"),
    params_json: str | None = typer.Option(None, "--params-json"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    open_orders(ctx=ctx, account_id=account_id, symbol=symbol, limit=limit, params_json=params_json, output_format=output_format)


@order_app.command("history")
@order_app.command("closed")
def history_orders(
    ctx: typer.Context,
    account_id: str = typer.Option(..., "--account"),
    symbol: str | None = typer.Option(None, "--symbol"),
    since: str | None = typer.Option(None, "--since", help="ISO-8601 time or milliseconds"),
    limit: int | None = typer.Option(None, "--limit"),
    params_json: str | None = typer.Option(None, "--params-json"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ORDERS.history(account_id=account_id, symbol=symbol, since=since, limit=limit, params=_params(params_json))
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_result(payload, output=resolve_output(ctx, output_format, default=OutputFormat.json))


@order_app.command("place")
def place_order(
    ctx: typer.Context,
    account_id: str = typer.Option(..., "--account"),
    symbol: str = typer.Option(..., "--symbol"),
    side: str = typer.Option(..., "--side"),
    type_: str = typer.Option("limit", "--type"),
    amount: str = typer.Option(..., "--amount", "--qty"),
    price: str | None = typer.Option(None, "--price"),
    params_json: str | None = typer.Option(None, "--params-json"),
    submit: bool = typer.Option(False, "--submit"),
    confirm_live: bool = typer.Option(False, "--confirm-live"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ORDERS.place(
            account_id=account_id,
            symbol=symbol,
            side=side,
            type_=type_,
            amount=amount,
            price=price,
            params=_params(params_json) or {},
            submit=submit,
            confirm_live=confirm_live,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_result(payload, output=resolve_output(ctx, output_format, default=OutputFormat.json))


@order_app.command("cancel")
def cancel_order(
    ctx: typer.Context,
    account_id: str = typer.Option(..., "--account"),
    order_id: str = typer.Option(..., "--order-id"),
    symbol: str | None = typer.Option(None, "--symbol"),
    params_json: str | None = typer.Option(None, "--params-json"),
    submit: bool = typer.Option(False, "--submit"),
    confirm_live: bool = typer.Option(False, "--confirm-live"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ORDERS.cancel(
            account_id=account_id,
            order_id=order_id,
            symbol=symbol,
            params=_params(params_json) or {},
            submit=submit,
            confirm_live=confirm_live,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_result(payload, output=resolve_output(ctx, output_format, default=OutputFormat.json))


@order_app.command("replace")
def replace_order(
    ctx: typer.Context,
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
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ORDERS.replace(
            account_id=account_id,
            order_id=order_id,
            symbol=symbol,
            side=side,
            type_=type_,
            amount=amount,
            price=price,
            params=_params(params_json) or {},
            submit=submit,
            confirm_live=confirm_live,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_result(payload, output=resolve_output(ctx, output_format, default=OutputFormat.json))


@order_app.command("show")
def show_order(
    ctx: typer.Context,
    account_id: str = typer.Option(..., "--account"),
    order_id: str = typer.Option(..., "--order-id"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _ORDERS.show(account_id=account_id, order_id=order_id)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_result(payload, output=resolve_output(ctx, output_format, default=OutputFormat.json))


@order_app.command("inspect")
def inspect_order(
    ctx: typer.Context,
    account_id: str = typer.Option(..., "--account"),
    order_id: str = typer.Option(..., "--order-id"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    show_order(ctx=ctx, account_id=account_id, order_id=order_id, output_format=output_format)


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


__all__ = ["order_app"]
