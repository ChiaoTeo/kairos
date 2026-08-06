"""Composition of the built-in interactive system resources."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path

from kairospy.application.support.composition.application.common import in_memory_message_bus, reference_runtime
from kairospy.application.support.composition.application.runtime import compose_runtime_assembly
from kairospy.application.system.application.business import SystemApplication
from kairospy.application.actor.account.application.authority import (
    authorize_account_runtime,
    authorize_trading_execution,
    build_trade_authority,
    build_trade_authority_lifecycle,
)
from kairospy.application.support.launch.application.lifecycle import TradingLifecycle
from kairospy.application.system.application.resources import TradingSystemResources
from kairospy.application.usecases.account.application.directory import AccountBinding, AccountDirectory
from kairospy.application.actor.support.services.connections import IntegrationConnectionScope
from kairospy.application.usecases.workspace.application.workspace import KairosWorkspace
from kairospy.application.usecases.account.application.configuration import AccountRecord, AccountStore
from kairospy.application.usecases.account.application.provisioning import AccountProvisioningService
from kairospy.application.usecases.account.application.runtime import SimulatedAccountService
from kairospy.application.usecases.account.application.runtime import account_segment_route, SimulatedAccount
from kairospy.application.usecases.execution.application.runtime import build_execution_coordinator, build_simulated_runtime
from kairospy.domain.account import AccountCapability, AccountRuntimeContext, AccountFeeSchedule, Environment
from kairospy.infrastructure.persistence.services.execution.sqlite_audit import SqliteOrderAuditStore


@dataclass(frozen=True, slots=True)
class ComposedSystem:
    resources: TradingSystemResources
    lifecycle: TradingLifecycle


def compose_system(*, launch_directory: Path, launch_id: str, producer_source: object) -> ComposedSystem:
    workspace = KairosWorkspace.resolve(launch_directory)
    connections = IntegrationConnectionScope()
    directory = _system_account_directory(workspace)
    launch_instance_id = os.environ.get("KAIROS_LAUNCH_INSTANCE_ID") or f"{launch_id}:{os.getpid()}"
    authority = build_trade_authority(
        workspace.account_locks,
        launch_id=launch_id,
        launch_instance_id=launch_instance_id,
        mode="system",
    )
    authority_lifecycle = build_trade_authority_lifecycle(authority, _tradable_contexts(directory))
    if not directory.bindings:
        resources = TradingSystemResources(
            business=SystemApplication(),
            input_streams=(producer_source,),  # type: ignore[arg-type]
            reference=reference_runtime(launch_directory),
            connection_scope=connections,
            message_bus=in_memory_message_bus(),
            assembly=compose_runtime_assembly(),
        )
        return ComposedSystem(resources, authority_lifecycle)

    primary = directory.bindings[0].segments[0]
    account = SimulatedAccount(
        str(primary.identity.account_id),
        initial_balances=(),
        broker=str(primary.identity.broker),
        environment=primary.environment,
        product_family=primary.segment.product_family,
    )
    coordinator = build_execution_coordinator(
        audit_store=SqliteOrderAuditStore(launch_directory / "run.sqlite", instance_id=launch_instance_id),
        instance_id=launch_instance_id,
    )
    account_service = SimulatedAccountService(
        account,
        coordinator.ledger,
        directory=directory,
        capabilities=_system_capabilities(directory),
        fees=_system_fees(directory),
    )
    execution = build_simulated_runtime(
        coordinator,
        account=primary,
        settlement_asset=account.settlement_asset,
        price_field="close",
        directory=directory,
    )
    resources = TradingSystemResources(
        business=SystemApplication(),
        input_streams=(producer_source,),  # type: ignore[arg-type]
        account=authorize_account_runtime(account_service, authority),
        reference=reference_runtime(launch_directory),
        trading_execution=authorize_trading_execution(execution, authority),
        connection_scope=connections,
        message_bus=in_memory_message_bus(),
        assembly=compose_runtime_assembly(),
    )
    return ComposedSystem(resources, authority_lifecycle)


def _system_account_directory(workspace: KairosWorkspace) -> AccountDirectory:
    bindings: list[AccountBinding] = []
    for index, record in enumerate(AccountStore.load(workspace.accounts_root).list()):
        contexts = tuple(_system_account_context(record, segment) for segment in record.segments)
        if contexts:
            bindings.append(
                AccountBinding(
                    record.account_id,
                    index,
                    contexts,
                    ref=record.account_id,
                    trade=_record_has_trade_credential(record),
                )
            )
    return AccountDirectory(tuple(bindings))


def _system_account_context(record: AccountRecord, segment: object) -> AccountRuntimeContext:
    return AccountRuntimeContext(segment.to_segment(record.identity), _environment(record.environment))


def _environment(value: object) -> Environment:
    text = str(value).strip().lower()
    return Environment({"sandbox": "testnet"}.get(text, text))


def _tradable_contexts(directory: AccountDirectory) -> tuple[AccountRuntimeContext, ...]:
    return tuple(
        context
        for binding in directory.bindings
        if binding.trade
        for context in binding.segments
        if account_segment_route(context.segment, broker=context.segment.broker).can_trade
    )


def _system_capabilities(directory: AccountDirectory) -> tuple[AccountCapability, ...]:
    provisioning = AccountProvisioningService()
    return tuple(
        provisioning.capability(context.segment, trade_enabled=binding.trade)
        for binding in directory.bindings
        for context in binding.segments
    )


def _system_fees(directory: AccountDirectory) -> tuple[AccountFeeSchedule, ...]:
    provisioning = AccountProvisioningService()
    return tuple(provisioning.fee_schedule(context.segment, fee_rate=Decimal("0")) for context in directory.contexts())


def _record_has_trade_credential(account: AccountRecord) -> bool:
    credentials = tuple(account.credentials)
    if credentials:
        return any(credential.role == "trade" and credential.ref for credential in credentials)
    if account.credential or account.credential_values:
        return True
    if account.environment.strip().lower() in {"live", "testnet"}:
        return False
    return True


__all__ = ["ComposedSystem", "compose_system"]
