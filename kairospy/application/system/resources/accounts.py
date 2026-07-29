from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.runtime import RuntimeMode
from kairospy.application.runtime.processors.account import account_current_view_key
from kairospy.application.runtime.run import RuntimeRunResult
from kairospy.application.service.domain.account import SimulatedAccount
from kairospy.application.service.domain.execution import ImmediateFillModel, PercentageCommissionModel
from kairospy.application.service.modes.backtest.account import BacktestAccountService
from kairospy.application.service.modes.backtest.config import BacktestRunResult, ConfiguredBacktest
from kairospy.application.service.modes.backtest.execution import BacktestExecutionService
from kairospy.application.service.modes.backtest.metrics import MetricsModel, closed_trades_from_fills, equity_point_from_account_view
from kairospy.application.service.modes.common import default_broker, slippage_model
from kairospy.application.service.modes.live.account import LiveAccountService
from kairospy.application.service.modes.live.config import ConfiguredLive, LiveConfigurationError, LiveRunResult
from kairospy.application.service.modes.live.execution import LiveExecutionService
from kairospy.application.service.modes.paper.account import PaperAccountService
from kairospy.application.service.modes.paper.config import ConfiguredPaper, PaperRunResult
from kairospy.application.service.modes.paper.execution import PaperExecutionService
from kairospy.core.account import AccountContext, AccountRef, Environment
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.integrations.payloads import CcxtAccountPayloadAdapter
from kairospy.infrastructure.integrations.protocols import BrokerClient


@dataclass(frozen=True, slots=True)
class BacktestAccountResources:
    account: BacktestAccountService
    execution: BacktestExecutionService

    def build_result(self, configured: ConfiguredBacktest, runtime: RuntimeRunResult) -> BacktestRunResult:
        account_config = self.account.account
        account_view = runtime.views.get(account_current_view_key(account_config.context), None)
        fills = self.execution.fills
        equity_curve = tuple(
            item
            for item in (
                equity_point_from_account_view(
                    None if runtime.runtime.last_event is None else runtime.runtime.last_event.time,
                    account_view,
                ),
            )
            if item is not None
        )
        trades = closed_trades_from_fills(fills)
        metrics = MetricsModel().evaluate(equity_curve, trades, initial_equity=account_config.initial_cash)
        return BacktestRunResult(
            run_id=configured.run_id,
            mode=RuntimeMode.BACKTEST,
            runtime=runtime.runtime,
            views=runtime.views,
            intents=runtime.intents,
            controls=runtime.controls,
            account=account_config.context,
            account_view=account_view,
            fills=fills,
            equity_curve=equity_curve,
            trades=trades,
            metrics=metrics,
        )

    @classmethod
    def from_configured(cls, configured: ConfiguredBacktest) -> "BacktestAccountResources":
        account_config = configured.account_config
        backtest = configured.backtest_config
        account = SimulatedAccount(
            account_config.account_id,
            account_config.cash,
            cash_currency=account_config.currency,
            fee_rate=account_config.fee_rate,
            price_field=account_config.price_field,
        )
        coordinator = ExecutionCoordinator()
        account_service = BacktestAccountService(account, coordinator)
        execution = BacktestExecutionService(
            coordinator,
            account=account.context,
            cash_currency=account.cash_currency,
            price_field=account.price_field,
            fill_model=ImmediateFillModel(volume_field=None if backtest.get("volume_field") is None else str(backtest["volume_field"])),
            slippage_model=slippage_model(configured.execution_config),
            commission_model=PercentageCommissionModel(account.fee_rate),
        )
        return cls(account_service, execution)


@dataclass(frozen=True, slots=True)
class PaperAccountResources:
    account: PaperAccountService
    execution: PaperExecutionService

    def build_result(self, configured: ConfiguredPaper, runtime: RuntimeRunResult) -> PaperRunResult:
        account_context = self.account.account.context
        fills = tuple(self.execution.fills)
        account_view = runtime.views.get(account_current_view_key(account_context), None)
        return PaperRunResult(
            run_id=configured.run_id,
            mode=RuntimeMode.PAPER,
            runtime=runtime.runtime,
            views=runtime.views,
            intents=runtime.intents,
            controls=runtime.controls,
            account=account_context,
            account_view=account_view,
            fills=fills,
            trades=(),
            metrics={},
        )

    @classmethod
    def from_configured(cls, configured: ConfiguredPaper) -> "PaperAccountResources":
        account_config = configured.account_config
        paper = configured.paper_config
        account = SimulatedAccount(
            account_config.account_id,
            account_config.cash,
            cash_currency=account_config.currency,
            broker=str(paper.get("venue", "paper")),
            environment=Environment.PAPER,
            fee_rate=account_config.fee_rate,
            price_field=str(paper.get("price_field", "ask")),
        )
        coordinator = ExecutionCoordinator()
        account_service = PaperAccountService(account, coordinator)
        execution = PaperExecutionService(
            coordinator,
            account=account.context,
            cash_currency=account.cash_currency,
            price_field=account.price_field,
            fill_model=ImmediateFillModel(volume_field=None if paper.get("volume_field") is None else str(paper["volume_field"])),
            slippage_model=slippage_model(configured.execution_config),
            commission_model=PercentageCommissionModel(account.fee_rate),
        )
        return cls(account_service, execution)


@dataclass(frozen=True, slots=True)
class LiveAccountResources:
    account: LiveAccountService
    execution: LiveExecutionService
    coordinator: ExecutionCoordinator

    def build_result(self, configured: ConfiguredLive, runtime: RuntimeRunResult) -> LiveRunResult:
        account_context = self.account.account
        account_view = runtime.views.get(account_current_view_key(account_context), None)
        return LiveRunResult(
            run_id=configured.run_id,
            mode=RuntimeMode.LIVE,
            runtime=runtime.runtime,
            views=runtime.views,
            intents=runtime.intents,
            controls=runtime.controls,
            account=account_context,
            account_view=account_view,
        )

    @classmethod
    def from_configured(cls, configured: ConfiguredLive) -> "LiveAccountResources":
        account_config = configured.account_config
        market_resolver = MarketResolver(default_venue=configured.venue, default_market=configured.market)
        broker_factory = configured.broker_factory or _default_live_broker
        broker = broker_factory(configured.venue, account_config.credential)
        account = AccountContext(AccountRef(configured.venue, account_config.account_id, configured.market), Environment.LIVE)
        coordinator = ExecutionCoordinator(broker=broker, broker_symbol_resolver=market_resolver.broker_symbol)
        account_service = LiveAccountService(
            account,
            coordinator,
            broker=broker,
            parser=CcxtAccountPayloadAdapter(market_resolver),
            balance_params=configured.balance_params,
            open_order_params=configured.order_params,
            stream=broker if configured.watch_private else None,
            stream_symbol=configured.symbol,
            max_balance_events=configured.max_balance_events,
            max_order_events=configured.max_order_events,
            max_trade_events=configured.max_trade_events,
        )
        execution = LiveExecutionService(
            coordinator,
            account=account,
            snapshot_provider=account_service.snapshot,
            safety_policy=configured.safety_policy,
            order_params=configured.order_params,
        )
        return cls(account_service, execution, coordinator)


def _default_live_broker(venue: str, credential: str | None) -> BrokerClient:
    return default_broker(venue, credential, mode_label="live", error_type=LiveConfigurationError)


__all__ = ["BacktestAccountResources", "LiveAccountResources", "PaperAccountResources"]
