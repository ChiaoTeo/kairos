from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import sys

import typer

from kairospy.application.support.system.facade.reference import (
    AssetType,
    Broker,
    Exchange,
    MarketStatus,
    Provider,
    DriverName,
    ExchangeName,
    ProviderName,
    add_asset,
    asset_to_primitive,
    entity_to_primitive,
    instrument_to_primitive,
    lifecycle_event_to_primitive,
    listing_to_primitive,
    market_to_primitive,
    reference_brokers,
    reference_exchanges,
    reference_providers,
    refresh_exchange_reference,
    refresh_exchange_reference_with_delist_schedule,
    refresh_provider_reference,
    exchange,
    provider,
    reference_client,
    sync_lifecycle_events,
    reference_store,
)
from kairospy.application.support.system.facade.context import ProjectNotFound
from kairospy.application.support.system.browsing import ListQuery, query_rows
from kairospy.surface.tui import ResourceList, ResourceListBrowser
from kairospy.surface.cli.options import OutputFormat, resolve_output
from kairospy.surface.rendering.terminal import write_jsonl
from kairospy.surface.rendering.writer import write_result


reference_app = typer.Typer(no_args_is_help=True, help="Reference catalog commands")
sync_app = typer.Typer(no_args_is_help=True, help="Reference provider sync commands")
participants_app = typer.Typer(no_args_is_help=True, help="Reference participant registry commands")
assets_app = typer.Typer(no_args_is_help=True, help="Reference asset commands")
markets_app = typer.Typer(no_args_is_help=True, help="Reference market commands")
events_app = typer.Typer(no_args_is_help=False, invoke_without_command=True, help="Reference event commands")
reference_app.add_typer(sync_app, name="sync")
reference_app.add_typer(participants_app, name="participants")
reference_app.add_typer(assets_app, name="assets")
reference_app.add_typer(markets_app, name="markets")
reference_app.add_typer(events_app, name="events")


_EXCHANGE_COLUMNS = ("exchange_id", "name", "aliases", "default_markets", "mic", "country", "timezone", "entity_id")
_BROKER_COLUMNS = ("broker_id", "name", "exchange_id", "aliases", "default_markets", "credential_kind", "entity_id")
_PROVIDER_COLUMNS = ("provider_id", "name", "aliases", "asset_classes", "entity_id")
_ASSET_COLUMNS = ("asset_id", "asset_type", "symbol", "name", "issuer_id", "effective_from", "effective_to")


class ReferenceKind(StrEnum):
    entity = "entity"
    asset = "asset"
    instrument = "instrument"
    listing = "listing"
    market = "market"
    event = "event"
    all = "all"


