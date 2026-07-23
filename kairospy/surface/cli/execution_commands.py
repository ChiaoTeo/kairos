from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from kairospy.environment import Environment
from kairospy.execution.events import TradeSide
from kairospy.execution.orders import ExecutionInstructions, TimeInForce
from kairospy.execution.orders import OrderType
from kairospy.execution.ports import OrderRequest
from kairospy.execution.router import ExecutionRouter
from kairospy.governance.kill_switch import KillSwitch
from kairospy.governance.reconciliation import ReconciliationService
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
from kairospy.portfolio.accounting.ledger import LedgerService
from kairospy.portfolio.ledger import LedgerBook
from kairospy.reference import ReferenceCatalog, ReferenceCatalogRepository
from kairospy.reference.access import settlement_asset
from kairospy.reference.contracts import BrokerId, ExecutionRoute, RouteId
from kairospy.runtime.coordinator import ExecutionCoordinator
from kairospy.runtime.store.event_log import PersistentEventLog


def _authoritative_runtime_store(args: argparse.Namespace):
    from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore

    runtime_path = Path(args.runtime_db) if args.runtime_db else Path(args.event_log_path).parent / "runtime.sqlite3"
    store = SQLiteRuntimeStore(runtime_path)
    return store, runtime_path


def _account(args: argparse.Namespace) -> int:
    environment = Environment(args.environment)
    if args.venue == "binance" and args.product == "options" and environment is not Environment.LIVE:
        raise SystemExit("Binance options account is live-only; no equivalent options testnet is available")
    runtime_store, _ = _authoritative_runtime_store(args)
    ledger = runtime_store.load_ledger()
    catalog_repository = ReferenceCatalogRepository(args.reference_catalog_path)
    catalog = catalog_repository.load() if catalog_repository.path.exists() else ReferenceCatalog()
    account = _account_key(args.venue, args.account_id, args.product)
    account_gateway = _account_gateway(args.venue, environment, account, ledger, args.product, catalog, args.inverse)
    report = ReconciliationService(ledger, account_gateway).reconcile(account)
    print(f"Environment: {environment.value.upper()}")
    print(f"Account: {account.value}")
    print(f"Matched: {report.matched}")
    for difference in report.differences:
        print(f"{difference.kind} {difference.key}: local={difference.local} venue={difference.venue}")
    return 0 if report.matched else 2


def _accounts(args: argparse.Namespace) -> int:
    from kairospy.infrastructure.configuration import ConfigError, KairosProjectConfig
    from kairospy.integrations.config import CredentialResolver, resolve_account_binding

    try:
        config = KairosProjectConfig.discover(Path.cwd())
        binding = resolve_account_binding(config, args.account)
    except ConfigError as error:
        payload = {
            "product": "accounts",
            "operation": "doctor",
            "account": args.account,
            "status": "unknown_account",
            "issues": [{"code": "unknown_account", "message": str(error)}],
        }
        _emit_accounts_payload(args, payload)
        return 2
    resolver = CredentialResolver(config)
    credential_fields = _account_credential_fields(binding.credential, config.get(f"credentials.{binding.credential}", {}))
    credential_refs = []
    issues = []
    for field in credential_fields:
        ref = resolver.field(binding.credential, field)
        provided = ref.resolved not in (None, "")
        credential_refs.append({
            "credential": binding.credential,
            "field": field,
            "source": ref.source,
            "provided": provided,
        })
        if not provided:
            issues.append({
                "code": "missing_credential",
                "field": field,
                "source": ref.source,
            })
    if not binding.account_ref:
        issues.append({"code": "missing_account_ref"})
    if not binding.provider:
        issues.append({"code": "missing_provider"})
    if not binding.environment:
        issues.append({"code": "missing_environment"})
    if not binding.permissions:
        issues.append({"code": "missing_permissions"})
    payload = {
        "product": "accounts",
        "operation": "doctor",
        "account": binding.name,
        "status": "available" if not issues else "needs_configuration",
        "account_ref": binding.account_ref,
        "provider": binding.provider,
        "environment": binding.environment,
        "permissions": list(binding.permissions),
        "allowed_products": list(binding.allowed_products),
        "capital_scope": binding.capital_scope,
        "credential": binding.credential,
        "credential_refs": credential_refs,
        "checks": {
            "account_binding": bool(binding.account_ref and binding.provider and binding.environment),
            "credentials": not any(item["code"] == "missing_credential" for item in issues),
            "permissions": bool(binding.permissions),
            "account_query": "not_run",
        },
        "issues": issues,
    }
    _emit_accounts_payload(args, payload)
    return 0 if not issues else 2


def _account_credential_fields(name: str, raw: object) -> tuple[str, ...]:
    if isinstance(raw, dict):
        explicit = tuple(
            field for field in (
                "api_key",
                "api_secret",
                "passphrase",
                "private_key",
                "account_address",
                "host",
                "port",
                "client_id",
            )
            if field in raw
        )
        if explicit:
            return explicit
        kind = str(raw.get("kind") or "")
        if kind == "private_key_account":
            return ("private_key", "account_address")
        if kind == "api_key_secret_passphrase":
            return ("api_key", "api_secret", "passphrase")
        if kind == "api_key_secret":
            return ("api_key", "api_secret")
    if name.startswith("hyperliquid_"):
        return ("private_key", "account_address")
    if name.startswith("ibkr_"):
        return ("host", "port", "client_id")
    return ("api_key", "api_secret")


