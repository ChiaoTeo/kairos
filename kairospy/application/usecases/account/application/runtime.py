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
from kairospy.application.usecases.account.domain.simulated import SimulatedAccount
from kairospy.application.usecases.account.domain.routing import AccountBookRoute, account_book_route
from kairospy.application.usecases.account.domain.books import default_account_books

__all__ = [
    "BacktestAccountService",
    "LiveAccountService",
    "PaperAccountService",
    "RuntimeAccountService",
    "RuntimeAccountViewProjectionService",
    "SimulatedAccountService",
    "SimulatedAccount",
    "AccountBookRoute",
    "account_book_route",
    "default_account_books",
    "account_projection",
]
