from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from kairospy.environment import Environment
from kairospy.execution.events import TradeSide
from kairospy.execution.orders import ExecutionInstructions, TimeInForce
from kairospy.execution.orders import OrderType
from kairospy.execution.ports import OrderRequest
from kairospy.execution.router import ExecutionRouter
from kairospy.governance.kill_switch import KillSwitch
from kairospy.governance.reconciliation import ReconciliationService
from kairospy.identity import AccountRef, InstrumentId, VenueId
from kairospy.identity import InstitutionId
from kairospy.infrastructure.storage.codec import to_primitive
from kairospy.integrations.connectors.simulated import SimulatedExecutionAccountGateway
from kairospy.portfolio.accounting.ledger import LedgerService
from kairospy.reference import ReferenceCatalog, ReferenceCatalogRepository
from kairospy.reference.access import settlement_asset
from kairospy.runtime.coordinator import ExecutionCoordinator
from kairospy.runtime.store.event_log import PersistentEventLog
from kairospy.surface.cli.rendering.account import (
    _account_credential_fields,
    _emit_accounts_payload,
)
from kairospy.surface.cli.commands.account_runtime import (
    _CombinedExecutionAccount,
    _account_gateway,
    _account_key,
    _account_type,
    _authoritative_runtime_store,
    _credentials,
    _ensure_simulated_execution_route,
    _execution_account_gateway,
    _ibkr_connection_settings,
    _ibkr_session,
    _local_state,
    _runtime_l4_preflight,
    _write_l4_preflight_artifact,
)


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


def runtime_command(args: argparse.Namespace) -> int:
    if args.action == "reference-artifact":
        from kairospy.runtime.profiles.live.reference_artifact import run_runtime_reference_artifact

        result = run_runtime_reference_artifact(args.root)
        payload = {
            "scenario_id": result.scenario_id,
            "audit_hash": result.audit_hash,
            "artifact": str(result.artifact),
        }
    elif args.action == "failure-policy":
        from kairospy.governance.incidents import run_runtime_failure_policy

        result = run_runtime_failure_policy(args.root)
        payload = {
            "policy_id": result["policy_id"],
            "passed": result["passed"],
            "audit_hash": result["audit_hash"],
            "artifact": result["artifact"],
        }
    elif args.action == "orders":
        from kairospy.execution.order_state import DurableOrderStatus
        from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore

        store = SQLiteRuntimeStore(args.db)
        supplied = (args.client_order_id, args.target, args.actor, args.reason, args.evidence)
        if any(value is not None for value in supplied):
            if not all(value is not None for value in supplied):
                raise SystemExit("manual resolution requires --client-order-id, --target, --actor, --reason, and --evidence")
            resolution = store.resolve_unresolved_order(
                args.client_order_id,
                DurableOrderStatus(args.target),
                datetime.now(timezone.utc),
                actor=args.actor,
                reason=args.reason,
                evidence=args.evidence,
            )
            payload = {"resolution": to_primitive(resolution)}
        else:
            payload = {
                "unresolved_orders": [to_primitive(item) for item in store.unresolved_orders()],
                "manual_resolutions": [to_primitive(item) for item in store.manual_order_resolutions()],
            }
    elif args.action == "calibrate-execution":
        from kairospy.execution import build_execution_calibration_release

        release = build_execution_calibration_release(
            args.db,
            args.output_root,
            venue=args.venue,
            environment=args.environment,
            strategy_id=args.strategy,
            calibration_id=args.calibration_id,
        )
        payload = {
            "release_id": release.release_id,
            "release_hash": release.release_hash,
            "manifest": str(release.manifest_path),
            "sample_count": release.manifest["sample_count"],
            "summary": release.manifest["summary"],
            "limitations": release.manifest["limitations"],
        }
    else:
        payload = _runtime_l4_preflight(args)
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ready", True) else 2


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

