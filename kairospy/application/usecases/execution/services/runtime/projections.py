from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.usecases.execution.services.projections import ExecutionProjectionService
from kairospy.application.usecases.execution.services.updates import ExecutionUpdateService
from kairospy.domain.execution import EXECUTION_CURRENT_SCHEMA, EXECUTION_FILLS_SCHEMA, ExecutionCurrentView, ExecutionFillsView
from kairospy.domain.intent import TradeIntent
from kairospy.domain.order import OrderState


@dataclass(frozen=True, slots=True)
class TradingRuntimeExecutionService:
    port: object | None = None
    updates: ExecutionUpdateService | None = None
    projection: ExecutionProjectionService | None = None

    @property
    def has_updates(self) -> bool:
        return self.updates is not None

    @property
    def has_projection(self) -> bool:
        return self.projection is not None

    @property
    def can_execute_intents(self) -> bool:
        return self.port is not None

    def apply_update(self, update: object) -> OrderState:
        if self.updates is None:
            raise RuntimeError("runtime trading execution service has no update use case")
        return self.updates.apply(update)  # type: ignore[arg-type]

    def current_view(self) -> ExecutionCurrentView:
        return _require_execution_projection(self.projection).current_view()

    def fills_view(self) -> ExecutionFillsView:
        return _require_execution_projection(self.projection).fills_view()

    def schemas(self) -> tuple[object, ...]:
        return (EXECUTION_CURRENT_SCHEMA, EXECUTION_FILLS_SCHEMA)

    def submit_intent(self, intent: TradeIntent, context: object) -> object:
        if self.port is None:
            raise RuntimeError("runtime trading execution service has no intent executor")
        return self.port.submit_intent(intent, context)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class RuntimeExecutionService:
    trading: TradingRuntimeExecutionService | None = None

    @property
    def has_updates(self) -> bool:
        return self.trading is not None and self.trading.has_updates

    @property
    def has_projection(self) -> bool:
        return self.trading is not None and self.trading.has_projection

    @property
    def can_execute_intents(self) -> bool:
        return self.trading is not None and self.trading.can_execute_intents

    def apply_update(self, update: object) -> OrderState:
        return _require_trading_execution(self.trading).apply_update(update)

    def current_view(self) -> ExecutionCurrentView:
        return _require_trading_execution(self.trading).current_view()

    def fills_view(self) -> ExecutionFillsView:
        return _require_trading_execution(self.trading).fills_view()

    def schemas(self) -> tuple[object, ...]:
        return _require_trading_execution(self.trading).schemas()

    def submit_intent(self, intent: TradeIntent, context: object) -> object:
        return _require_trading_execution(self.trading).submit_intent(intent, context)


def _require_execution_projection(service: ExecutionProjectionService | None) -> ExecutionProjectionService:
    if service is None:
        raise RuntimeError("runtime execution service has no execution projection")
    return service


def _require_trading_execution(service: TradingRuntimeExecutionService | None) -> TradingRuntimeExecutionService:
    if service is None:
        raise RuntimeError("runtime execution service has no trading execution service")
    return service


__all__ = ["RuntimeExecutionService", "TradingRuntimeExecutionService"]
