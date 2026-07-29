from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.account import AccountSnapshot
from kairospy.core.order import OrderRequest, OrderState


class TradingExecutionPort(Protocol):
    def events(self) -> AsyncIterator[RuntimeEnvelope]:
        ...

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
        ...

    def submit_order(
        self,
        client_order_id: str,
        *,
        at: datetime,
        params: Mapping[str, object] | None = None,
    ) -> OrderState:
        ...

    def cancel_order(
        self,
        client_order_id: str,
        *,
        at: datetime,
        params: Mapping[str, object] | None = None,
    ) -> OrderState:
        ...


__all__ = ["TradingExecutionPort"]
