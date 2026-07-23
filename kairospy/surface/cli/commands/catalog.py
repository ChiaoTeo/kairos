from __future__ import annotations

import argparse
from datetime import datetime, timezone

from kairospy.environment import Environment
from kairospy.identity import InstrumentId
from kairospy.integrations.connectors.binance.market_data_client import BinanceMarketDataClient
from kairospy.integrations.connectors.binance.reference_data import (
    BinanceFuturesReferenceDataClient,
    BinanceOptionsReferenceDataClient,
    BinanceSpotReferenceDataClient,
)
from kairospy.integrations.connectors.binance.rest_transport import UrllibBinanceTransport
from kairospy.integrations.connectors.ibkr.market_data_client import IbkrMarketDataClient
from kairospy.integrations.connectors.ibkr.reference_data import IbkrReferenceDataClient
from kairospy.integrations.connectors.market_data_router import CompositeMarketDataClient
from kairospy.reference.ports import ReferenceDataRequest
from kairospy.reference import ReferenceCatalogRepository
from kairospy.reference.contracts import ProductType
from kairospy.data.snapshots.market_snapshot_storage import MarketSnapshotStorageDriver
from kairospy.research.capture.normalized_series import NormalizedSeriesCaptureService
from kairospy.research.capture.series import SeriesCaptureSpec


def capture_normalized_series_command(args: argparse.Namespace) -> int:
    environment = Environment(args.environment)
    repository = ReferenceCatalogRepository(args.reference_catalog_path)
    if not repository.path.exists():
        raise SystemExit("catalog is missing; run 'kairospy catalog sync' first")
    catalog = repository.load()
    now = datetime.now(timezone.utc)
    definitions = tuple(catalog.instruments.get(InstrumentId(value.strip()), now) for value in args.instruments.split(",") if value.strip())
    session = None
    if args.venue == "ibkr":
        if environment not in {Environment.PAPER, Environment.LIVE}:
            raise SystemExit("IBKR normalized capture requires paper or live environment")
        session = ibkr_session(readonly=True)
        reference = IbkrReferenceDataClient(session)
        for definition in definitions:
            reference.bind_definition(definition, catalog)
        provider = IbkrMarketDataClient(session)
    else:
        if environment not in {Environment.TESTNET, Environment.LIVE}:
            raise SystemExit("Binance normalized capture requires testnet or live environment")
        spot_base = "https://testnet.binance.vision" if environment is Environment.TESTNET else "https://api.binance.com"
        futures_base = "https://testnet.binancefuture.com" if environment is Environment.TESTNET else "https://dapi.binance.com" if args.inverse else "https://fapi.binance.com"
        futures_path = "/dapi/v1/ticker/bookTicker" if args.inverse else "/fapi/v1/ticker/bookTicker"
        routes = {
            ProductType.CRYPTO_SPOT: BinanceMarketDataClient(UrllibBinanceTransport(spot_base)),
            ProductType.PERPETUAL: BinanceMarketDataClient(UrllibBinanceTransport(futures_base), ProductType.PERPETUAL, path=futures_path),
            ProductType.FUTURE: BinanceMarketDataClient(UrllibBinanceTransport(futures_base), ProductType.FUTURE, path=futures_path),
        }
        if environment is Environment.LIVE:
            routes[ProductType.CRYPTO_OPTION] = BinanceMarketDataClient(UrllibBinanceTransport("https://eapi.binance.com"), ProductType.CRYPTO_OPTION)
        provider = CompositeMarketDataClient(routes)
    series_spec = SeriesCaptureSpec(args.dataset_id, args.samples, args.interval_seconds, args.split)
    try:
        dataset = NormalizedSeriesCaptureService(MarketSnapshotStorageDriver(args.dataset_root)).capture(
            provider, catalog, definitions, series_spec, source=f"{args.venue}.normalized-series", market_data_type=environment.value,
        )
    finally:
        if session is not None:
            session.disconnect()
    print(f"Dataset: {dataset.manifest.dataset_id}")
    print(f"Products: {','.join(sorted({item.instrument_type.value for item in definitions}))}")
    print(f"Slices: {dataset.manifest.slice_count}")
    print(f"Hash: {dataset.manifest.content_hash}")
    return 0


def catalog_command(args: argparse.Namespace) -> int:
    environment = Environment(args.environment)
    products = {item.strip() for item in args.products.split(",") if item.strip()}
    symbols = tuple(item.strip() for item in args.symbols.split(",") if item.strip())
    from kairospy.reference import ReferenceCatalog
    from kairospy.reference.repository import ReferenceCatalogRepository
    repository = ReferenceCatalogRepository(args.reference_catalog_path)
    catalog = repository.load() if repository.path.exists() else ReferenceCatalog()
    before = len(catalog.instruments.values())
    if args.venue == "ibkr":
        if environment not in {Environment.PAPER, Environment.LIVE}:
            raise SystemExit("IBKR catalog sync requires paper or live environment")
        session = ibkr_session(readonly=True)
        reference_client = IbkrReferenceDataClient(session)
        try:
            if "equity" in products:
                catalog.merge(reference_client.sync(ReferenceDataRequest(ProductType.EQUITY, tuple(item for item in symbols if ":" not in item))))
            if "option" in products:
                catalog.merge(reference_client.sync(ReferenceDataRequest(ProductType.LISTED_OPTION, tuple(item for item in symbols if ":" in item))))
        finally:
            session.disconnect()
    else:
        if environment not in {Environment.TESTNET, Environment.LIVE}:
            raise SystemExit("Binance catalog sync requires testnet or live environment")
        if "spot" in products:
            transport = UrllibBinanceTransport("https://testnet.binance.vision" if environment is Environment.TESTNET else "https://api.binance.com")
            catalog.merge(BinanceSpotReferenceDataClient(transport).sync(ReferenceDataRequest(ProductType.CRYPTO_SPOT, symbols)))
        if "perpetual" in products:
            transport = UrllibBinanceTransport("https://testnet.binancefuture.com" if environment is Environment.TESTNET else "https://dapi.binance.com" if args.inverse else "https://fapi.binance.com")
            catalog.merge(BinanceFuturesReferenceDataClient(transport, inverse=args.inverse).sync(ReferenceDataRequest(ProductType.PERPETUAL, symbols)))
        if "future" in products:
            transport = UrllibBinanceTransport("https://testnet.binancefuture.com" if environment is Environment.TESTNET else "https://dapi.binance.com" if args.inverse else "https://fapi.binance.com")
            catalog.merge(BinanceFuturesReferenceDataClient(transport, inverse=args.inverse).sync(ReferenceDataRequest(ProductType.FUTURE, symbols)))
        if "option" in products:
            if environment is Environment.TESTNET:
                raise SystemExit("Binance options do not provide the same public testnet contract; use live public reference data only")
            catalog.merge(BinanceOptionsReferenceDataClient(UrllibBinanceTransport("https://eapi.binance.com")).sync(ReferenceDataRequest(ProductType.CRYPTO_OPTION, symbols)))
    repository.save(catalog)
    print(f"Reference Catalog: {repository.path}")
    print(f"Synced: {len(catalog.instruments.values()) - before} instruments from {args.venue} ({environment.value})")
    return 0


def ibkr_session(*, readonly: bool):
    from kairospy.surface.cli.commands.account import _ibkr_session

    return _ibkr_session(readonly=readonly)
