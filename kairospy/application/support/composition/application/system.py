"""Composition of the built-in interactive system resources."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path

from kairospy.application.support.composition.application.common import reference_runtime
from kairospy.application.support.composition.application.runtime_services import compose_runtime_assembly
from kairospy.application.support.composition.services.accounts_authority import (
    AccountTradeAuthority,
    AuthorizingAccountRuntime,
    AuthorizingTradingExecutionService,
)
from kairospy.application.support.runtime.application.launch.lifecycle import TradingLifecycle
from kairospy.application.support.runtime.application.launch.resources import TradingRuntimeResources
from kairospy.application.support.runtime.domain.accounts import RuntimeAccountBinding, RuntimeAccountDirectory
from kairospy.application.support.runtime.domain.connections import DefaultConnectionManager
from kairospy.application.support.system.application.workspace import AccountRecord, KairosWorkspace
from kairospy.application.usecases.account.application.provisioning import AccountProvisioningService
from kairospy.application.usecases.account.application.runtime import SimulatedAccountService
from kairospy.application.usecases.account.domain.routing import account_book_route
from kairospy.application.usecases.account.domain.simulated import SimulatedAccount
from kairospy.application.usecases.execution.application.runtime import build_execution_coordinator, build_simulated_runtime
from kairospy.domain.account import AccountCapability, AccountContext, AccountFeeSchedule, Environment


@dataclass(frozen=True, slots=True)
class ComposedSystem:
    resources: TradingRuntimeResources
    lifecycle: TradingLifecycle


def compose_system(*, launch_directory: Path, launch_id: str, source: object) -> ComposedSystem:
    workspace = KairosWorkspace.resolve(launch_directory)
    connections = DefaultConnectionManager()
    directory = _system_account_directory(workspace)
    launch_instance_id = os.environ.get("KAIROS_LAUNCH_INSTANCE_ID") or f"{launch_id}:{os.getpid()}"
    authority = AccountTradeAuthority(
        workspace.account_locks,
        launch_id=launch_id,
        launch_instance_id=launch_instance_id,
        mode="system",
    )
    authority.acquire_available(_tradable_contexts(directory))
    if not directory.bindings:
        resources = TradingRuntimeResources(
            source=source,  # type: ignore[arg-type]
            reference=reference_runtime(launch_directory),
            connection_scope=connections,
            assembly=compose_runtime_assembly(),
        )
        return ComposedSystem(resources, _SystemTradeAuthorityLifecycle(authority))

    primary = directory.bindings[0].books[0]
    account = SimulatedAccount(
        str(primary.identity.account_id),
        Decimal("0"),
        cash_currency="USD",
        broker=str(primary.identity.broker),
        environment=primary.environment,
        book=primary.book.book,
    )
    coordinator = build_execution_coordinator()
    account_service = SimulatedAccountService(
        account,
        coordinator,
        directory=directory,
        capabilities=_system_capabilities(directory),
        fees=_system_fees(directory),
    )
    execution = build_simulated_runtime(
        coordinator,
        account=primary,
        cash_currency="USD",
        price_field="close",
        directory=directory,
    )
    resources = TradingRuntimeResources(
        source=source,  # type: ignore[arg-type]
        account=AuthorizingAccountRuntime(account_service, authority),
        reference=reference_runtime(launch_directory),
        trading_execution=AuthorizingTradingExecutionService(execution, authority),
        connection_scope=connections,
        assembly=compose_runtime_assembly(),
    )
    return ComposedSystem(resources, _SystemTradeAuthorityLifecycle(authority))


class _SystemTradeAuthorityLifecycle:
    def __init__(self, authority: AccountTradeAuthority) -> None:
        self._authority = authority

    def prepare(self) -> None:
        return None

    def complete(self) -> None:
        self._authority.release()


def _system_account_directory(workspace: KairosWorkspace) -> RuntimeAccountDirectory:
    bindings: list[RuntimeAccountBinding] = []
    for index, record in enumerate(workspace.accounts.list()):
        contexts = tuple(_system_account_context(record, book) for book in record.books)
        if contexts:
            bindings.append(
                RuntimeAccountBinding(
                    record.account_id,
                    index,
                    contexts,
                    ref=record.account_id,
                    trade=_record_has_trade_credential(record),
                )
            )
    return RuntimeAccountDirectory(tuple(bindings))


def _system_account_context(record: AccountRecord, book: object) -> AccountContext:
    ref = getattr(book, "to_ref")(record.identity)
    return AccountContext(ref, _environment(record.environment))


def _environment(value: object) -> Environment:
    text = str(value).strip().lower()
    return Environment({"sandbox": "testnet"}.get(text, text))


def _tradable_contexts(directory: RuntimeAccountDirectory) -> tuple[AccountContext, ...]:
    return tuple(
        context
        for binding in directory.bindings
        if binding.trade
        for context in binding.books
        if account_book_route(context.book, broker=context.book.broker).can_trade
    )


def _system_capabilities(directory: RuntimeAccountDirectory) -> tuple[AccountCapability, ...]:
    provisioning = AccountProvisioningService()
    return tuple(
        provisioning.capability(context.book, trade_enabled=binding.trade)
        for binding in directory.bindings
        for context in binding.books
    )


def _system_fees(directory: RuntimeAccountDirectory) -> tuple[AccountFeeSchedule, ...]:
    provisioning = AccountProvisioningService()
    return tuple(provisioning.fee_schedule(context.book, fee_rate=Decimal("0")) for context in directory.contexts())


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
