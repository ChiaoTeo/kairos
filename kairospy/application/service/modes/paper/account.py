from __future__ import annotations

from datetime import datetime

from kairospy.application.service.domain.account import SimulatedAccount
from kairospy.application.launch import LaunchAccountDirectory
from kairospy.application.service.runtime.account import SimulatedAccountService
from kairospy.core.account import AccountCapability, AccountFeeSchedule, Environment
from kairospy.core.execution import ExecutionCoordinator


class PaperAccountService(SimulatedAccountService):
    def __init__(
        self,
        account: SimulatedAccount,
        coordinator: ExecutionCoordinator,
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
