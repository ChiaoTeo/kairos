from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from kairospy.accounts import AccountContext
from kairospy.context import DataContext
from kairospy.strategy import Strategy
from kairospy.strategy.views import ViewStore

from .components import AccountCurrentProjection, AccountCurrentView, RuntimeComponent
from .events import RuntimeEvent
from .line import RuntimeMode
from .loop import IntentHandler, StrategyRunResult, StrategyRuntime
from .modes import mode_runtime_line
from .sources import EventSource


@dataclass(frozen=True, slots=True)
class ModeRunResult:
    runtime: StrategyRunResult
    views: ViewStore
    account_view: AccountCurrentView
    account_projection: AccountCurrentProjection


class ModeRunner:
    def __init__(
        self,
        strategy: Strategy,
        data: DataContext,
        account: AccountContext,
        mode: RuntimeMode,
        *,
        equity_currency: str | None = None,
        initial_equity: Decimal | str | int | float | None = None,
        components: tuple[RuntimeComponent, ...] = (),
    ) -> None:
        self.strategy = strategy
        self.data = data
        self.account = account
        self.mode = mode
        self.equity_currency = equity_currency
        self.initial_equity = initial_equity
        self.components = tuple(components)

    def run(
        self,
        source: EventSource,
        *,
        pre_events: Iterable[RuntimeEvent] = (),
        started_at=None,
        intent_handler: IntentHandler | None = None,
    ) -> ModeRunResult:
        account_projection = AccountCurrentProjection(
            self.account,
            equity_currency=self.equity_currency,
            initial_equity=self.initial_equity,
        )
        strategy_runtime = StrategyRuntime(
            self.strategy,
            self.data,
            components=(account_projection, *self.components),
        )
        runtime = strategy_runtime.run(
            mode_runtime_line(
                self.mode,
                (*tuple(pre_events), *tuple(source.events())),
                started_at=started_at,
            ),
            intent_handler=intent_handler,
        )
        return ModeRunResult(
            runtime,
            strategy_runtime.views,
            strategy_runtime.views.require(account_projection.key),
            account_projection,
        )


__all__ = ["ModeRunResult", "ModeRunner"]
