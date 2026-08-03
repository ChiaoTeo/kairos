from __future__ import annotations

from datetime import datetime

from kairospy.application.usecases.account.domain.simulated import SimulatedAccount
from kairospy.application.support.runtime.domain.accounts import RuntimeAccountDirectory
from kairospy.application.usecases.account.services.runtime.simulated import SimulatedAccountService
from kairospy.domain.account import AccountCapability, AccountFeeSchedule, Environment


class PaperAccountService(SimulatedAccountService):
    def __init__(
        self,
        account: SimulatedAccount,
        coordinator: object,
        *,
        initialized_at: datetime | None = None,
        directory: RuntimeAccountDirectory | None = None,
        capabilities: tuple[AccountCapability, ...] = (),
        fees: tuple[AccountFeeSchedule, ...] = (),
    ) -> None:
        if account.context.environment is not Environment.PAPER:
            raise ValueError("paper account service requires a paper simulated account")
        super().__init__(account, coordinator, initialized_at=initialized_at, directory=directory, capabilities=capabilities, fees=fees)


__all__ = ["PaperAccountService"]
