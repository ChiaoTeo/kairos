from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import sys

import typer

from kairospy.application.system.facade.reference import (
    AssetType,
    Broker,
    Exchange,
    MarketStatus,
    Provider,
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
    refresh_equity_provider,
    refresh_instrument_provider,
    refresh_instrument_provider_with_delist_schedule,
    sync_lifecycle_events,
)
from kairospy.application.system.facade.resources import DriverName, ExchangeName, ProviderName, exchange, provider, reference_store
from kairospy.application.system.facade.context import ProjectNotFound
from kairospy.surface.cli.options import OutputFormat, resolve_output
from kairospy.surface.rendering.terminal import write_jsonl


reference_app = typer.Typer(no_args_is_help=True, help="Reference catalog commands")
sync_app = typer.Typer(no_args_is_help=True, help="Reference provider sync commands")
catalog_app = typer.Typer(no_args_is_help=True, help="Reference catalog inspection commands")
participants_app = typer.Typer(no_args_is_help=True, help="Reference participant registry commands")
assets_app = typer.Typer(no_args_is_help=True, help="Reference asset commands")
markets_app = typer.Typer(no_args_is_help=True, help="Reference market commands")
lifecycle_app = typer.Typer(no_args_is_help=True, help="Reference lifecycle commands")
reference_app.add_typer(sync_app, name="sync")
reference_app.add_typer(participants_app, name="participants")
reference_app.add_typer(catalog_app, name="catalog")
reference_app.add_typer(assets_app, name="assets")
reference_app.add_typer(markets_app, name="markets")
reference_app.add_typer(lifecycle_app, name="lifecycle")


_EXCHANGE_COLUMNS = ("exchange_id", "name", "aliases", "default_markets", "mic", "country", "timezone", "entity_id")
_BROKER_COLUMNS = ("broker_id", "name", "exchange_id", "aliases", "default_markets", "credential_kind", "entity_id")
_PROVIDER_COLUMNS = ("provider_id", "name", "aliases", "asset_classes", "entity_id")


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
    symbol: str = typer.Option(..., "--symbol"),
    asset_type: AssetType = typer.Option(..., "--type"),
    root: str | None = typer.Option(None, "--root"),
    asset_id: str | None = typer.Option(None, "--asset-id"),
    name: str | None = typer.Option(None, "--name"),
    issuer_id: str | None = typer.Option(None, "--issuer"),
    replace_existing: bool = typer.Option(False, "--replace"),
    effective_from: str | None = typer.Option(None, "--effective-from", "--as-of"),
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
    write_jsonl((asset_to_primitive(item),), sys.stdout)


