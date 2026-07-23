from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from kairospy.environment import Environment
from kairospy.identity import AccountRef, AccountType, InstrumentId, VenueId
from kairospy.identity import InstitutionId
from kairospy.infrastructure.storage.codec import to_primitive
from kairospy.integrations.connectors.binance.account_gateway import (
    BinanceAccountGateway,
    BinanceOptionsAccountGateway,
)
from kairospy.integrations.connectors.binance.execution_gateway import (
    BinanceExecutionGateway,
    BinanceOptionsExecutionGateway,
)
from kairospy.integrations.connectors.binance.request_signing import BinanceSigner
from kairospy.integrations.connectors.binance.rest_transport import UrllibBinanceTransport
from kairospy.integrations.connectors.ibkr.account_gateway import IbkrAccountGateway
from kairospy.integrations.connectors.ibkr.execution_gateway import IbkrExecutionGateway
from kairospy.integrations.connectors.ibkr.reference_data import IbkrReferenceDataClient
from kairospy.integrations.connectors.ibkr.session import IbkrSession
from kairospy.integrations.connectors.simulated import SimulatedExecutionAccountGateway
from kairospy.portfolio.ledger import LedgerBook
from kairospy.reference import ReferenceCatalog, ReferenceCatalogRepository
from kairospy.reference.contracts import BrokerId, ExecutionRoute, RouteId


def _authoritative_runtime_store(args: argparse.Namespace):
    from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore

    runtime_path = Path(args.runtime_db) if args.runtime_db else Path(args.event_log_path).parent / "runtime.sqlite3"
    store = SQLiteRuntimeStore(runtime_path)
    return store, runtime_path

def _runtime_l4_preflight(args: argparse.Namespace) -> dict[str, object]:
    import socket
    environment = Environment(args.environment)
    compatible_environment = (
        args.venue == "binance" and environment is Environment.TESTNET
        or args.venue == "ibkr" and environment is Environment.PAPER
    )
    strategy_id = str(args.strategy)
    instrument_ready = False
    instrument_reason = "instrument catalog is missing"
    catalog_path = Path(args.reference_catalog_path)
    if catalog_path.exists():
        try:
            catalog = ReferenceCatalogRepository(catalog_path).load()
            definition = catalog.instruments.get(InstrumentId(args.instrument), datetime.now(timezone.utc))
            if not catalog.active_listings(definition.instrument_id, datetime.now(timezone.utc)):
                raise LookupError("no active listing")
            instrument_ready = True
            instrument_reason = "active Venue listing found"
        except (LookupError, ValueError) as error:
            instrument_reason = str(error)
    if args.venue == "binance":
        try:
            _credentials(Environment.TESTNET)
            external_ready = True
            external_reason = "Binance testnet credential resolved from Kairos project config"
        except SystemExit as error:
            external_ready = False
            external_reason = str(error)
    else:
        host, port, _client_id = _ibkr_connection_settings()
        connection = socket.socket(); connection.settimeout(0.25)
        try:
            connection.connect((host, port)); external_ready = True
            external_reason = f"IBKR Paper Gateway reachable at {host}:{port}"
        except OSError:
            external_ready = False
            external_reason = f"IBKR Paper Gateway unreachable at {host}:{port}"
        finally:
            connection.close()
    checks = {
        "environment_compatible": compatible_environment,
        "external_connection_ready": external_ready,
        "instrument_listing_ready": instrument_ready,
    }
    payload = {
        "schema_version": 1,
        "kind": "runtime_l4_preflight",
        "ready": all(checks.values()),
        "venue": args.venue,
        "environment": args.environment,
        "strategy": strategy_id,
        "instrument": args.instrument,
        "checks": checks,
        "reasons": {
            "external": external_reason,
            "instrument": instrument_reason,
        },
    }
    if getattr(args, "evidence_artifact", None):
        payload["artifact"] = str(_write_l4_preflight_artifact(args.evidence_artifact, payload))
    return payload

