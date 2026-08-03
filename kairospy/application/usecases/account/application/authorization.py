"""Public account trade-authorization use case."""

from __future__ import annotations

from kairospy.application.usecases.account.services.authorization import (
    AccountAuthorizationService as _AccountAuthorizationService,
    AccountTradeAuthorizationRequest,
    AccountTradeAuthorizationResult,
    trade_lock_state,
)


class AccountAuthorizationService:
    def __init__(self, broker: str | None = None) -> None:
        self._service = _AccountAuthorizationService(broker)

    def authorize_trade(self, request: AccountTradeAuthorizationRequest) -> AccountTradeAuthorizationResult:
        return self._service.authorize_trade(request)


__all__ = ["AccountAuthorizationService", "AccountTradeAuthorizationRequest", "AccountTradeAuthorizationResult", "trade_lock_state"]
