from __future__ import annotations

from datetime import datetime

from kairospy.application.usecases.account.domain.simulated import SimulatedAccount
from kairospy.application.usecases.account.application.directory import AccountDirectory
from kairospy.application.usecases.account.services.runtime.simulated import SimulatedAccountService
from kairospy.domain.account import AccountCapability, AccountFeeSchedule, Environment


class PaperAccountService(SimulatedAccountService):
    def __init__(
        self,
        account: SimulatedAccount,
        ledger: object,
        *,
        initialized_at: datetime | None = None,
        directory: AccountDirectory | None = None,
        capabilities: tuple[AccountCapability, ...] = (),
        fees: tuple[AccountFeeSchedule, ...] = (),
    ) -> None:
        if account.context.environment is not Environment.PAPER:
            raise ValueError("paper account service requires a paper simulated account")
        super().__init__(account, ledger, initialized_at=initialized_at, directory=directory, capabilities=capabilities, fees=fees)


__all__ = ["PaperAccountService"]