def _write_l4_preflight_artifact(target: str | Path, payload: dict[str, object]) -> Path:
    path = Path(target)
    material = {key: value for key, value in payload.items() if key not in {"artifact", "audit_hash"}}
    audit_hash = sha256(json.dumps(
        to_primitive(material), ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    artifact_payload = {**material, "audit_hash": audit_hash}
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and json.loads(path.read_text(encoding="utf-8")) != artifact_payload:
        raise ValueError("l4 preflight evidence artifact path already contains different content")
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(artifact_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        temporary.replace(path)
    return path

def _ensure_simulated_execution_route(catalog: ReferenceCatalog, account: AccountRef, listings, at: datetime) -> None:
    if not listings:
        raise SystemExit("instrument has no active listing in the reference catalog")
    listing = listings[0]
    try:
        catalog.resolve_execution_route(account, listing.instrument_id, at)
        return
    except LookupError:
        pass
    catalog.routes.add(ExecutionRoute(
        RouteId(f"route:simulated:{account.account_id}:{listing.listing_id.value}"),
        BrokerId("simulated"),
        account,
        listing.listing_id,
        at,
    ))

def _ibkr_session(*, readonly: bool) -> IbkrSession:
    host, port, client_id = _ibkr_connection_settings()
    return IbkrSession(host, port, client_id, readonly)

def _ibkr_connection_settings() -> tuple[str, int, int]:
    from kairospy.infrastructure.configuration import load_project_config_or_none
    from kairospy.integrations.config import resolve_ibkr_trading_connection

    config = load_project_config_or_none()
    settings = resolve_ibkr_trading_connection(config)
    return settings.host, settings.port, settings.client_id

def _credentials(environment: Environment) -> tuple[str, str]:
    from kairospy.infrastructure.configuration import ConfigError, load_project_config_or_none
    from kairospy.integrations.config import resolve_binance_trading_credentials

    config = load_project_config_or_none()
    config_environment = "testnet" if environment is Environment.TESTNET else "live"
    if config is None:
        raise SystemExit("missing Kairos project config for Binance trading credentials")
    try:
        credentials = resolve_binance_trading_credentials(config, config_environment)
    except ConfigError as error:
        raise SystemExit(str(error)) from error
    return credentials.api_key, credentials.api_secret

def _account_gateway(venue: str, environment: Environment, account: AccountRef, ledger, product: str, catalog, inverse: bool):
    if venue == "simulated":
        balances, positions = _local_state(ledger, account)
        return SimulatedExecutionAccountGateway(VenueId("simulated"), account, balances, positions, environment)
    if venue == "ibkr":
        session = _ibkr_session(readonly=True)
        reference = IbkrReferenceDataClient(session)
        for definition in catalog.instruments.values(datetime.now(timezone.utc)):
            if definition.instrument_type.value in {"equity", "etf", "listed_option"}:
                reference.bind_definition(definition, catalog)
        return IbkrAccountGateway(session, environment)
    key, secret = _credentials(environment)
    if product == "options":
        lookup = {
            listing.trading_symbol: listing.instrument_id
            for listing in catalog.listings.values(datetime.now(timezone.utc)) if listing.venue_id == VenueId("binance")
        }
        return BinanceOptionsAccountGateway(
            UrllibBinanceTransport("https://eapi.binance.com"), BinanceSigner(key, secret),
            environment, instrument_lookup=lookup,
        )
    base = "https://testnet.binancefuture.com" if product == "futures" and environment is Environment.TESTNET else "https://dapi.binance.com" if product == "futures" and inverse else "https://fapi.binance.com" if product == "futures" else "https://testnet.binance.vision" if environment is Environment.TESTNET else "https://api.binance.com"
    lookup = {
        listing.trading_symbol: listing.instrument_id
        for listing in catalog.listings.values(datetime.now(timezone.utc)) if listing.venue_id == VenueId("binance")
    }
    return BinanceAccountGateway(UrllibBinanceTransport(base), BinanceSigner(key, secret), environment, futures=product == "futures", inverse=inverse, instrument_lookup=lookup)

def _execution_account_gateway(venue: str, environment: Environment, product: str, definition, catalog, inverse: bool):
    if venue == "ibkr":
        session = _ibkr_session(readonly=False)
        IbkrReferenceDataClient(session).bind_definition(definition, catalog)
        return _CombinedExecutionAccount(IbkrExecutionGateway(session, environment), IbkrAccountGateway(session, environment))
    key, secret = _credentials(environment)
    if product == "options":
        transport, signer = UrllibBinanceTransport("https://eapi.binance.com"), BinanceSigner(key, secret)
        lookup = {
            listing.trading_symbol: listing.instrument_id
            for listing in catalog.listings.values(datetime.now(timezone.utc)) if listing.venue_id == VenueId("binance")
        }
        symbol = next(item.trading_symbol for item in catalog.active_listings(definition.instrument_id, datetime.now(timezone.utc)) if item.venue_id == VenueId("binance"))
        return _CombinedExecutionAccount(
            BinanceOptionsExecutionGateway(transport, signer, environment, instrument_symbols={definition.instrument_id: symbol}),
            BinanceOptionsAccountGateway(transport, signer, environment, instrument_lookup=lookup),
        )
    base = "https://testnet.binancefuture.com" if product == "futures" and environment is Environment.TESTNET else "https://dapi.binance.com" if product == "futures" and inverse else "https://fapi.binance.com" if product == "futures" else "https://testnet.binance.vision" if environment is Environment.TESTNET else "https://api.binance.com"
    transport, signer = UrllibBinanceTransport(base), BinanceSigner(key, secret)
    symbol = next(item.trading_symbol for item in catalog.active_listings(definition.instrument_id, datetime.now(timezone.utc)) if item.venue_id == VenueId("binance"))
    execution = BinanceExecutionGateway(
        transport, signer, environment, futures=product == "futures", inverse=inverse,
        instrument_symbols={definition.instrument_id: symbol},
    )
    lookup = {
        listing.trading_symbol: listing.instrument_id
        for listing in catalog.listings.values(datetime.now(timezone.utc)) if listing.venue_id == VenueId("binance")
    }
    account = BinanceAccountGateway(transport, signer, environment, futures=product == "futures", inverse=inverse, instrument_lookup=lookup)
    return _CombinedExecutionAccount(execution, account)

class _CombinedExecutionAccount:
    def __init__(self, execution, account) -> None:
        self.execution, self.account = execution, account
        self.institution_id = execution.institution_id
        self.venue_id, self.environment, self.capabilities = execution.venue_id, execution.environment, execution.capabilities
    def place_order(self, request): return self.execution.place_order(request)
    def cancel_order(self, account, venue_order_id): return self.execution.cancel_order(account, venue_order_id)
    def open_orders(self, account): return self.execution.open_orders(account)
    def account_state(self, account): return self.account.account_state(account)
    def recover_order(self, account, request, venue_order_id=None):
        recovery = getattr(self.execution, "recover_order", None)
        if not callable(recovery):
            raise NotImplementedError(f"{self.venue_id} execution gateway does not support order recovery")
        return recovery(account, request, venue_order_id)

def _account_key(venue: str, account_id: str, product: str) -> AccountRef:
    return AccountRef(InstitutionId(venue), account_id, _account_type(product))

def _account_type(product: str) -> AccountType:
    return {
        "securities": AccountType.SECURITIES_MARGIN,
        "spot": AccountType.CRYPTO_SPOT,
        "futures": AccountType.DERIVATIVES,
        "options": AccountType.DERIVATIVES,
    }[product]

def _local_state(ledger, account):
    balances, positions = {}, {}
    owned = {LedgerBook.CASH, LedgerBook.AVAILABLE, LedgerBook.LOCKED, LedgerBook.MARGIN, LedgerBook.COLLATERAL, LedgerBook.BORROWED}
    for entry in ledger.entries:
        if entry.account != account:
            continue
        if entry.book in owned:
            balances[entry.asset] = balances.get(entry.asset, Decimal("0")) + entry.amount
        elif entry.book is LedgerBook.POSITION and entry.instrument_id is not None:
            positions[entry.instrument_id] = positions.get(entry.instrument_id, Decimal("0")) + entry.amount
    return tuple(balances.items()), tuple(positions.items())
