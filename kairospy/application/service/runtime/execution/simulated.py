from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from decimal import Decimal

from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.launch import LaunchAccountDirectory
from kairospy.application.ports import TradingExecutionPort
from kairospy.application.service.domain.execution import (
    CommissionModel,
    FillModel,
    SimulatedExecutionAdapter,
    SimulatedFill,
    SlippageModel,
)
from kairospy.core.account import AccountContext, AccountSnapshot
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.intent import TradeIntent
from kairospy.core.order import OrderRequest, OrderState


class SimulatedExecutionService(TradingExecutionPort):
    def __init__(
        self,
        coordinator: ExecutionCoordinator,
        *,
        account: AccountContext | None = None,
        cash_currency: str = "USD",
        price_field: str,
        fill_model: FillModel | None = None,
        slippage_model: SlippageModel | None = None,
        commission_model: CommissionModel | None = None,
        mode_label: str = "simulated",
        directory: LaunchAccountDirectory | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.account = account
        self.directory = directory
        self.cash_currency = cash_currency
        self.price_field = price_field
        self.fill_model = fill_model
        self.slippage_model = slippage_model
        self.commission_model = commission_model
        self.mode_label = mode_label
        self._fills: list[SimulatedFill] = []
        self._adapter: SimulatedExecutionAdapter | None = None
        self._adapters: dict[AccountContext, SimulatedExecutionAdapter] = {}

    @property
    def fills(self) -> tuple[SimulatedFill, ...]:
        return tuple(self._fills)

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if False:
            yield

    def execute_intent(self, intent: TradeIntent, context: object, *, hook: str = "") -> SimulatedFill | None:
        fill = self._adapter_for(intent).execute_intent(intent, context)  # type: ignore[arg-type]
        if fill is not None:
            self._fills.append(fill)
        return fill

    def plan_order(
        self,
        request: OrderRequest,
        *,
        reserve_currency: str | None = None,
        reserve_amount: Decimal | None = None,
        margin_notional: Decimal | None = None,
        margin_leverage: Decimal = Decimal("1"),
        margin_instrument_id: str | None = None,
        venue_snapshot: AccountSnapshot | None = None,
        at: datetime,
    ) -> OrderState:
        return self.coordinator.plan_order(
            request,
            reserve_currency=reserve_currency,
            reserve_amount=reserve_amount,
            margin_notional=margin_notional,
            margin_leverage=margin_leverage,
            margin_instrument_id=margin_instrument_id,
            venue_snapshot=venue_snapshot,
            at=at,
        )

    def submit_order(
        self,
        order_id: str,
        *,
        at: datetime,
        params: Mapping[str, object] | None = None,
    ) -> OrderState:
        return self.coordinator.submit_order(order_id, at=at, params=params)

    def cancel_order(
        self,
        order_id: str,
        *,
        at: datetime,
        params: Mapping[str, object] | None = None,
    ) -> OrderState:
        return self.coordinator.cancel_order(order_id, at=at, params=params)

    def _require_adapter(self) -> SimulatedExecutionAdapter:
        if self._adapter is not None:
            return self._adapter
        if self.account is None:
            raise RuntimeError(f"{self.mode_label} execution service requires an account before it can execute intents")
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

    def _adapter_for(self, intent: TradeIntent) -> SimulatedExecutionAdapter:
        account = self._resolve_account(intent)
        if account is None:
            return self._require_adapter()
        if account in self._adapters:
            return self._adapters[account]
        adapter = SimulatedExecutionAdapter(
            account=account,
            cash_currency=self.cash_currency,
            price_field=self.price_field,
            coordinator=self.coordinator,
            fill_model=self.fill_model,
            slippage_model=self.slippage_model,
            commission_model=self.commission_model,
        )
        self._adapters[account] = adapter
        return adapter

    def _resolve_account(self, intent: TradeIntent) -> AccountContext | None:
        directory = self.directory
        if directory is None:
            return self.account
        return directory.resolve_context(
            account_id=getattr(intent, "account_id", None),
            account_index=getattr(intent, "account_index", None),
            book=getattr(intent, "account_book", None),
            default=self.account,
        )


__all__ = ["SimulatedExecutionService"]
