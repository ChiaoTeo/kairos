from __future__ import annotations

from kairospy.application.usecases.execution.services.simulation import CommissionModel, FillModel, SlippageModel
from kairospy.application.usecases.execution.services.runtime.simulated import SimulatedExecutionRuntimeService
from kairospy.domain.account import AccountContext


class BacktestExecutionService(SimulatedExecutionRuntimeService):
    def __init__(
        self,
        coordinator: object,
        *,
        account: AccountContext | None = None,
        cash_currency: str = "USD",
        price_field: str = "close",
        fill_model: FillModel | None = None,
        slippage_model: SlippageModel | None = None,
        commission_model: CommissionModel | None = None,
    ) -> None:
        super().__init__(
            coordinator,
            account=account,
            cash_currency=cash_currency,
            price_field=price_field,
            fill_model=fill_model,
            slippage_model=slippage_model,
            commission_model=commission_model,
            mode_label="backtest",
        )


__all__ = ["BacktestExecutionService"]