@assets_app.command("add")
def add_asset_command(
    ctx: typer.Context,
    symbol: str = typer.Option(..., "--symbol"),
    asset_type: AssetType = typer.Option(..., "--type"),
    root: str | None = typer.Option(None, "--root"),
    asset_id: str | None = typer.Option(None, "--asset-id"),
    name: str | None = typer.Option(None, "--name"),
    issuer_id: str | None = typer.Option(None, "--issuer"),
    replace_existing: bool = typer.Option(False, "--replace"),
    effective_from: str | None = typer.Option(None, "--effective-from", "--as-of"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    at = _time(effective_from)
    try:
        item = add_asset(
            reference_store(root),
            symbol=symbol,
            asset_type=asset_type,
            asset_id=asset_id,
            name=name,
            issuer_id=issuer_id,
            effective_from=at,
            replace_existing=replace_existing,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _write_asset_rows(ctx, (asset_to_primitive(item),), output_format=output_format)


@assets_app.command("list")
def list_assets(
    ctx: typer.Context,
    root: str | None = typer.Option(None, "--root"),
    asset_type: AssetType | None = typer.Option(None, "--type"),
    active_only: bool = typer.Option(False, "--active-only"),
    as_of: str | None = typer.Option(None, "--as-of"),
    limit: int | None = typer.Option(None, "--limit", show_default="50"),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int = typer.Option(50, "--page-size", min=1),
    query: str | None = typer.Option(None, "--query", help="JMESPath expression returning a list of objects."),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    at = _time(as_of)
    rows = []
    for item in reference_store(root).load_catalog().assets():
        if active_only and not item.active_at(at):
            continue
        if asset_type is not None and item.asset_type is not asset_type:
            continue
        rows.append(asset_to_primitive(item))
    rows.sort(key=lambda row: str(row["asset_id"]))
    if query is not None or page != 1 or page_size != 50:
        result = query_rows(rows, ListQuery(page=page, page_size=page_size, limit=limit, expression=query), columns=_ASSET_COLUMNS)
        _write_list_result(ctx, result, output_format=output_format)
        return
    rows = rows[:50 if limit is None else limit]
    _write_asset_rows(ctx, tuple(rows), output_format=output_format)


@assets_app.command("browse")
def browse_assets(
    root: str | None = typer.Option(None, "--root"),
    asset_type: AssetType | None = typer.Option(None, "--type"),
    active_only: bool = typer.Option(False, "--active-only"),
    as_of: str | None = typer.Option(None, "--as-of"),
    page_size: int = typer.Option(20, "--page-size", min=1),
    query: str | None = typer.Option(None, "--query", help="JMESPath expression returning a list of objects."),
) -> None:
    try:
        resource = ResourceList.from_rows(
            _asset_rows(root=root, asset_type=asset_type, active_only=active_only, as_of=as_of),
            columns=_ASSET_COLUMNS,
            title="Reference Assets",
            query=ListQuery(page_size=page_size, expression=query),
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    ResourceListBrowser(resource).run()


@assets_app.command("show")
def show_asset(
    ctx: typer.Context,
    asset_id: str = typer.Argument(...),
    root: str | None = typer.Option(None, "--root"),
    as_of: str | None = typer.Option(None, "--as-of"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    at = _time(as_of)
    item = reference_store(root).load_catalog().maybe_get_asset(asset_id, at)
    if item is None:
        raise typer.BadParameter(f"unknown asset identifier: {asset_id}")
    _write_asset_rows(ctx, (asset_to_primitive(item),), output_format=output_format)


@participants_app.command("brokers")
def brokers(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    _write_entity_rows(ctx, _broker_rows(), output_format=output_format, columns=_BROKER_COLUMNS)


@participants_app.command("exchanges")
def exchanges(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    _write_entity_rows(ctx, _exchange_rows(), output_format=output_format, columns=_EXCHANGE_COLUMNS)


@participants_app.command("providers")
def providers(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    _write_entity_rows(ctx, _provider_rows(), output_format=output_format, columns=_PROVIDER_COLUMNS)


@sync_app.command("binance")
def sync_binance(
    ctx: typer.Context,
    root: str | None = typer.Option(None, "--root"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    market: str = typer.Option("spot", "--market"),
    include_delist_schedule: bool = typer.Option(False, "--include-delist-schedule/--no-delist-schedule"),
    as_of: str | None = typer.Option(None, "--as-of"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        _sync_binance(
            root=root,
            market=market,
            driver_name=driver_name,
            include_delist_schedule=include_delist_schedule,
            at=_time(as_of),
            ctx=ctx,
            output_format=output_format,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@sync_app.command("hyperliquid")
def sync_hyperliquid(
    ctx: typer.Context,
    root: str | None = typer.Option(None, "--root"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    market: str | None = typer.Option(None, "--market"),
    as_of: str | None = typer.Option(None, "--as-of"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    _sync_hyperliquid(root=root, market=market, driver_name=driver_name, at=_time(as_of), ctx=ctx, output_format=output_format)


@sync_app.command("massive")
def sync_massive(
    ctx: typer.Context,
    root: str | None = typer.Option(None, "--root"),
    driver_name: DriverName = typer.Option(DriverName.massive, "--driver"),
    venue: str | None = typer.Option(None, "--venue"),
    market: str | None = typer.Option(None, "--market"),
    as_of: str | None = typer.Option(None, "--as-of"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    _sync_massive(root=root, venue=venue, market=market, driver_name=driver_name, at=_time(as_of), ctx=ctx, output_format=output_format)


def _sync_binance(
    *,
    root: str | None,
    market: str,
    driver_name: DriverName,
    include_delist_schedule: bool,
    at: datetime,
    ctx: typer.Context,
    output_format: OutputFormat | None,
) -> None:
    store = reference_store(root)
    provider_result = refresh_exchange_reference_with_delist_schedule(
        store,
        _reference_client("exchange", ExchangeName.binance.value, market=market, driver_name=driver_name),
        as_of=at,
        venue=ExchangeName.binance.value,
        market=market,
        params={"type": market},
        include_delist_schedule=include_delist_schedule,
    )
    result = provider_result.refresh
    _write_sync_result(
        root=root,
        at=at,
        source_kind="exchange",
        source_name="binance",
        venue=ExchangeName.binance.value,
        market=market,
        previous_markets=len(result.previous_markets),
        current_markets=len(result.current_markets),
        events=len(result.events),
        scheduled_events=len(provider_result.scheduled_events),
        ctx=ctx,
        output_format=output_format,
    )


def _sync_hyperliquid(
    *,
    root: str | None,
    market: str | None,
    driver_name: DriverName,
    at: datetime,
    ctx: typer.Context,
    output_format: OutputFormat | None,
) -> None:
    result = refresh_exchange_reference(
        reference_store(root),
        _reference_client("exchange", ExchangeName.hyperliquid.value, market=market, driver_name=driver_name),
        as_of=at,
        venue=ExchangeName.hyperliquid.value,
        market=market,
    ).refresh
    _write_sync_result(
        root=root,
        at=at,
        source_kind="exchange",
        source_name="hyperliquid",
        venue=ExchangeName.hyperliquid.value,
        market=market or "all",
        previous_markets=len(result.previous_markets),
        current_markets=len(result.current_markets),
        events=len(result.events),
        ctx=ctx,
        output_format=output_format,
    )


def _sync_massive(
    *,
    root: str | None,
    venue: str | None,
    market: str | None,
    driver_name: DriverName,
    at: datetime,
    ctx: typer.Context,
    output_format: OutputFormat | None,
) -> None:
    result = refresh_provider_reference(
        reference_store(root),
        _reference_client("provider", ProviderName.massive.value, market=market, driver_name=driver_name),
        as_of=at,
        venue=venue,
        params={"asset_class": "equity"},
    ).refresh
    _write_sync_result(
        root=root,
        at=at,
        source_kind="provider",
        source_name="massive",
        venue=venue,
        market=market or "equity",
        previous_markets=len(result.previous_markets),
        current_markets=len(result.current_markets),
        events=len(result.events),
        ctx=ctx,
        output_format=output_format,
    )


def _write_sync_result(
    *,
    root: str | None,
    at: datetime,
    source_kind: str,
    source_name: str,
    venue: str | None,
    market: str,
    previous_markets: int,
    current_markets: int,
    events: int,
    ctx: typer.Context,
    output_format: OutputFormat | None,
    scheduled_events: int | None = None,
) -> None:
    catalog = reference_store(root).load_catalog()
    payload: dict[str, object] = {
        "time": at.isoformat(),
        "source_kind": source_kind,
        "source": source_name,
        "provider": source_name,
        "venue": venue,
        "market": market,
        "previous_markets": previous_markets,
        "current_markets": current_markets,
        "events": events,
        "entities": len(catalog.entities()),
        "assets": len(catalog.assets()),
        "instruments": len(catalog.instruments()),
        "listings": len(catalog.listings()),
        "markets": len(catalog.markets()),
    }
    if scheduled_events is not None:
        payload["scheduled_events"] = scheduled_events
    _write_reference_rows(ctx, (payload,), output_format=output_format)


@events_app.command("sync")
def sync_events(
    ctx: typer.Context,
    provider_name: str = typer.Option(ProviderName.massive.value, "--provider"),
    ticker: str = typer.Option(..., "--ticker"),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    root: str | None = typer.Option(None, "--root"),
    driver_name: DriverName = typer.Option(DriverName.massive, "--driver"),
    venue: str | None = typer.Option(None, "--venue"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    if provider_name.strip().lower() != ProviderName.massive.value:
        raise typer.BadParameter(f"unsupported event provider: {provider_name}")
    start_at = _time(start)
    end_at = _time(end)
    if end_at <= start_at:
        raise typer.BadParameter("end must be after start")
    try:
        events = sync_lifecycle_events(
            reference_store(root),
            _reference_client("provider", ProviderName.massive.value, market=None, driver_name=driver_name),
            ticker=ticker,
            start=start_at,
            end=end_at,
            venue=venue,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _write_reference_rows(ctx, (
        {
            "provider": "massive",
            "ticker": ticker.upper(),
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
            "events": len(events),
        },
    ), output_format=output_format)


def _reference_client(source_kind: str, source_name: str, *, market: str | None, driver_name: DriverName):
    if source_kind in {"exchange", "broker"}:
        if driver_name is not DriverName.ccxt:
            raise ValueError(f"{source_kind} reference source requires ccxt driver")
        return exchange(ExchangeName(source_name), driver_name)
    if source_kind == "provider" and source_name == ProviderName.massive.value:
        if driver_name is not DriverName.massive:
            raise ValueError("massive provider requires massive driver")
        return provider(ProviderName(source_name), driver_name)
    return reference_client(source_kind, source_name, market=market, driver_name=driver_name)


@markets_app.command("list")
def list_markets(
    ctx: typer.Context,
    root: str | None = typer.Option(None, "--root"),
    venue: str | None = typer.Option(None, "--venue"),
    market: str | None = typer.Option(None, "--market"),
    status: MarketStatus | None = typer.Option(None, "--status"),
    active_only: bool = typer.Option(False, "--active-only"),
    as_of: str | None = typer.Option(None, "--as-of"),
    limit: int | None = typer.Option(None, "--limit", show_default="50"),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int = typer.Option(50, "--page-size", min=1),
    query: str | None = typer.Option(None, "--query", help="JMESPath expression returning a list of objects."),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    _write_markets(
        root=root,
        venue=venue,
        market=market,
        status=status,
        active_only=active_only,
        as_of=as_of,
        limit=limit,
        page=page,
        page_size=page_size,
        query=query,
        ctx=ctx,
        output_format=output_format,
    )


@markets_app.command("browse")
def browse_markets(
    root: str | None = typer.Option(None, "--root"),
    venue: str | None = typer.Option(None, "--venue"),
    market: str | None = typer.Option(None, "--market"),
    status: MarketStatus | None = typer.Option(None, "--status"),
    active_only: bool = typer.Option(False, "--active-only"),
    as_of: str | None = typer.Option(None, "--as-of"),
    page_size: int = typer.Option(20, "--page-size", min=1),
    query: str | None = typer.Option(None, "--query", help="JMESPath expression returning a list of objects."),
) -> None:
    try:
        resource = ResourceList.from_rows(
            _market_rows(
                root=root,
                venue=venue,
                market=market,
                status=status,
                active_only=active_only,
                as_of=as_of,
            ),
            title="Reference Markets",
            query=ListQuery(page_size=page_size, expression=query),
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    ResourceListBrowser(resource).run()


@events_app.callback()
def events(
    ctx: typer.Context,
    root: str | None = typer.Option(None, "--root"),
    limit: int | None = typer.Option(None, "--limit"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    rows = [lifecycle_event_to_primitive(item) for item in reference_store(root).load_events()]
    rows = rows[:50 if limit is None else limit]
    _write_reference_rows(ctx, tuple(rows), output_format=output_format)


@reference_app.command("view")
def view(
    ctx: typer.Context,
    identifier: str | None = typer.Argument(None),
    root: str | None = typer.Option(None, "--root"),
    as_of: str | None = typer.Option(None, "--as-of"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    if identifier is None:
        _write_catalog_status(ctx, root=root, as_of=as_of, output_format=output_format)
        return
    _write_identifier(ctx, identifier, root=root, as_of=as_of, output_format=output_format)


@reference_app.command("query")
def query(
    ctx: typer.Context,
    text: str | None = typer.Argument(None),
    kind: ReferenceKind = typer.Option(ReferenceKind.all, "--kind"),
    root: str | None = typer.Option(None, "--root"),
    venue: str | None = typer.Option(None, "--venue"),
    market: str | None = typer.Option(None, "--market"),
    status: MarketStatus | None = typer.Option(None, "--status"),
    active_only: bool = typer.Option(False, "--active-only"),
    as_of: str | None = typer.Option(None, "--as-of"),
    limit: int = typer.Option(50, "--limit"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    at = _time(as_of)
    needle = None if text is None else text.casefold()
    catalog = reference_store(root).load_catalog()
    rows: list[dict[str, object]] = []
    if kind in (ReferenceKind.all, ReferenceKind.entity):
        rows.extend(
            {"kind": "entity", **entity_to_primitive(item)}
            for item in catalog.entities()
            if item.active_at(at) and _matches_optional(needle, str(item.entity_id), item.name, item.entity_type.value)
        )
    if kind in (ReferenceKind.all, ReferenceKind.asset):
        rows.extend(
            {"kind": "asset", **asset_to_primitive(item)}
            for item in catalog.assets()
            if item.active_at(at) and _matches_optional(needle, str(item.asset_id), item.symbol, item.name, item.asset_type.value)
        )
    if kind in (ReferenceKind.all, ReferenceKind.instrument):
        rows.extend(
            {"kind": "instrument", **instrument_to_primitive(item)}
            for item in catalog.instruments()
            if item.active_at(at)
            and _matches_optional(
                needle,
                str(item.instrument_id),
                str(item.base_asset_id),
                str(item.quote_asset_id),
                item.display_name,
                item.instrument_type.value,
            )
        )
    if kind in (ReferenceKind.all, ReferenceKind.listing):
        rows.extend(
            {"kind": "listing", **listing_to_primitive(item)}
            for item in catalog.active_listings(at=at, venue=venue)
            if _matches_market_status(item.status, status, active_only)
            and _matches_optional(needle, str(item.listing_id), str(item.instrument_id), item.trading_symbol, item.venue_instrument_id)
        )
    if kind in (ReferenceKind.all, ReferenceKind.market):
        rows.extend(
            {"kind": "market", **market_to_primitive(item)}
            for item in catalog.list_markets(at=at, venue=venue, market=market, status=status, active_only=active_only)
            if _matches_optional(needle, str(item.market_id), str(item.instrument_id), str(item.listing_id), item.venue, item.market, item.source_symbol)
        )
    if kind in (ReferenceKind.all, ReferenceKind.event):
        rows.extend(
            {"kind": "event", **lifecycle_event_to_primitive(item)}
            for item in reference_store(root).load_events()
            if _matches_event(item, needle=needle, venue=venue, market=market, status=status, active_only=active_only)
        )
    _write_reference_rows(ctx, tuple(rows[:limit]), output_format=output_format)


@reference_app.command("search")
def search(
    ctx: typer.Context,
    query: str = typer.Argument(...),
    root: str | None = typer.Option(None, "--root"),
    as_of: str | None = typer.Option(None, "--as-of"),
    limit: int = typer.Option(50, "--limit"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
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
    _write_reference_rows(ctx, tuple(rows[:limit]), output_format=output_format)


@markets_app.command("resolve")
def resolve(
    ctx: typer.Context,
    symbol: str = typer.Argument(...),
    venue: str = typer.Option(..., "--venue"),
    market: str | None = typer.Option(None, "--market"),
    root: str | None = typer.Option(None, "--root"),
    as_of: str | None = typer.Option(None, "--as-of"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    at = _time(as_of)
    try:
        item = reference_store(root).load_catalog().resolve_market(symbol, venue=venue, market=market, at=at)
    except KeyError as error:
        raise typer.BadParameter(str(error)) from error
    _write_reference_rows(ctx, (market_to_primitive(item),), output_format=output_format)


@reference_app.command("show")
def show(
    ctx: typer.Context,
    identifier: str = typer.Argument(...),
    root: str | None = typer.Option(None, "--root"),
    as_of: str | None = typer.Option(None, "--as-of"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    _write_identifier(ctx, identifier, root=root, as_of=as_of, output_format=output_format)


def _write_identifier(
    ctx: typer.Context,
    identifier: str,
    *,
    root: str | None,
    as_of: str | None,
    output_format: OutputFormat | None,
) -> None:
    at = _time(as_of)
    catalog = reference_store(root).load_catalog()
    rows: list[dict[str, object]] = []
    entity_item = catalog.maybe_get_entity(identifier, at)
    if entity_item is not None:
        rows.append({"kind": "entity", **entity_to_primitive(entity_item)})
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
    _write_reference_rows(ctx, tuple(rows), output_format=output_format)


@reference_app.command("status")
def catalog_status(
    ctx: typer.Context,
    root: str | None = typer.Option(None, "--root"),
    as_of: str | None = typer.Option(None, "--as-of"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    _write_catalog_status(ctx, root=root, as_of=as_of, output_format=output_format)


def _write_catalog_status(
    ctx: typer.Context,
    *,
    root: str | None,
    as_of: str | None,
    output_format: OutputFormat | None,
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
    _write_reference_rows(ctx, (payload,), output_format=output_format)


def _write_markets(
    *,
    root: str | None,
    venue: str | None,
    market: str | None,
    status: MarketStatus | None,
    active_only: bool,
    as_of: str | None,
    limit: int | None,
    page: int = 1,
    page_size: int = 50,
    query: str | None = None,
    ctx: typer.Context,
    output_format: OutputFormat | None,
) -> None:
    at = _time(as_of)
    catalog = reference_store(root).load_catalog()
    rows = [
        market_to_primitive(item)
        for item in catalog.list_markets(at=at, venue=venue, market=market, status=status, active_only=active_only)
    ]
    if query is not None or page != 1 or page_size != 50:
        result = query_rows(rows, ListQuery(page=page, page_size=page_size, limit=limit, expression=query))
        _write_list_result(ctx, result, output_format=output_format)
        return
    if limit is not None:
        rows = rows[:limit]
    _write_reference_rows(ctx, tuple(rows), output_format=output_format)


def _asset_rows(
    *,
    root: str | None,
    asset_type: AssetType | None,
    active_only: bool,
    as_of: str | None,
) -> tuple[dict[str, object], ...]:
    at = _time(as_of)
    rows = [
        asset_to_primitive(item)
        for item in reference_store(root).load_catalog().assets()
        if (not active_only or item.active_at(at)) and (asset_type is None or item.asset_type is asset_type)
    ]
    rows.sort(key=lambda row: str(row["asset_id"]))
    return tuple(rows)


def _market_rows(
    *,
    root: str | None,
    venue: str | None,
    market: str | None,
    status: MarketStatus | None,
    active_only: bool,
    as_of: str | None,
) -> tuple[dict[str, object], ...]:
    at = _time(as_of)
    return tuple(
        market_to_primitive(item)
        for item in reference_store(root).load_catalog().list_markets(
            at=at,
            venue=venue,
            market=market,
            status=status,
            active_only=active_only,
        )
    )


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


def _matches_optional(needle: str | None, *values: object) -> bool:
    if needle is None:
        return True
    return _matches(needle, *values)


def _matches_market_status(status: MarketStatus, expected: MarketStatus | None, active_only: bool) -> bool:
    if expected is not None and status is not expected:
        return False
    if active_only and status is not MarketStatus.ACTIVE:
        return False
    return True


def _matches_event(
    item,
    *,
    needle: str | None,
    venue: str | None,
    market: str | None,
    status: MarketStatus | None,
    active_only: bool,
) -> bool:
    if venue is not None and str(item.venue) != str(venue):
        return False
    if market is not None and market.casefold() not in str(item.source_symbol or "").casefold():
        return False
    if status is not None:
        values = (item.previous or {}) | (item.current or {})
        if status.value not in {str(value) for value in values.values()}:
            return False
    if active_only:
        values = (item.previous or {}) | (item.current or {})
        if MarketStatus.ACTIVE.value not in {str(value) for value in values.values()}:
            return False
    return _matches_optional(
        needle,
        item.event_type.value,
        str(item.instrument_id),
        str(item.listing_id),
        str(item.market_id),
        item.venue,
        item.source_symbol,
    )


def _exchange_rows() -> tuple[dict[str, object], ...]:
    return tuple(_exchange_row(item) for item in reference_exchanges())


def _broker_rows() -> tuple[dict[str, object], ...]:
    return tuple(_broker_row(item) for item in reference_brokers())


def _provider_rows() -> tuple[dict[str, object], ...]:
    return tuple(_provider_row(item) for item in reference_providers())


def _exchange_row(item: Exchange) -> dict[str, object]:
    row: dict[str, object] = {
        "kind": "exchange",
        "exchange_id": str(item.exchange_id),
        "name": item.name,
        "entity_id": None if item.entity_id is None else str(item.entity_id),
        "country": item.country,
        "timezone": item.timezone,
    }
    return _with_metadata_fields(row, item.metadata)


def _broker_row(item: Broker) -> dict[str, object]:
    row: dict[str, object] = {
        "kind": "broker",
        "broker_id": str(item.broker_id),
        "name": item.name,
        "exchange_id": None if item.exchange_id is None else str(item.exchange_id),
        "entity_id": None if item.entity_id is None else str(item.entity_id),
    }
    return _with_metadata_fields(row, item.metadata)


def _provider_row(item: Provider) -> dict[str, object]:
    row: dict[str, object] = {
        "kind": "provider",
        "provider_id": str(item.provider_id),
        "name": item.name,
        "entity_id": None if item.entity_id is None else str(item.entity_id),
    }
    return _with_metadata_fields(row, item.metadata)


def _with_metadata_fields(row: dict[str, object], metadata: object) -> dict[str, object]:
    if isinstance(metadata, dict):
        row.update(metadata)
        return {key: value for key, value in row.items() if value is not None}
    try:
        row.update(dict(metadata))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {key: value for key, value in row.items() if value is not None}
    return {key: value for key, value in row.items() if value is not None}


def _write_reference_rows(
    ctx: typer.Context,
    rows: tuple[dict[str, object], ...],
    *,
    output_format: OutputFormat | None,
) -> None:
    output = _effective_output_format(ctx, output_format, default=OutputFormat.json)
    if output in {OutputFormat.json, OutputFormat.jsonl}:
        write_jsonl(rows, sys.stdout)
        return
    typer.echo(_render_reference_rows(rows))


def _write_list_result(
    ctx: typer.Context,
    result: object,
    *,
    output_format: OutputFormat | None,
) -> None:
    output = _effective_output_format(ctx, output_format, default=OutputFormat.json)
    if output is OutputFormat.jsonl:
        rows = result.rows if hasattr(result, "rows") else ()
        write_jsonl(rows, sys.stdout)
        return
    if output is OutputFormat.json:
        payload = result.to_dict() if hasattr(result, "to_dict") else result
        write_result(payload, output=output)
        return
    rows = result.rows if hasattr(result, "rows") else ()
    page = result
    typer.echo(f"page {page.page}/{page.total_pages}  ({page.total_rows} rows)\n{_render_reference_rows(rows)}")


def _render_reference_rows(result: object) -> str:
    if isinstance(result, dict):
        rows = (result,)
    elif isinstance(result, (tuple, list)):
        rows = tuple(item for item in result if isinstance(item, dict))
    else:
        return str(result)
    columns: list[str] = []
    for row in rows:
        for key, value in row.items():
            if key not in columns and _is_table_value(value):
                columns.append(key)
    return _render_table(rows, tuple(columns))


def _is_table_value(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _write_entity_rows(
    ctx: typer.Context,
    rows: tuple[dict[str, object], ...],
    *,
    output_format: OutputFormat | None,
    columns: tuple[str, ...],
) -> None:
    output = _effective_output_format(ctx, output_format)
    if output in {OutputFormat.json, OutputFormat.jsonl}:
        write_jsonl(rows, sys.stdout)
        return
    typer.echo(_render_table(rows, columns))


def _write_asset_rows(
    ctx: typer.Context,
    rows: tuple[dict[str, object], ...],
    *,
    output_format: OutputFormat | None,
) -> None:
    output = _effective_output_format(ctx, output_format, default=OutputFormat.json)
    if output in {OutputFormat.json, OutputFormat.jsonl}:
        write_jsonl(rows, sys.stdout)
        return
    typer.echo(_render_table(rows, _ASSET_COLUMNS))


def _effective_output_format(
    ctx: typer.Context,
    output_format: OutputFormat | None,
    *,
    default: OutputFormat = OutputFormat.text,
) -> OutputFormat:
    if output_format is not None and output_format is not OutputFormat.auto:
        return output_format
    try:
        return resolve_output(ctx, output_format, default=default)
    except ProjectNotFound:
        return default


def _render_table(rows: tuple[dict[str, object], ...], columns: tuple[str, ...]) -> str:
    present_columns = tuple(column for column in columns if any(column in row for row in rows))
    if not present_columns:
        return "No rows"
    widths = {
        column: max(len(column), *(len(_cell(row.get(column))) for row in rows))
        for column in present_columns
    }
    lines = [
        "  ".join(column.ljust(widths[column]) for column in present_columns),
        "  ".join("-" * widths[column] for column in present_columns),
    ]
    for row in rows:
        lines.append("  ".join(_cell(row.get(column)).ljust(widths[column]) for column in present_columns))
    return "\n".join(lines)


def _cell(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, (tuple, list)):
        return ",".join(str(item) for item in value)
    return str(value)


__all__ = ["reference_app"]
