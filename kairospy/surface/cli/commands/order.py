from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import typer

from kairospy.application.support.launch.application.control import RuntimeMode
from kairospy.application.usecases.execution.application.commands import OrderCommandApplication
from kairospy.application.support.composition.application.cli import build_order_command
from kairospy.application.support.query.browsing import ListQuery
from kairospy.surface.cli.options import OutputFormat
from kairospy.surface.cli.output import write_cli_result
from kairospy.surface.tui import ResourceList, ResourceListBrowser


order_app = typer.Typer(no_args_is_help=True, help="Order commands")
_ORDERS = build_order_command()


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
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


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


@order_app.command("browse")
def browse_orders(
    account_id: str = typer.Option(..., "--account"),
    symbol: str | None = typer.Option(None, "--symbol"),
    limit: int | None = typer.Option(None, "--limit"),
    params_json: str | None = typer.Option(None, "--params-json"),
    page_size: int = typer.Option(20, "--page-size", min=1),
    query: str | None = typer.Option(None, "--query", help="JMESPath expression returning a list of objects."),
) -> None:
    params = _params(params_json)
    try:
        resource = ResourceList.from_rows(
            _open_order_rows(account_id=account_id, symbol=symbol, limit=limit, params=params),
            title="Open Orders",
            query=ListQuery(page_size=page_size, expression=query),
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    ResourceListBrowser(resource).run()


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
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@order_app.command("place")
def place_order(
    ctx: typer.Context,
    account_id: str | None = typer.Option(None, "--account"),
    symbol: str = typer.Option(..., "--symbol"),
    side: str = typer.Option(..., "--side"),
    type_: str = typer.Option("limit", "--type"),
    amount: str = typer.Option(..., "--amount", "--qty"),
    price: str | None = typer.Option(None, "--price"),
    params_json: str | None = typer.Option(None, "--params-json"),
    launch: str | None = typer.Option(None, "--launch", help="Registered launch name or launch id for the launched system session."),
    mode: RuntimeMode | None = typer.Option(None, "--mode", hidden=True),
    launch_id: str | None = typer.Option(None, "--launch-id", hidden=True),
    root: Path | None = typer.Option(None, "--root", hidden=True),
    wait: bool = typer.Option(False, "--wait", help="Wait for a launched system command response."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    submit: bool = typer.Option(False, "--submit"),
    confirm_live: bool = typer.Option(False, "--confirm-live"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        if launch is not None or mode is not None or launch_id is not None:
            payload = _ORDERS.submit_runtime(
                launch=launch,
                mode=mode,
                launch_id=launch_id,
                root=root,
                account_id=account_id,
                symbol=symbol,
                side=side,
                type_=type_,
                amount=amount,
                price=price,
                params=_params(params_json) or {},
                wait=wait,
                timeout_seconds=timeout_seconds,
            )
        else:
            if account_id is None:
                raise ValueError("order place requires --account unless --launch targets a launched system")
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
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@order_app.command("cancel")
def cancel_order(
    ctx: typer.Context,
    account_id: str | None = typer.Option(None, "--account"),
    order_id: str = typer.Option(..., "--order-id"),
    symbol: str | None = typer.Option(None, "--symbol"),
    params_json: str | None = typer.Option(None, "--params-json"),
    launch: str | None = typer.Option(None, "--launch", help="Registered launch name or launch id for the launched system session."),
    mode: RuntimeMode | None = typer.Option(None, "--mode", hidden=True),
    launch_id: str | None = typer.Option(None, "--launch-id", hidden=True),
    root: Path | None = typer.Option(None, "--root", hidden=True),
    wait: bool = typer.Option(False, "--wait", help="Wait for a launched system command response."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    submit: bool = typer.Option(False, "--submit"),
    confirm_live: bool = typer.Option(False, "--confirm-live"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        if launch is not None or mode is not None or launch_id is not None:
            payload = _ORDERS.cancel_runtime(
                launch=launch,
                mode=mode,
                launch_id=launch_id,
                root=root,
                account_id=account_id,
                order_id=order_id,
                symbol=symbol,
                params=_params(params_json) or {},
                wait=wait,
                timeout_seconds=timeout_seconds,
            )
        else:
            if account_id is None:
                raise ValueError("order cancel requires --account unless --launch targets a launched system")
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
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


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
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@order_app.command("show")
def show_order(
    ctx: typer.Context,
    account_id: str | None = typer.Option(None, "--account"),
    order_id: str = typer.Option(..., "--order-id"),
    launch: str | None = typer.Option(None, "--launch", help="Registered launch name or launch id for the launched system session."),
    mode: RuntimeMode | None = typer.Option(None, "--mode", hidden=True),
    launch_id: str | None = typer.Option(None, "--launch-id", hidden=True),
    root: Path | None = typer.Option(None, "--root", hidden=True),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for a launched system command response."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        if launch is not None or mode is not None or launch_id is not None:
            payload = _ORDERS.status_runtime(
                launch=launch,
                mode=mode,
                launch_id=launch_id,
                root=root,
                account_id=account_id,
                order_id=order_id,
                wait=wait,
                timeout_seconds=timeout_seconds,
            )
        else:
            if account_id is None:
                raise ValueError("order show requires --account unless --launch targets a launched system")
            payload = _ORDERS.show(account_id=account_id, order_id=order_id)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@order_app.command("inspect")
def inspect_order(
    ctx: typer.Context,
    account_id: str | None = typer.Option(None, "--account"),
    order_id: str = typer.Option(..., "--order-id"),
    launch: str | None = typer.Option(None, "--launch", help="Registered launch name or launch id for the launched system session."),
    mode: RuntimeMode | None = typer.Option(None, "--mode", hidden=True),
    launch_id: str | None = typer.Option(None, "--launch-id", hidden=True),
    root: Path | None = typer.Option(None, "--root", hidden=True),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for a launched system command response."),
    timeout_seconds: float = typer.Option(5.0, "--timeout"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    show_order(
        ctx=ctx,
        account_id=account_id,
        order_id=order_id,
        launch=launch,
        mode=mode,
        launch_id=launch_id,
        root=root,
        wait=wait,
        timeout_seconds=timeout_seconds,
        output_format=output_format,
    )


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


def _open_order_rows(
    *,
    account_id: str,
    symbol: str | None,
    limit: int | None,
    params: Mapping[str, object] | None,
) -> tuple[Mapping[str, object], ...]:
    payload = _ORDERS.open_orders(account_id=account_id, symbol=symbol, limit=limit, params=params)
    return tuple(row for row in payload.orders if isinstance(row, Mapping))


__all__ = ["order_app"]
