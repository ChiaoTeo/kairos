"""Account-level trading authorization.

Account exposes facts such as capabilities and credentials.  This service is
The account usecase owns account capability checks and combines them with the
workspace lease supplied by the running system.
"""

from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.usecases.account.application.capabilities import AccountCapabilityPolicy
from kairospy.application.usecases.workspace.domain.workspace import AccountLease
from kairospy.domain.account import AccountBookRef, AccountCapability, AccountContext


@dataclass(frozen=True, slots=True)
class AccountTradeAuthorizationRequest:
    account: AccountContext | AccountBookRef
    action: str = "trade"
    trade_enabled: bool = True
    has_trade_credential: bool = True
    capability: AccountCapability | None = None
    lock: AccountLease | None = None
    lock_owned: bool = False
    require_trade_lock: bool = False

    @property
    def book(self) -> AccountBookRef:
        return self.account.book if isinstance(self.account, AccountContext) else self.account


@dataclass(frozen=True, slots=True)
class AccountTradeAuthorizationResult:
    allowed: bool
    reasons: tuple[str, ...] = ()
    trade_state: str = "available"

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


@dataclass(frozen=True, slots=True)
class TradingAuthorizationService:
    broker: str | None = None

    def authorize(self, request: AccountTradeAuthorizationRequest) -> AccountTradeAuthorizationResult:
        reasons: list[str] = []
        book = request.book
        trade_state = trade_lock_state(request.lock, owned=request.lock_owned)
        broker = self.broker or str(book.broker)
        if not request.trade_enabled:
            reasons.append(f"account {book.identity.value} is configured read-only")
        if not request.has_trade_credential:
            reasons.append(f"account {book.identity.value} has no trade credential")
        if not AccountCapabilityPolicy(broker).can_trade(book):
            reasons.append(f"account book {book.value} is not tradable")
        if request.capability is not None and not request.capability.can_trade:
            reasons.append(f"account capability for {book.value} does not allow trading")
        if request.require_trade_lock and request.lock is None:
            reasons.append(f"account {book.identity.value} has no trade lock")
        if request.lock is not None and request.lock.stale:
            reasons.append(f"account {book.identity.value} trade lock is stale")
        elif request.lock is not None and not request.lock_owned:
            reasons.append(f"account {book.identity.value} trading is locked by {request.lock.launch_id} ({request.lock.launch_instance_id})")
        return AccountTradeAuthorizationResult(not reasons, tuple(reasons), trade_state)


def trade_lock_state(lock: AccountLease | None, *, owned: bool) -> str:
    if lock is None:
        return "available"
    if lock.stale:
        return "stale"
    return "owned" if owned else "occupied"


__all__ = [
    "AccountTradeAuthorizationRequest",
    "AccountTradeAuthorizationResult",
    "TradingAuthorizationService",
    "trade_lock_state",
]