def _emit_accounts_payload(args: argparse.Namespace, payload: dict[str, object]) -> None:
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"Account: {payload.get('account')}")
    print(f"Status: {payload.get('status')}")
    if payload.get("account_ref"):
        print(f"Account Ref: {payload.get('account_ref')}")
    if payload.get("provider"):
        print(f"Provider: {payload.get('provider')}")
    for issue in payload.get("issues", ()):
        if isinstance(issue, dict):
            print(f"Issue: {issue.get('code')}")


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


def _submit_order_or_runtime_soak(args: argparse.Namespace) -> int:
    environment = Environment(args.environment)
    if environment is Environment.LIVE and not args.confirm_live:
        raise SystemExit("live trading requires --confirm-live")
    if args.venue == "ibkr" and environment is Environment.TESTNET:
        raise SystemExit("IBKR uses paper rather than testnet")
    if args.venue == "binance" and environment is Environment.PAPER:
        raise SystemExit("Binance uses testnet rather than paper")
    if args.venue == "binance" and args.product == "options" and environment is not Environment.LIVE:
        raise SystemExit("Binance options execution is live-only; no equivalent options testnet is available")
    manual_order=bool(getattr(args,"manual_order",False))
    strategy_id = "manual-operations-v1" if manual_order else str(args.strategy)
    if manual_order:
        print(f"Manual operations intent: actor={args.actor} reason={args.reason}")
    catalog_repository = ReferenceCatalogRepository(args.reference_catalog_path)
    if not catalog_repository.path.exists():
        raise SystemExit("catalog is missing; run 'kairospy catalog sync' first")
    catalog = catalog_repository.load()
    definition = catalog.instruments.get(InstrumentId(args.instrument), datetime.now(timezone.utc))
    runtime_store, runtime_path = _authoritative_runtime_store(args)
    ledger = runtime_store.load_ledger()
    listings = catalog.active_listings(definition.instrument_id, datetime.now(timezone.utc))
    venue = listings[0].venue_id if args.venue == "simulated" else VenueId(args.venue)
    account = AccountRef(InstitutionId(args.venue), args.account_id, _account_type(args.product))
    if args.venue == "simulated":
        _ensure_simulated_execution_route(catalog, account, listings, datetime.now(timezone.utc))
        balances, positions = _local_state(ledger, account)
        execution_gateway = SimulatedExecutionAccountGateway(venue, account, balances, positions, environment)
        market_ready = True
    else:
        execution_gateway = _execution_account_gateway(args.venue, environment, args.product, definition, catalog, args.inverse)
        market_ready = args.market_data_ready
    reconciliation = ReconciliationService(ledger, execution_gateway, runtime_store=runtime_store)
    event_log = PersistentEventLog(args.event_log_path)
    from kairospy.execution.ingestion import DurableExecutionIngestionService
    from kairospy.execution.recovery import VenueOrderRecoveryService
    order_recovery = None
    if callable(getattr(execution_gateway, "recover_order", None)):
        order_recovery = VenueOrderRecoveryService(
            runtime_store,
            {account: execution_gateway},
            DurableExecutionIngestionService(
                LedgerService(ledger, catalog),
                runtime_store,
            ),
        )
    kill_switch = KillSwitch((execution_gateway,), runtime_store=runtime_store)
    from kairospy.runtime.application import FunctionProbe, KairosApplication
    from kairospy.runtime.config import ApplicationConfig, RuntimePaths
    from kairospy.runtime.recovery import RuntimeRecoveryService
    runtime_root = runtime_path.parent
    paths = RuntimePaths(runtime_root, Path(args.reference_catalog_path), Path(args.lake_root), runtime_path, runtime_root / "artifacts")
    application = KairosApplication(
        ApplicationConfig(environment, paths), runtime_store, runtime_id=f"cli-{uuid4()}", accounts=(account,),
        order_recovery=order_recovery,
        recovery=RuntimeRecoveryService(
            runtime_store,
            catalog,
            settlement_asset(catalog, definition, datetime.now(timezone.utc)),
            {account: execution_gateway},
            marks={definition.instrument_id: args.limit_price} if args.limit_price is not None else {},
        ),
        probes=(
            FunctionProbe("instrument_catalog", lambda: (True, f"loaded {definition.instrument_id}")),
            FunctionProbe("market_data", lambda: (market_ready, "ready" if market_ready else "not confirmed")),
            FunctionProbe("account", lambda: (execution_gateway.account_state(account).account == account, "account query passed")),
            FunctionProbe("reconciliation", lambda: (
                (report := reconciliation.reconcile(account)).matched,
                "matched" if report.matched else f"{len(report.differences)} differences",
            )),
        ),
    )
    coordinator = ExecutionCoordinator(
        ExecutionRouter(catalog, (execution_gateway,)), {account: reconciliation}, kill_switch, event_log,
        runtime_store=runtime_store, application=application,
    )
    print(f"Environment: {environment.value.upper()}")
    if args.soak_seconds < 0 or args.cycle_seconds <= 0:
        raise SystemExit("--soak-seconds cannot be negative and --cycle-seconds must be positive")
    supervisor = None
    soak_started = None
    if args.soak_seconds:
        from kairospy.runtime.supervisor import RecoveryBackgroundService, RuntimeSupervisor
        from kairospy.governance.observability import OperationalMonitor
        background_services = [RecoveryBackgroundService(order_recovery)] if order_recovery is not None else []
        if args.venue == "ibkr" and order_recovery is not None:
            from kairospy.integrations.connectors.ibkr.ingestion import IbkrDurableFillIngestion
            execution = getattr(execution_gateway, "execution", None)
            session = getattr(execution, "session", None)
            if session is not None:
                background_services = [IbkrDurableFillIngestion(session, order_recovery)]
        if args.venue == "binance" and args.product == "futures":
            from kairospy.integrations.connectors.binance.funding_settlement import BinanceFundingSettlementClient
            from kairospy.integrations.connectors.binance.funding_ingestion import BinanceDurableFundingBackfill
            from kairospy.execution.ingestion import DurableAccountingIngestionService
            execution = getattr(execution_gateway, "execution", None)
            if execution is not None:
                symbols = getattr(execution, "instrument_symbols", {})
                funding_client = BinanceFundingSettlementClient(
                    execution.transport, execution.signer, environment,
                    inverse=bool(getattr(execution, "inverse", False)),
                    instrument_lookup={symbol: instrument for instrument, symbol in symbols.items()},
                )
                background_services.append(BinanceDurableFundingBackfill(
                    account, funding_client,
                    DurableAccountingIngestionService(LedgerService(ledger, catalog), runtime_store),
                ))
        supervisor = RuntimeSupervisor(
            application, {account: reconciliation}, kill_switch,
            OperationalMonitor(application.config.maximum_clock_skew_ms),
            background_services=tuple(background_services), activate=coordinator.activate,
        )
        soak_started = datetime.now(timezone.utc)
        supervisor.start()
    else:
        application.start()
    try:
        if supervisor is None:
            coordinator.activate()
            application.run()
        order_type = OrderType(args.order_type)
        if order_type is OrderType.LIMIT and args.limit_price is None:
            raise SystemExit("limit orders require --limit-price")
        correlation = str(uuid5(NAMESPACE_URL, f"cli:{strategy_id}:{args.instrument}:{datetime.now(timezone.utc).date()}"))
        if manual_order:
            event_log.append(f"manual-intent:{correlation}","manual_order_intent",{
                "actor":args.actor,"reason":args.reason,"strategy_id":strategy_id,
                "instrument_id":args.instrument,"side":args.side,"quantity":str(args.quantity),
                "environment":environment.value,"created_at":datetime.now(timezone.utc).isoformat(),
            })
        request = OrderRequest(
            f"internal-{correlation}", f"client-{correlation}", strategy_id, f"intent-{correlation}", correlation,
            account, definition.instrument_id, TradeSide(args.side), args.quantity,
            ExecutionInstructions(order_type, TimeInForce.DAY, args.limit_price, post_only=args.post_only, reduce_only=args.reduce_only),
        )
        ack = coordinator.submit(request, datetime.now(timezone.utc))
        print(f"Accepted: client={ack.client_order_id} venue_order={ack.venue_order_id} intent={ack.intent_id}")
        if supervisor is not None:
            supervisor.run_for(args.soak_seconds, interval_seconds=args.cycle_seconds)
        if args.kill_switch_drill:
            result = kill_switch.trigger((account,), "CLI drill")
            application.degrade("CLI kill-switch drill")
            print(f"Kill switch: cancelled={len(result.cancelled_orders)} failures={len(result.failures)} reduce_only={kill_switch.reduce_only}")
        if supervisor is not None:
            supervisor.stop()
            restart_passed = False
            if args.restart_drill:
                application.start()
                restart_passed = application.status.value == "ready"
                application.stop()
            from kairospy.runtime.supervisor import write_soak_artifact
            ended = datetime.now(timezone.utc)
            target = args.soak_artifact or (
                paths.artifacts / "soak" / f"{environment.value}-{account.account_id}-{int(soak_started.timestamp())}.json"
            )
            soak = write_soak_artifact(
                supervisor, target, started_at=soak_started, ended_at=ended,
                target_duration_seconds=args.soak_seconds, environment=environment.value,
                restart_drill_passed=restart_passed,
                kill_switch_drill_passed=args.kill_switch_drill and kill_switch.triggered,
            )
            print(json.dumps(soak, ensure_ascii=False, indent=2))
            return 0 if soak["passed"] else 2
        return 0
    finally:
        if supervisor is not None and supervisor.started:
            supervisor.stop()
        elif application.status.value != "stopped":
            application.stop()


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
