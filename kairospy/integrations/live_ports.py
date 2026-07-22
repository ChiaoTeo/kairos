from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from kairospy.identity import AccountRef, AccountType, InstitutionId, InstrumentId, VenueId
from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT, KairosProjectConfig
from kairospy.environment import Environment
from kairospy.execution.ports import ExecutionPort, OrderRecoveryPort
from kairospy.integrations.config import resolve_binance_trading_credentials
from kairospy.portfolio.account_ports import AccountPort
from kairospy.reference.catalog import ReferenceCatalog


@dataclass(frozen=True, slots=True)
class LiveProviderPorts:
    """Provider port instances for a live run, before runtime binding."""

    provider: str
    execution_driver: str
    account: AccountRef
    execution_gateway: ExecutionPort
    account_gateway: AccountPort
    order_recovery_gateway: OrderRecoveryPort | None = None
    market_event_source: object | None = None


@dataclass(frozen=True, slots=True)
class LiveMarketEventSourceBinding:
    """Runtime market EventSource built from a Data Product Live View."""

    provider: str
    name: str
    dataset: str
    live_view_id: str
    event_source: object
    service_id: str
    managed_services: tuple[object, ...]
    plan_hash: str
    service_bundle_hash: str
    manifest_path: Path


def build_live_provider_ports(
    config: KairosProjectConfig,
    *,
    provider: str,
    execution_driver: str,
    account: AccountRef | str,
    reference_catalog: ReferenceCatalog,
    product: str | None = None,
    inverse: bool = False,
    transport_factory: Callable[[str], object] | None = None,
) -> LiveProviderPorts:
    provider_id = provider.strip().lower()
    if provider_id != "binance":
        raise ValueError(f"unsupported live provider binding: {provider!r}")
    account_ref = parse_account_ref(account)
    product_id = _product_from_driver(execution_driver, product)
    execution_gateway, account_gateway = _binance_live_ports(
        config,
        product=product_id,
        inverse=inverse,
        reference_catalog=reference_catalog,
        transport_factory=transport_factory,
    )
    return LiveProviderPorts(
        provider_id,
        execution_driver,
        account_ref,
        execution_gateway,
        account_gateway,
        execution_gateway if hasattr(execution_gateway, "recover_order") else None,
    )


def build_live_market_event_source(
    config: KairosProjectConfig,
    *,
    provider: str,
    name: str,
    dataset: str,
    live_view_id: str,
    lake_root: str | Path | None = None,
    journal_root: str | Path | None = None,
) -> LiveMarketEventSourceBinding:
    provider_id = provider.strip().lower()
    if provider_id != "binance":
        raise ValueError(f"unsupported live market binding provider: {provider!r}")
    if not name.strip() or not dataset.strip() or not live_view_id.strip():
        raise ValueError("live market binding requires name, dataset, and live_view_id")

    from kairospy.data.quality.freshness import (
        LIVE_VIEW_CONFIGURED_FRESHNESS_POLICY,
        evaluate_live_view_freshness,
        freshness_gate_to_primitive,
        live_view_manifest_path,
        load_live_view_manifest,
    )
    from kairospy.integrations.connectors.binance import BinanceRuntimeFeedFactory
    from kairospy.runtime import runtime_feed_plan

    root = Path(lake_root) if lake_root is not None else config.relative_path("paths.lake_root", DEFAULT_LAKE_ROOT)
    manifest_path = live_view_manifest_path(root, dataset, live_view_id)
    manifest = load_live_view_manifest(manifest_path)
    gate = evaluate_live_view_freshness(manifest, policy=LIVE_VIEW_CONFIGURED_FRESHNESS_POLICY)
    if not gate.passed:
        raise ValueError(gate.reason)
    plane = manifest.live_data_plane
    plan = runtime_feed_plan("live", ({
        "name": name,
        "dataset": dataset,
        "live_view_id": live_view_id,
        "event_source_contract": str(plane.get("event_source_contract") or "EventSource[DataSetRecord]"),
        "channel_contract": str(plane.get("channel_contract") or "BoundedEventChannel"),
        "freshness_gate": freshness_gate_to_primitive(gate),
    },))
    feed = BinanceRuntimeFeedFactory(
        root,
        environment=Environment.LIVE,
        journal_root=journal_root,
    ).build(plan)
    service = plan.services[0]
    return LiveMarketEventSourceBinding(
        provider_id,
        name,
        dataset,
        live_view_id,
        feed.channels[service.service_id],
        service.service_id,
        tuple(feed.managed_services),
        plan.plan_hash,
        plan.service_bundle_hash,
        manifest_path,
    )


