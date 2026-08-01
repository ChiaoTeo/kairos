from __future__ import annotations

from datetime import datetime

from kairospy.application.usecases.account import SimulatedAccount
from kairospy.application.support.launch.accounts import LaunchAccountDirectory
from kairospy.application.support.runtime.services.account.simulated import SimulatedAccountService
from kairospy.core.account import AccountCapability, AccountFeeSchedule, Environment


class PaperAccountService(SimulatedAccountService):
    def __init__(
        self,
        account: SimulatedAccount,
        coordinator: object,
        *,
        initialized_at: datetime | None = None,
        directory: LaunchAccountDirectory | None = None,
        capabilities: tuple[AccountCapability, ...] = (),
        fees: tuple[AccountFeeSchedule, ...] = (),
    ) -> None:
        if account.context.environment is not Environment.PAPER:
            raise ValueError("paper account service requires a paper simulated account")
        super().__init__(account, coordinator, initialized_at=initialized_at, directory=directory, capabilities=capabilities, fees=fees)


__all__ = ["PaperAccountService"]
