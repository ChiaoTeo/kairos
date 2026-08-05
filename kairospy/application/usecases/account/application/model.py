"""Account model transition use cases."""

from __future__ import annotations

from kairospy.application.usecases.account.application.account_model import AccountModelApplicationService, ManualAccountModelSwitchPort, SwitchAccountModelRequest
from kairospy.application.usecases.account.application.configuration import AccountConfigurationError, AccountRecord, AccountStore
from kairospy.application.usecases.account.application.results import AccountModelSwitchResult
from kairospy.application.usecases.workspace.application.context import workspace as resolve_workspace
from kairospy.domain.account import AccountModel, ExternalAccount


class AccountModelApplication:
    """Request an explicit account-model transition for a local binding."""

    def switch(self, account_id: str, *, target: str, reason: str = "") -> AccountModelSwitchResult:
        account = _account(account_id)
        aggregate = ExternalAccount(account.identity, tuple(segment.to_segment(account.identity) for segment in account.segments))
        result = AccountModelApplicationService(ManualAccountModelSwitchPort()).request_switch(
            SwitchAccountModelRequest(aggregate, AccountModel(target.strip().lower()), reason)
        )
        return AccountModelSwitchResult(
            result.account.identity.value,
            None if result.transition.from_model is None else result.transition.from_model.value,
            result.transition.to_model.value,
            result.transition.status.value,
            result.transition.reason,
        )


def _account(account_id: str) -> AccountRecord:
    try:
        return AccountStore.load(resolve_workspace().accounts_root).get(account_id)
    except AccountConfigurationError as error:
        raise ValueError(str(error)) from error


__all__ = ["AccountModelApplication"]