def parse_account_ref(value: AccountRef | str) -> AccountRef:
    if isinstance(value, AccountRef):
        return value
    parts = str(value).split(":", 2)
    if len(parts) != 3:
        raise ValueError("live provider binding account must be institution:account_type:account_id")
    institution, account_type, account_id = parts
    return AccountRef(InstitutionId(institution), account_id, AccountType(account_type))


def _binance_live_ports(
    config: KairosProjectConfig,
    *,
    product: str,
    inverse: bool,
    reference_catalog: ReferenceCatalog,
    transport_factory: Callable[[str], object] | None,
) -> tuple[ExecutionPort, AccountPort]:
    from kairospy.integrations.connectors.binance import (
        BinanceAccountGateway,
        BinanceExecutionGateway,
        BinanceOptionsAccountGateway,
        BinanceOptionsExecutionGateway,
        BinanceSigner,
        UrllibBinanceTransport,
    )

    credentials = resolve_binance_trading_credentials(config, "live")
    signer = BinanceSigner(credentials.api_key, credentials.api_secret)
    build_transport = transport_factory or (lambda base_url: UrllibBinanceTransport(base_url))
    at = datetime.now(timezone.utc)
    instrument_symbols, instrument_lookup = _binance_symbol_maps(reference_catalog, at)
    if product == "options":
        transport = build_transport("https://eapi.binance.com")
        return (
            BinanceOptionsExecutionGateway(
                transport, signer, Environment.LIVE, instrument_symbols=instrument_symbols,
            ),
            BinanceOptionsAccountGateway(
                transport, signer, Environment.LIVE, instrument_lookup=instrument_lookup,
            ),
        )
    futures = product == "futures"
    base_url = (
        "https://dapi.binance.com"
        if futures and inverse
        else "https://fapi.binance.com"
        if futures
        else "https://api.binance.com"
    )
    transport = build_transport(base_url)
    return (
        BinanceExecutionGateway(
            transport,
            signer,
            Environment.LIVE,
            futures=futures,
            inverse=inverse,
            instrument_symbols=instrument_symbols,
        ),
        BinanceAccountGateway(
            transport,
            signer,
            Environment.LIVE,
            futures=futures,
            inverse=inverse,
            instrument_lookup=instrument_lookup,
        ),
    )


def _binance_symbol_maps(
    reference_catalog: ReferenceCatalog,
    at: datetime,
) -> tuple[dict[InstrumentId, str], dict[str, InstrumentId]]:
    listings = tuple(
        item for item in reference_catalog.listings.values(at) if item.venue_id == VenueId("binance")
    )
    if not listings:
        raise ValueError("live Binance provider binding requires Binance listings in the reference catalog")
    instrument_symbols = {item.instrument_id: item.trading_symbol for item in listings}
    instrument_lookup = {item.trading_symbol: item.instrument_id for item in listings}
    return instrument_symbols, instrument_lookup


def _product_from_driver(execution_driver: str, product: str | None) -> str:
    value = (product or "").strip().lower()
    if not value:
        driver = execution_driver.strip().lower()
        if "option" in driver:
            value = "options"
        elif any(token in driver for token in ("future", "futures", "perp", "usdm", "coinm", "fapi", "dapi")):
            value = "futures"
        else:
            value = "spot"
    aliases = {"spot": "spot", "crypto_spot": "spot", "futures": "futures", "perpetual": "futures", "perp": "futures", "options": "options", "option": "options"}
    try:
        return aliases[value]
    except KeyError as error:
        raise ValueError("live Binance provider binding product must be spot, futures, or options") from error


__all__ = [
    "LiveMarketEventSourceBinding",
    "LiveProviderPorts",
    "build_live_market_event_source",
    "build_live_provider_ports",
    "parse_account_ref",
]
