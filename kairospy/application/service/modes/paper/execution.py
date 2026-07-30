from __future__ import annotations

from kairospy.application.service.domain.execution import CommissionModel, FillModel, SlippageModel
from kairospy.application.launch import LaunchAccountDirectory
from kairospy.application.service.runtime.execution import SimulatedExecutionService
from kairospy.core.account import AccountContext
from kairospy.core.execution import ExecutionCoordinator


class PaperExecutionService(SimulatedExecutionService):
    def __init__(
        self,
        coordinator: ExecutionCoordinator,
        *,
        account: AccountContext | None = None,
        cash_currency: str = "USD",
        price_field: str = "ask",
        fill_model: FillModel | None = None,
        slippage_model: SlippageModel | None = None,
        commission_model: CommissionModel | None = None,
        directory: LaunchAccountDirectory | None = None,
    ) -> None:
        super().__init__(
            coordinator,
            account=account,
            cash_currency=cash_currency,
            price_field=price_field,
            fill_model=fill_model,
            slippage_model=slippage_model,
            commission_model=commission_model,
            mode_label="paper",
            directory=directory,
        )


__all__ = ["PaperExecutionService"]
