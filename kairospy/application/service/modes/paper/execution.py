from __future__ import annotations

from kairospy.application.runtime.services.component import RuntimeViewPublisher
from kairospy.application.service.domain.execution import (
    CommissionModel,
    FillModel,
    SimulatedExecutionAdapter,
    SimulatedFill,
    SlippageModel,
    execution_coordinator_components,
)
from kairospy.core.account import AccountContext
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.intent import TradeIntent


class PaperExecutionService:
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
    ) -> None:
        self.coordinator = coordinator
        self.account = account
        self.cash_currency = cash_currency
        self.price_field = price_field
        self.fill_model = fill_model
        self.slippage_model = slippage_model
        self.commission_model = commission_model
        self._fills: list[SimulatedFill] = []
        self._adapter: SimulatedExecutionAdapter | None = None

    @property
    def fills(self) -> tuple[SimulatedFill, ...]:
        return tuple(self._fills)

    def runtime_components(self) -> tuple[RuntimeViewPublisher, ...]:
        return execution_coordinator_components(self.coordinator)

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        adapter = self._require_adapter()
        for intent in intents:
            if not isinstance(intent, TradeIntent):
                continue
            fill = adapter.execute_intent(intent, context)  # type: ignore[arg-type]
            if fill is not None:
                self._fills.append(fill)

    def _require_adapter(self) -> SimulatedExecutionAdapter:
        if self._adapter is not None:
            return self._adapter
        if self.account is None:
            raise RuntimeError("paper execution service requires an account before it can execute intents")
        self._adapter = SimulatedExecutionAdapter(
            account=self.account,
            cash_currency=self.cash_currency,
            price_field=self.price_field,
            coordinator=self.coordinator,
            fill_model=self.fill_model,
            slippage_model=self.slippage_model,
            commission_model=self.commission_model,
        )
        return self._adapter


__all__ = ["PaperExecutionService"]
