from __future__ import annotations

from datetime import datetime, timezone
import sys

import typer

from kairospy.core.reference import MarketStatus
from kairospy.application.service.domain.reference import (
    refresh_equity_provider,
    refresh_instrument_provider,
    refresh_instrument_provider_with_delist_schedule,
    sync_lifecycle_events,
)
from kairospy.application.service.domain.reference.serde import (
    asset_to_primitive,
    instrument_to_primitive,
    lifecycle_event_to_primitive,
    listing_to_primitive,
    market_to_primitive,
)
from kairospy.surface.runtime import DriverName, ExchangeName, ProviderName, exchange, provider, reference_store
from kairospy.surface.ui.terminal import write_jsonl


reference_app = typer.Typer(no_args_is_help=True, help="Reference catalog commands")
refresh_app = typer.Typer(no_args_is_help=True, help="Refresh reference catalogs from providers")
catalog_app = typer.Typer(no_args_is_help=True, help="Reference catalog inspection commands")
reference_app.add_typer(refresh_app, name="refresh")
reference_app.add_typer(catalog_app, name="catalog")


@refresh_app.callback(invoke_without_command=True)
def refresh(
    ctx: typer.Context,
    provider_name: str | None = typer.Option(None, "--provider"),
    root: str | None = typer.Option(None, "--root"),
    venue: str | None = typer.Option(None, "--venue"),
    market: str | None = typer.Option(None, "--market"),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    if provider_name is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()
    at = _time(as_of)
    provider_key = provider_name.strip().lower()
    if provider_key == "massive":
        result = refresh_equity_provider(
            reference_store(root),
            provider(ProviderName.massive, DriverName.massive),
            as_of=at,
            venue=venue,
            params={"asset_class": "equity"},
        ).refresh
        write_jsonl((
            {
                "time": at.isoformat(),
                "provider": provider_key,
                "market": market or "equity",
                "previous_markets": len(result.previous_markets),
                "current_markets": len(result.current_markets),
                "events": len(result.events),
            },
        ), sys.stdout)
        return
    if provider_key == "hyperliquid":
        result = refresh_instrument_provider(
            reference_store(root),
            exchange(ExchangeName.hyperliquid, DriverName.ccxt),
            as_of=at,
            venue=ExchangeName.hyperliquid.value,
            market=market,
        ).refresh
        write_jsonl((
            {
                "time": at.isoformat(),
                "provider": provider_key,
                "market": market or "all",
                "previous_markets": len(result.previous_markets),
                "current_markets": len(result.current_markets),
                "events": len(result.events),
            },
        ), sys.stdout)
        return
    raise typer.BadParameter(f"unsupported reference provider: {provider_name}")


@refresh_app.command("hyperliquid")
def refresh_hyperliquid(
    root: str | None = typer.Option(None, "--root"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    market: str | None = typer.Option(None, "--market"),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    at = _time(as_of)
    result = refresh_instrument_provider(
        reference_store(root),
        exchange(ExchangeName.hyperliquid, driver_name),
        as_of=at,
        venue=ExchangeName.hyperliquid.value,
        market=market,
    ).refresh
    write_jsonl((
        {
            "time": at.isoformat(),
            "venue": ExchangeName.hyperliquid.value,
            "market": market or "all",
            "previous_markets": len(result.previous_markets),
            "current_markets": len(result.current_markets),
            "events": len(result.events),
        },
    ), sys.stdout)


@reference_app.command("refresh-binance")
def refresh_binance(
    root: str | None = typer.Option(None, "--root"),
    exchange_name: ExchangeName = typer.Option(ExchangeName.binance, "--exchange"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    market: str = typer.Option("spot", "--market"),
    include_delist_schedule: bool = typer.Option(True, "--include-delist-schedule/--no-delist-schedule"),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    if exchange_name is not ExchangeName.binance:
        raise typer.BadParameter("refresh-binance only supports binance")
    at = _time(as_of)
    store = reference_store(root)
    provider_result = refresh_instrument_provider_with_delist_schedule(
        store,
        exchange(exchange_name, driver_name),
        as_of=at,
        venue=exchange_name.value,
        market=market,
        params={"type": market},
        include_delist_schedule=include_delist_schedule,
    )
    result = provider_result.refresh
    write_jsonl((
        {
            "time": at.isoformat(),
            "venue": exchange_name.value,
            "market": market,
            "previous_markets": len(result.previous_markets),
            "current_markets": len(result.current_markets),
            "events": len(result.events),
            "scheduled_events": len(provider_result.scheduled_events),
        },
    ), sys.stdout)


@reference_app.command("refresh-massive-equities")
def refresh_massive_equities(
    root: str | None = typer.Option(None, "--root"),
    driver_name: DriverName = typer.Option(DriverName.massive, "--driver"),
    venue: str | None = typer.Option(None, "--venue"),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    at = _time(as_of)
    result = refresh_equity_provider(
        reference_store(root),
        provider(ProviderName.massive, driver_name),
        as_of=at,
        venue=venue,
        params={"asset_class": "equity"},
    ).refresh
    write_jsonl((
        {
            "time": at.isoformat(),
            "provider": "massive",
            "market": "equity",
            "previous_markets": len(result.previous_markets),
            "current_markets": len(result.current_markets),
            "events": len(result.events),
        },
    ), sys.stdout)


@reference_app.command("sync-massive-actions")
def sync_massive_actions(
    ticker: str = typer.Option(..., "--ticker"),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    root: str | None = typer.Option(None, "--root"),
    driver_name: DriverName = typer.Option(DriverName.massive, "--driver"),
    venue: str | None = typer.Option(None, "--venue"),
) -> None:
    start_at = _time(start)
    end_at = _time(end)
    if end_at <= start_at:
        raise typer.BadParameter("end must be after start")
    try:
        events = sync_lifecycle_events(
            reference_store(root),
            provider(ProviderName.massive, driver_name),
            ticker=ticker,
            start=start_at,
            end=end_at,
            venue=venue,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_jsonl((
        {
            "provider": "massive",
            "ticker": ticker.upper(),
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
            "events": len(events),
        },
    ), sys.stdout)


@reference_app.command("markets")
def markets(
    root: str | None = typer.Option(None, "--root"),
    venue: str | None = typer.Option(None, "--venue"),
    market: str | None = typer.Option(None, "--market"),
    status: MarketStatus | None = typer.Option(None, "--status"),
    active_only: bool = typer.Option(False, "--active-only"),
    as_of: str | None = typer.Option(None, "--as-of"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    _write_markets(
        root=root,
        venue=venue,
        market=market,
        status=status,
        active_only=active_only,
        as_of=as_of,
        limit=limit,
    )


@reference_app.command("list")
def list_markets(
    root: str | None = typer.Option(None, "--root"),
    venue: str | None = typer.Option(None, "--venue"),
    market: str | None = typer.Option(None, "--market"),
    status: MarketStatus | None = typer.Option(None, "--status"),
    active_only: bool = typer.Option(False, "--active-only"),
    as_of: str | None = typer.Option(None, "--as-of"),
    limit: int | None = typer.Option(50, "--limit"),
) -> None:
    _write_markets(
        root=root,
        venue=venue,
        market=market,
        status=status,
        active_only=active_only,
        as_of=as_of,
        limit=limit,
    )


@reference_app.command("events")
def events(
    root: str | None = typer.Option(None, "--root"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    rows = [lifecycle_event_to_primitive(item) for item in reference_store(root).load_events()]
    if limit is not None:
        rows = rows[:limit]
    write_jsonl(rows, sys.stdout)


@reference_app.command("search")
def search(
    query: str = typer.Argument(...),
    root: str | None = typer.Option(None, "--root"),
    as_of: str | None = typer.Option(None, "--as-of"),
    limit: int = typer.Option(50, "--limit"),
) -> None:
    at = _time(as_of)
    needle = query.casefold()
    catalog = reference_store(root).load_catalog()
    rows: list[dict[str, object]] = []
    for item in catalog.list_markets(at=at):
        if _matches(needle, str(item.market_id), str(item.instrument_id), str(item.listing_id), item.venue, item.market, item.source_symbol):
            rows.append({"kind": "market", **market_to_primitive(item)})
    for item in catalog.active_listings(at=at):
        if _matches(needle, str(item.listing_id), str(item.instrument_id), item.venue, item.trading_symbol, item.venue_instrument_id):
            rows.append({"kind": "listing", **listing_to_primitive(item)})
    for item in catalog.instruments():
        if item.active_at(at) and _matches(needle, str(item.instrument_id), item.display_name, str(item.base_asset_id), str(item.quote_asset_id)):
            rows.append({"kind": "instrument", **instrument_to_primitive(item)})
    for item in catalog.assets():
        if item.active_at(at) and _matches(needle, str(item.asset_id), item.symbol, item.name):
            rows.append({"kind": "asset", **asset_to_primitive(item)})
    write_jsonl(rows[:limit], sys.stdout)


@reference_app.command("resolve")
def resolve(
    symbol: str = typer.Argument(...),
    venue: str = typer.Option(..., "--venue"),
    market: str | None = typer.Option(None, "--market"),
    root: str | None = typer.Option(None, "--root"),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    at = _time(as_of)
    try:
        item = reference_store(root).load_catalog().resolve_market(symbol, venue=venue, market=market, at=at)
    except KeyError as error:
        raise typer.BadParameter(str(error)) from error
    write_jsonl((market_to_primitive(item),), sys.stdout)


@reference_app.command("show")
def show(
    identifier: str = typer.Argument(...),
    root: str | None = typer.Option(None, "--root"),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    at = _time(as_of)
    catalog = reference_store(root).load_catalog()
    rows: list[dict[str, object]] = []
    market_item = catalog.maybe_get_market(identifier, at)
    if market_item is not None:
        rows.append({"kind": "market", **market_to_primitive(market_item)})
    listing_item = catalog.maybe_get_listing(identifier, at)
    if listing_item is not None:
        rows.append({"kind": "listing", **listing_to_primitive(listing_item)})
    instrument_item = catalog.maybe_get_instrument(identifier, at)
    if instrument_item is not None:
        rows.append({"kind": "instrument", **instrument_to_primitive(instrument_item)})
    asset_item = catalog.maybe_get_asset(identifier, at)
    if asset_item is not None:
        rows.append({"kind": "asset", **asset_to_primitive(asset_item)})
    if not rows:
        raise typer.BadParameter(f"unknown reference identifier: {identifier}")
    write_jsonl(rows, sys.stdout)


@catalog_app.command("status")
def catalog_status(
    root: str | None = typer.Option(None, "--root"),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    at = _time(as_of)
    store = reference_store(root)
    catalog = store.load_catalog()
    payload = {
        "root": str(store.root),
        "database": str(store.database_path),
        "exists": store.database_path.exists(),
        "as_of": at.isoformat(),
        "entities": len(catalog.entities()),
        "assets": len(catalog.assets()),
        "instruments": len(catalog.instruments()),
        "listings": len(catalog.listings()),
        "markets": len(catalog.markets()),
        "active_markets": len(catalog.list_markets(at=at, active_only=True)),
        "events": len(store.load_events()),
    }
    write_jsonl((payload,), sys.stdout)


def _write_markets(
    *,
    root: str | None,
    venue: str | None,
    market: str | None,
    status: MarketStatus | None,
    active_only: bool,
    as_of: str | None,
    limit: int | None,
) -> None:
    at = _time(as_of)
    catalog = reference_store(root).load_catalog()
    rows = [
        market_to_primitive(item)
        for item in catalog.list_markets(at=at, venue=venue, market=market, status=status, active_only=active_only)
    ]
    if limit is not None:
        rows = rows[:limit]
    write_jsonl(rows, sys.stdout)


def _time(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise typer.BadParameter("time must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _matches(needle: str, *values: object) -> bool:
    return any(isinstance(value, str) and needle in value.casefold() for value in values)


__all__ = ["reference_app"]
