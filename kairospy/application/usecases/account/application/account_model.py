from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from kairospy.domain.account import (
    ExternalAccount,
    AccountModel,
    AccountModelChangedEvent,
    AccountModelTransition,
    AccountTransitionStatus,
)


@dataclass(frozen=True, slots=True)
class SwitchAccountModelRequest:
    account: ExternalAccount
    target: AccountModel
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SwitchAccountModelResult:
    account: ExternalAccount
    transition: AccountModelTransition


class AccountModelSwitcher(Protocol):
    def switch(self, request: SwitchAccountModelRequest) -> SwitchAccountModelResult:
        raise NotImplementedError


class ManualAccountModelSwitchPort:
    """Explicitly refuses automatic switching when no venue adapter is wired."""

    def switch(self, request: SwitchAccountModelRequest) -> SwitchAccountModelResult:
        transition = AccountModelTransition(
            request.account.identity,
            request.account.observed_model,
            request.target,
            AccountTransitionStatus.REJECTED,
            "venue adapter does not support automatic account model switching; manual confirmation required",
        )
        return SwitchAccountModelResult(request.account, transition)


class AccountModelChangePublisher(Protocol):
    def publish(self, event: AccountModelChangedEvent) -> None:
        raise NotImplementedError


@dataclass(slots=True)
class AccountModelApplicationService:
    switch_port: AccountModelSwitcher
    publisher: AccountModelChangePublisher | None = None

    def request_switch(self, request: SwitchAccountModelRequest) -> SwitchAccountModelResult:
        account, transition = request.account.request_model_switch(request.target, reason=request.reason)
        if transition.status is AccountTransitionStatus.REJECTED:
            return SwitchAccountModelResult(account, transition)
        result = self.switch_port.switch(SwitchAccountModelRequest(account, request.target, request.reason))
        if result.transition.status is not AccountTransitionStatus.COMPLETED:
            return result
        account = result.account.observe_model(request.target)
        if self.publisher is not None:
            self.publisher.publish(AccountModelChangedEvent(result.transition, datetime.now(timezone.utc)))
        return SwitchAccountModelResult(account, result.transition)


__all__ = [
    "AccountModelApplicationService",
    "AccountModelSwitcher",
    "AccountModelChangePublisher",
    "ManualAccountModelSwitchPort",
    "SwitchAccountModelRequest",
    "SwitchAccountModelResult",
]
