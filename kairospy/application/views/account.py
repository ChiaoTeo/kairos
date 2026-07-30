from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.service.domain.execution import SimulatedEquityPoint
from kairospy.core.account import AccountContext
from kairospy.core.views import ViewFieldSchema, ViewSchema


class AccountRuntimeViewKeys:
    equity_curve = "account.equity_curve"


@dataclass(frozen=True, slots=True)
class EquityCurveView:
    account: AccountContext
    points: tuple[SimulatedEquityPoint, ...]


ACCOUNT_EQUITY_CURVE_SCHEMA = ViewSchema(
    AccountRuntimeViewKeys.equity_curve,
    "system",
    fields=(
        ViewFieldSchema("account", "account identity", "runtime state", "account ledger"),
        ViewFieldSchema("points", "marked equity curve points", "event time", "market and account state"),
    ),
    mutability="runtime_writable",
    evidence="marked simulated account equity curve",
)


__all__ = ["ACCOUNT_EQUITY_CURVE_SCHEMA", "AccountRuntimeViewKeys", "EquityCurveView"]
