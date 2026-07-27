from __future__ import annotations

from datetime import datetime, timezone
import sys

import typer

from kairospy.integrations import EquityProviderRefreshService, InstrumentProviderRefreshService
from kairospy.reference import MarketStatus, ReferenceRefreshService
from kairospy.reference.serde import lifecycle_event_to_primitive, market_to_primitive
from kairospy.surface.runtime import DriverName, ExchangeName, ProviderName, exchange, provider, reference_store
from kairospy.surface.ui.terminal import write_jsonl


reference_app = typer.Typer(no_args_is_help=True, help="Reference catalog commands")
refresh_app = typer.Typer(no_args_is_help=True, help="Refresh reference catalogs from providers")
reference_app.add_typer(refresh_app, name="refresh")


@refresh_app.command("hyperliquid")
def refresh_hyperliquid(
    root: str | None = typer.Option(None, "--root"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    market: str | None = typer.Option(None, "--market"),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    at = _time(as_of)
    provider = exchange(ExchangeName.hyperliquid, driver_name)
    service = InstrumentProviderRefreshService(ReferenceRefreshService(reference_store(root)))
    result = service.refresh(provider, as_of=at, venue=ExchangeName.hyperliquid.value, market=market)
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
    provider = exchange(exchange_name, driver_name)
    store = reference_store(root)
    service = InstrumentProviderRefreshService(ReferenceRefreshService(store))
    result = service.refresh(provider, as_of=at, venue=exchange_name.value, market=market, params={"type": market})
    schedule_events = ()
    if include_delist_schedule and hasattr(provider, "fetch_delist_events"):
        schedule_events = provider.fetch_delist_events(catalog=result.catalog, market=market)
        store.append_events(schedule_events)
    write_jsonl((
        {
            "time": at.isoformat(),
            "venue": exchange_name.value,
            "market": market,
            "previous_markets": len(result.previous_markets),
            "current_markets": len(result.current_markets),
            "events": len(result.events),
            "scheduled_events": len(schedule_events),
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
    provider_client = provider(ProviderName.massive, driver_name)
    service = EquityProviderRefreshService(ReferenceRefreshService(reference_store(root)))
    result = service.refresh(provider_client, as_of=at, venue=venue, params={"asset_class": "equity"})
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
    store = reference_store(root)
    catalog = store.load_catalog()
    provider_client = provider(ProviderName.massive, driver_name)
    if not hasattr(provider_client, "fetch_lifecycle_events"):
        raise typer.BadParameter("provider does not support lifecycle events")
    events = provider_client.fetch_lifecycle_events(ticker, start=start_at, end=end_at, catalog=catalog, venue=venue)
    store.append_events(events)
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


__all__ = ["reference_app"]
