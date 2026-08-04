"""Account Actor trade authority composition."""

from kairospy.application.actor.account.application.authority_runtime import (
    AccountTradeAuthority,
    AuthorizingAccountRuntime,
    AuthorizingTradingExecutionService,
)


def build_trade_authority(manager: object, *, launch_id: str, launch_instance_id: str, mode: str) -> AccountTradeAuthority:
    return AccountTradeAuthority(manager, launch_id=launch_id, launch_instance_id=launch_instance_id, mode=mode)  # type: ignore[arg-type]


def authorize_account_runtime(account: object, authority: AccountTradeAuthority) -> AuthorizingAccountRuntime:
    return AuthorizingAccountRuntime(account, authority)  # type: ignore[arg-type]


def authorize_trading_execution(execution: object, authority: AccountTradeAuthority) -> AuthorizingTradingExecutionService:
    return AuthorizingTradingExecutionService(execution, authority)  # type: ignore[arg-type]


class TradeAuthorityLifecycle:
    def __init__(self, authority: AccountTradeAuthority, contexts: tuple[object, ...]) -> None:
        self._authority = authority
        self._contexts = contexts

    def prepare(self) -> None:
        self._authority.acquire_available(self._contexts)  # type: ignore[arg-type]

    def complete(self) -> None:
        self._authority.release()


def build_trade_authority_lifecycle(authority: AccountTradeAuthority, contexts: tuple[object, ...]) -> TradeAuthorityLifecycle:
    return TradeAuthorityLifecycle(authority, contexts)


__all__ = ["AccountTradeAuthority", "AuthorizingAccountRuntime", "AuthorizingTradingExecutionService", "TradeAuthorityLifecycle", "authorize_account_runtime", "authorize_trading_execution", "build_trade_authority", "build_trade_authority_lifecycle"]
