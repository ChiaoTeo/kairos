"""Runtime-facing assembly entry points for the account usecase.

The concrete runtime adapters remain private to the account module.  Other
modules import this module when the composition root needs an account runtime
implementation; they do not reach into ``account.services`` directly.
"""

from __future__ import annotations

from kairospy.application.usecases.account.services.runtime.live import LiveAccountService
from kairospy.application.usecases.account.services.runtime.modes.backtest import BacktestAccountService
from kairospy.application.usecases.account.services.runtime.modes.paper import PaperAccountService
from kairospy.application.usecases.account.services.runtime.projections import (
    RuntimeAccountService,
    RuntimeAccountViewProjectionService,
    account_projection,
)
from kairospy.application.usecases.account.services.runtime.simulated import SimulatedAccountService

__all__ = [
    "BacktestAccountService",
    "LiveAccountService",
    "PaperAccountService",
    "RuntimeAccountService",
    "RuntimeAccountViewProjectionService",
    "SimulatedAccountService",
    "account_projection",
]
