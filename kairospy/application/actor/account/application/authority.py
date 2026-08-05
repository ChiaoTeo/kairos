"""ExternalAccount Actor trade authority composition."""

from kairospy.application.actor.account.application.authority_runtime import (
    AccountTradeAuthority,
    AuthorizingAccountRuntime,
    AuthorizingTradingExecutionService,
)
from kairospy.application.usecases.account.application.runtime_capability import AccountRuntimeCapability
from kairospy.application.usecases.execution.application.runtime import LiveExecutionService, SimulatedExecutionRuntimeService
from kairospy.application.usecases.workspace.application.leases import AccountLeaseManager
from kairospy.domain.account import AccountRuntimeContext


def build_trade_authority(manager: AccountLeaseManager, *, launch_id: str, launch_instance_id: str, mode: str) -> AccountTradeAuthority:
    return AccountTradeAuthority(manager, launch_id=launch_id, launch_instance_id=launch_instance_id, mode=mode)


def authorize_account_runtime(account: AccountRuntimeCapability, authority: AccountTradeAuthority) -> AuthorizingAccountRuntime:
    return AuthorizingAccountRuntime(account, authority)


def authorize_trading_execution(execution: LiveExecutionService | SimulatedExecutionRuntimeService, authority: AccountTradeAuthority) -> AuthorizingTradingExecutionService:
    return AuthorizingTradingExecutionService(execution, authority)


class TradeAuthorityLifecycle:
    def __init__(self, authority: AccountTradeAuthority, contexts: tuple[AccountRuntimeContext, ...]) -> None:
        self._authority = authority
        self._contexts = contexts

    def prepare(self) -> None:
        self._authority.acquire_available(self._contexts)  # type: ignore[arg-type]

    def complete(self) -> None:
        self._authority.release()


def build_trade_authority_lifecycle(authority: AccountTradeAuthority, contexts: tuple[AccountRuntimeContext, ...]) -> TradeAuthorityLifecycle:
    return TradeAuthorityLifecycle(authority, contexts)


__all__ = ["AccountTradeAuthority", "AuthorizingAccountRuntime", "AuthorizingTradingExecutionService", "TradeAuthorityLifecycle", "authorize_account_runtime", "authorize_trading_execution", "build_trade_authority", "build_trade_authority_lifecycle"]