@assets_app.command("list")
def list_assets(
    root: str | None = typer.Option(None, "--root"),
    asset_type: AssetType | None = typer.Option(None, "--type"),
    active_only: bool = typer.Option(False, "--active-only"),
    as_of: str | None = typer.Option(None, "--as-of"),
    limit: int | None = typer.Option(50, "--limit"),
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
    if limit is not None:
        rows = rows[:limit]
    write_jsonl(rows, sys.stdout)


@assets_app.command("show")
def show_asset(
    asset_id: str = typer.Argument(...),
    root: str | None = typer.Option(None, "--root"),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    at = _time(as_of)
    item = reference_store(root).load_catalog().maybe_get_asset(asset_id, at)
    if item is None:
        raise typer.BadParameter(f"unknown asset identifier: {asset_id}")
    write_jsonl((asset_to_primitive(item),), sys.stdout)


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
    root: str | None = typer.Option(None, "--root"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    market: str = typer.Option("spot", "--market"),
    include_delist_schedule: bool = typer.Option(False, "--include-delist-schedule/--no-delist-schedule"),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    try:
        _sync_binance(
            root=root,
            market=market,
            driver_name=driver_name,
            include_delist_schedule=include_delist_schedule,
            at=_time(as_of),
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@sync_app.command("hyperliquid")
def sync_hyperliquid(
    root: str | None = typer.Option(None, "--root"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    market: str | None = typer.Option(None, "--market"),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    _sync_hyperliquid(root=root, market=market, driver_name=driver_name, at=_time(as_of))


@sync_app.command("massive")
def sync_massive(
    root: str | None = typer.Option(None, "--root"),
    driver_name: DriverName = typer.Option(DriverName.massive, "--driver"),
    venue: str | None = typer.Option(None, "--venue"),
    market: str | None = typer.Option(None, "--market"),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    _sync_massive(root=root, venue=venue, market=market, driver_name=driver_name, at=_time(as_of))


def _sync_binance(
    *,
    root: str | None,
    market: str,
    driver_name: DriverName,
    include_delist_schedule: bool,
    at: datetime,
) -> None:
    store = reference_store(root)
    provider_result = refresh_instrument_provider_with_delist_schedule(
        store,
        exchange(ExchangeName.binance, driver_name),
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
        provider_name="binance",
        venue=ExchangeName.binance.value,
        market=market,
        previous_markets=len(result.previous_markets),
        current_markets=len(result.current_markets),
        events=len(result.events),
        scheduled_events=len(provider_result.scheduled_events),
    )


def _sync_hyperliquid(
    *,
    root: str | None,
    market: str | None,
    driver_name: DriverName,
    at: datetime,
) -> None:
    result = refresh_instrument_provider(
        reference_store(root),
        exchange(ExchangeName.hyperliquid, driver_name),
        as_of=at,
        venue=ExchangeName.hyperliquid.value,
        market=market,
    ).refresh
    _write_sync_result(
        root=root,
        at=at,
        provider_name="hyperliquid",
        venue=ExchangeName.hyperliquid.value,
        market=market or "all",
        previous_markets=len(result.previous_markets),
        current_markets=len(result.current_markets),
        events=len(result.events),
    )


def _sync_massive(
    *,
    root: str | None,
    venue: str | None,
    market: str | None,
    driver_name: DriverName,
    at: datetime,
) -> None:
    result = refresh_equity_provider(
        reference_store(root),
        provider(ProviderName.massive, driver_name),
        as_of=at,
        venue=venue,
        params={"asset_class": "equity"},
    ).refresh
    _write_sync_result(
        root=root,
        at=at,
        provider_name="massive",
        venue=venue,
        market=market or "equity",
        previous_markets=len(result.previous_markets),
        current_markets=len(result.current_markets),
        events=len(result.events),
    )


def _write_sync_result(
    *,
    root: str | None,
    at: datetime,
    provider_name: str,
    venue: str | None,
    market: str,
    previous_markets: int,
    current_markets: int,
    events: int,
    scheduled_events: int | None = None,
) -> None:
    catalog = reference_store(root).load_catalog()
    payload: dict[str, object] = {
        "time": at.isoformat(),
        "provider": provider_name,
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
    write_jsonl((payload,), sys.stdout)


@lifecycle_app.command("sync")
def sync_lifecycle(
    provider_name: str = typer.Option(ProviderName.massive.value, "--provider"),
    ticker: str = typer.Option(..., "--ticker"),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    root: str | None = typer.Option(None, "--root"),
    driver_name: DriverName = typer.Option(DriverName.massive, "--driver"),
    venue: str | None = typer.Option(None, "--venue"),
) -> None:
    if provider_name.strip().lower() != ProviderName.massive.value:
        raise typer.BadParameter(f"unsupported lifecycle provider: {provider_name}")
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


@markets_app.command("list")
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


@markets_app.command("stream")
def stream_markets(
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


@lifecycle_app.command("events")
def events(
    root: str | None = typer.Option(None, "--root"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    rows = [lifecycle_event_to_primitive(item) for item in reference_store(root).load_events()]
    if limit is not None:
        rows = rows[:limit]
    write_jsonl(rows, sys.stdout)


@catalog_app.command("view")
def view(
    identifier: str | None = typer.Argument(None),
    root: str | None = typer.Option(None, "--root"),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    if identifier is None:
        _write_catalog_status(root=root, as_of=as_of)
        return
    _write_identifier(identifier, root=root, as_of=as_of)


@catalog_app.command("query")
def query(
    text: str | None = typer.Argument(None),
    kind: ReferenceKind = typer.Option(ReferenceKind.all, "--kind"),
    root: str | None = typer.Option(None, "--root"),
    venue: str | None = typer.Option(None, "--venue"),
    market: str | None = typer.Option(None, "--market"),
    status: MarketStatus | None = typer.Option(None, "--status"),
    active_only: bool = typer.Option(False, "--active-only"),
    as_of: str | None = typer.Option(None, "--as-of"),
    limit: int = typer.Option(50, "--limit"),
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
    write_jsonl(rows[:limit], sys.stdout)


@catalog_app.command("search")
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


@markets_app.command("resolve")
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


@catalog_app.command("show")
def show(
    identifier: str = typer.Argument(...),
    root: str | None = typer.Option(None, "--root"),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    _write_identifier(identifier, root=root, as_of=as_of)


def _write_identifier(identifier: str, *, root: str | None, as_of: str | None) -> None:
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
    write_jsonl(rows, sys.stdout)


@catalog_app.command("status")
def catalog_status(
    root: str | None = typer.Option(None, "--root"),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    _write_catalog_status(root=root, as_of=as_of)


def _write_catalog_status(*, root: str | None, as_of: str | None) -> None:
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


def _effective_output_format(ctx: typer.Context, output_format: OutputFormat | None) -> OutputFormat:
    if output_format is not None and output_format is not OutputFormat.auto:
        return output_format
    try:
        return resolve_output(ctx, output_format, default=OutputFormat.text)
    except ProjectNotFound:
        return OutputFormat.text


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
