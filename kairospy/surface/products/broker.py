from __future__ import annotations

import asyncio
from enum import StrEnum
import json
import os
from pathlib import Path
from typing import AsyncIterator, Mapping

import typer

from kairospy.config import ConfigError, load_run_config
from kairospy.core.reference import MarketResolver
from kairospy.surface.runtime import DriverName, ExchangeName, broker, exchange


broker_app = typer.Typer(no_args_is_help=True, help="Broker and account commands")


class OutputFormat(StrEnum):
    json = "json"
    text = "text"


@broker_app.command("balance")
def balance(
    exchange_name: ExchangeName = typer.Option(..., "--exchange"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    credential: str | None = typer.Option(None, "--credential"),
    params_json: str | None = typer.Option(None, "--params-json"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    client = broker(exchange_name, driver_name, credential=credential)
    payload = client.fetch_balance(params=_params(params_json))
    _echo(payload, output_format=output_format)


@broker_app.command("open-orders")
def open_orders(
    symbol: str | None = typer.Option(None, "--symbol"),
    exchange_name: ExchangeName = typer.Option(..., "--exchange"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    credential: str | None = typer.Option(None, "--credential"),
    limit: int | None = typer.Option(None, "--limit"),
    params_json: str | None = typer.Option(None, "--params-json"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    client = broker(exchange_name, driver_name, credential=credential)
    rows = tuple(client.fetch_open_orders(symbol, limit=limit, params=_params(params_json)))
    _echo(rows, output_format=output_format)


@broker_app.command("preflight")
def preflight(
    config_path: Path = typer.Option(..., "--config"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    watch_private: bool = typer.Option(False, "--watch-private"),
    watch_timeout_seconds: float = typer.Option(5.0, "--watch-timeout-seconds"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--format"),
) -> None:
    try:
        run_config = load_run_config(config_path)
        run_config.require_mode("live")
        live_config = _table(run_config.values.get("live"), "live")
    except (ConfigError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    venue = str(live_config.get("venue") or "").strip()
    symbol = str(live_config.get("symbol") or "").strip()
    market = str(live_config.get("market") or "spot")
    if not venue or not symbol:
        raise typer.BadParameter("live.venue and live.symbol are required")
    account = _preflight_account(run_config.accounts, venue)
    balance_params = _merged_params(live_config.get("balance_params"), default={"type": market})
    order_params = _merged_params(live_config.get("order_params"), default={"type": market})
    stream_params = _merged_params(live_config.get("stream"), default={"type": market})
    safety = _table(live_config.get("safety"), "live.safety") if live_config.get("safety") is not None else {}
    exchange_name = _exchange_name(venue)
    market_ref = MarketResolver(default_venue=venue, default_market=market).resolve(symbol)
    market_client = exchange(exchange_name, driver_name)
    client = broker(exchange_name, driver_name, credential=account.credential)
    quote_check = _checked_call(lambda: market_client.fetch_quote(market_ref, params=stream_params))
    balance_check = _checked_call(lambda: client.fetch_balance(params=balance_params))
    open_orders_check = _checked_call(lambda: tuple(client.fetch_open_orders(symbol, params=order_params)))
    payload = {
        "run_id": run_config.run_id,
        "venue": venue,
        "market": market,
        "symbol": symbol,
        "account_count": len(run_config.accounts),
        "account": {
            "account_id": account.account_id,
            "credential": account.credential,
        },
        "credentials": _credential_status(venue, account.credential),
        "safety": {
            "trading_enabled": bool(safety.get("trading_enabled", False)),
            "require_limit_orders": bool(safety.get("require_limit_orders", True)),
            "max_order_notional": safety.get("max_order_notional"),
        },
        "quote": quote_check,
        "balance": balance_check,
        "open_orders": open_orders_check,
    }
    if watch_private:
        private_streams = asyncio.run(
            _private_stream_checks(
                client,
                symbol=symbol,
                balance_params=balance_params,
                order_params=order_params,
                timeout_seconds=watch_timeout_seconds,
            )
        )
        payload["private_streams"] = private_streams
    _echo(payload, output_format=output_format)
    if _preflight_failed(payload):
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


def _merged_params(value: object, *, default: Mapping[str, object]) -> Mapping[str, object]:
    values = dict(default)
    if value is None:
        return values
    if not isinstance(value, Mapping):
        raise typer.BadParameter("live params must be TOML tables")
    values.update(value)
    return values


def _table(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise typer.BadParameter(f"[{name}] must be a table")
    return value


def _exchange_name(value: str) -> ExchangeName:
    try:
        return ExchangeName(value)
    except ValueError as error:
        raise typer.BadParameter(f"unsupported broker exchange: {value}") from error


def _preflight_account(accounts: Mapping[str, object], venue: str):
    matches = tuple(account for account in accounts.values() if getattr(account, "venue", None) == venue)
    if not matches:
        raise typer.BadParameter(f"no configured account for live venue: {venue}")
    if len(matches) > 1:
        raise typer.BadParameter(f"multiple accounts configured for live venue {venue}; preflight requires one")
    return matches[0]


def _credential_status(venue: str, credential: str | None = None) -> Mapping[str, bool]:
    normalized = venue.strip().lower()
    if normalized in {"okx", "okex"}:
        prefix = _credential_env_prefix(credential)
        if prefix is not None:
            return {
                f"{prefix}_API_KEY": _has_env(f"{prefix}_API_KEY"),
                f"{prefix}_SECRET": _has_env(f"{prefix}_SECRET"),
                f"{prefix}_PASSWORD": _has_env(f"{prefix}_PASSWORD"),
                "fallback_OKX_API_KEY": _has_env("OKX_API_KEY", "OKEX_API_KEY"),
                "fallback_OKX_SECRET": _has_env("OKX_SECRET", "OKEX_SECRET"),
                "fallback_OKX_PASSWORD": _has_env("OKX_PASSWORD", "OKEX_PASSWORD", "OKX_PASSPHRASE"),
            }
        return {
            "OKX_API_KEY": _has_env("OKX_API_KEY", "OKEX_API_KEY"),
            "OKX_SECRET": _has_env("OKX_SECRET", "OKEX_SECRET"),
            "OKX_PASSWORD": _has_env("OKX_PASSWORD", "OKEX_PASSWORD", "OKX_PASSPHRASE"),
        }
    return {}


def _has_env(*names: str) -> bool:
    return any(bool(os.environ.get(name)) for name in names)


def _credential_env_prefix(credential: str | None) -> str | None:
    if credential is None:
        return None
    value = credential.strip()
    if not value.startswith("env:"):
        return None
    name = value.split(":", 1)[1].strip()
    if not name:
        return None
    normalized = "".join(char.upper() if char.isalnum() else "_" for char in name)
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_") or None


def _checked_call(call):
    try:
        return {"ok": True, "data": call()}
    except Exception as error:
        return {
            "ok": False,
            "error_type": type(error).__name__,
            "error": str(error),
        }


def _preflight_failed(payload: Mapping[str, object]) -> bool:
    for key in ("quote", "balance", "open_orders"):
        value = payload.get(key)
        if isinstance(value, Mapping) and value.get("ok") is False:
            return True
    private = payload.get("private_streams")
    if isinstance(private, Mapping):
        for value in private.values():
            if isinstance(value, Mapping) and value.get("ok") is False:
                return True
    return False


async def _private_stream_checks(
    client: object,
    *,
    symbol: str,
    balance_params: Mapping[str, object],
    order_params: Mapping[str, object],
    timeout_seconds: float,
) -> Mapping[str, object]:
    if timeout_seconds <= 0:
        raise typer.BadParameter("--watch-timeout-seconds must be positive")
    return {
        "balance": await _first_private_event(
            "balance",
            client.watch_balance(params=balance_params),
            timeout_seconds=timeout_seconds,
        ),
        "orders": await _first_private_event(
            "orders",
            client.watch_orders(symbol, params=order_params),
            timeout_seconds=timeout_seconds,
        ),
        "my_trades": await _first_private_event(
            "my_trades",
            client.watch_my_trades(symbol, params=order_params),
            timeout_seconds=timeout_seconds,
        ),
    }


async def _first_private_event(
    name: str,
    events: AsyncIterator[Mapping[str, object]],
    *,
    timeout_seconds: float,
) -> Mapping[str, object]:
    try:
        event = await asyncio.wait_for(events.__anext__(), timeout=timeout_seconds)
    except StopAsyncIteration:
        return {"ok": True, "status": "empty"}
    except TimeoutError:
        return {"ok": False, "status": "timeout"}
    except Exception as error:
        return {"ok": False, "status": "error", "error_type": type(error).__name__, "error": str(error)}
    finally:
        close = getattr(events, "aclose", None)
        if callable(close):
            await close()
    return {"ok": True, "status": "event", "event": dict(event), "stream": name}


def _echo(value: object, *, output_format: OutputFormat) -> None:
    if output_format is OutputFormat.json:
        typer.echo(json.dumps(_jsonable(value), sort_keys=True))
        return
    typer.echo(_render_text(value))


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _render_text(value: object) -> str:
    if isinstance(value, Mapping):
        return "\n".join(f"{key}: {item}" for key, item in value.items())
    if isinstance(value, (tuple, list)):
        if not value:
            return "No open orders"
        return "\n".join(str(item) for item in value)
    return str(value)
