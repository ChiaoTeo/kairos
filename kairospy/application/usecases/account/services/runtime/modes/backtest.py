from __future__ import annotations

from kairospy.application.usecases.account.services.runtime.simulated import SimulatedAccountService


class BacktestAccountService(SimulatedAccountService):
    pass


__all__ = ["BacktestAccountService"]
