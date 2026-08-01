from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from kairospy.application.account_books import default_account_books
from kairospy.application.launch import LaunchAccountBinding, LaunchAccountDirectory
from kairospy.application.modes import RuntimeMode
from kairospy.application.runtime.launch import RuntimeLaunchResult
from kairospy.application.domain.account import SimulatedAccount
from kairospy.application.domain.account.routing import AccountBookRoute, account_book_route
from kairospy.application.domain.execution import ImmediateFillModel, PercentageCommissionModel
from kairospy.application.service.modes.backtest.account import BacktestAccountService
from kairospy.application.service.modes.backtest.config import BacktestLaunchResult, ConfiguredBacktest
from kairospy.application.service.modes.backtest.execution import BacktestExecutionService
from kairospy.application.service.modes.backtest.metrics import MetricsModel, closed_trades_from_fills, equity_point_from_account_view
from kairospy.application.service.modes.common import default_broker, slippage_model
from kairospy.application.service.modes.live.account import LiveAccountService
from kairospy.application.service.modes.live.config import ConfiguredLive, LiveConfigurationError, LiveLaunchResult
from kairospy.application.service.modes.live.execution import LiveExecutionService
from kairospy.application.service.modes.paper.account import PaperAccountService
from kairospy.application.service.modes.paper.config import ConfiguredPaper, PaperLaunchResult
from kairospy.application.service.modes.paper.execution import PaperExecutionService
from kairospy.application.service.modes.common import ConfiguredAccount, default_broker_for_book
from kairospy.config import LaunchAccountConfig
from kairospy.core.account import AccountBookKind, AccountBookRef, AccountCapability, AccountContext, AccountFeeSchedule, Environment, account_current_view_key
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.integrations.payloads import CcxtAccountPayloadAdapter
from kairospy.application.domain.account.bootstrap import AccountBootstrapGateway


@dataclass(frozen=True, slots=True)
class BacktestAccountResources:
    account: BacktestAccountService
    execution: BacktestExecutionService

    def build_result(self, configured: ConfiguredBacktest, runtime: RuntimeLaunchResult) -> BacktestLaunchResult:
        account_config = self.account.account
        account_view = runtime.views.get(account_current_view_key(account_config.context), None)
        fills = self.execution.fills
        equity_curve = _equity_curve(runtime)
        trades = closed_trades_from_fills(fills)
        metrics = MetricsModel().evaluate(equity_curve, trades, initial_equity=account_config.initial_cash)
        return BacktestLaunchResult(
            launch_id=configured.launch_id,
            mode=RuntimeMode.BACKTEST,
            runtime=runtime.runtime,
            views=runtime.views,
            intents=runtime.intents,
            account=account_config.context,
            account_view=account_view,
            fills=fills,
            equity_curve=equity_curve,
            trades=trades,
            decision_trace=_decision_trace(runtime),
            risk_snapshots=_risk_snapshots(runtime),
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


def _equity_curve(runtime: RuntimeLaunchResult) -> tuple[object, ...]:
    equity_view = runtime.views.get("account.equity_curve", None)
    points = tuple(getattr(equity_view, "points", ()) or ())
    if points:
        return points
    risk_curve = _risk_equity_curve(runtime)
    if risk_curve:
        return risk_curve
    account_keys = tuple(key for key in runtime.views.envelopes() if key.startswith("account.current."))
    account_view = runtime.views.get(account_keys[0], None) if account_keys else None
    return tuple(
        item
        for item in (
            equity_point_from_account_view(
                None if runtime.runtime.last_event is None else runtime.runtime.last_event.time,
                account_view,
            ),
        )
        if item is not None
    )


def _decision_trace(runtime: RuntimeLaunchResult) -> tuple[object, ...]:
    view = runtime.views.get("strategy.decision_trace", None)
    return tuple(getattr(view, "records", ()) or ())


def _risk_snapshots(runtime: RuntimeLaunchResult) -> tuple[object, ...]:
    view = runtime.views.get("account.risk_snapshots", None)
    return tuple(getattr(view, "snapshots", ()) or ())


def _risk_equity_curve(runtime: RuntimeLaunchResult) -> tuple[object, ...]:
    return tuple(
        item
        for item in (
            equity_point_from_account_view(getattr(snapshot, "time", None), _RiskEquityView(snapshot))
            for snapshot in _risk_snapshots(runtime)
        )
        if item is not None
    )


class _RiskEquityView:
    def __init__(self, snapshot: object) -> None:
        self.equity = getattr(snapshot, "equity", None)
        self.cash = getattr(snapshot, "cash", None)
        self.positions = tuple(_RiskPositionView(position) for position in tuple(getattr(snapshot, "positions", ()) or ()))


class _RiskPositionView:
    def __init__(self, position: object) -> None:
        self.instrument_id = getattr(position, "instrument_id", "")
        self.quantity = getattr(position, "quantity", 0)


@dataclass(frozen=True, slots=True)
class PaperAccountResources:
    account: PaperAccountService
    execution: PaperExecutionService

    def build_result(self, configured: ConfiguredPaper, runtime: RuntimeLaunchResult) -> PaperLaunchResult:
        account_context = self.account.account.context
        fills = tuple(self.execution.fills)
        account_view = runtime.views.get(account_current_view_key(account_context), None)
        return PaperLaunchResult(
            launch_id=configured.launch_id,
            mode=RuntimeMode.PAPER,
            runtime=runtime.runtime,
            views=runtime.views,
            intents=runtime.intents,
            account=account_context,
            account_view=account_view,
            fills=fills,
            trades=(),
            decision_trace=_decision_trace(runtime),
            risk_snapshots=_risk_snapshots(runtime),
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
            broker=str(paper.get("venue") or account_config.venue or "paper"),
            environment=Environment.PAPER,
            book=_primary_book(configured.launch_accounts, default=str(paper.get("market", "spot"))),
            fee_rate=account_config.fee_rate,
            price_field=str(configured.execution_config.get("price_field") or paper.get("price_field", "ask")),
        )
        coordinator = ExecutionCoordinator()
        directory = _launch_account_directory(
            configured.launch_accounts,
            account_configs=configured.launch_account_configs,
            fallback=account_config,
            fallback_broker=str(paper.get("venue") or account_config.venue or "paper"),
            environment=Environment.PAPER,
            default_book=account.book,
        )
        capabilities = _capabilities(directory)
        fees = _fees(directory, configured.launch_account_configs, fallback=account_config)
        account_service = PaperAccountService(account, coordinator, directory=directory, capabilities=capabilities, fees=fees)
        execution = PaperExecutionService(
            coordinator,
            account=account.context,
            cash_currency=account.cash_currency,
            price_field=account.price_field,
            fill_model=ImmediateFillModel(
                volume_field=(
                    None
                    if (configured.execution_config.get("volume_field") or paper.get("volume_field")) is None
                    else str(configured.execution_config.get("volume_field") or paper["volume_field"])
                )
            ),
            slippage_model=slippage_model(configured.execution_config),
            commission_model=PercentageCommissionModel(account.fee_rate),
            directory=directory,
        )
        return cls(account_service, execution)


@dataclass(frozen=True, slots=True)
class LiveAccountResources:
    account: LiveAccountService
    execution: LiveExecutionService
    coordinator: ExecutionCoordinator

    def build_result(self, configured: ConfiguredLive, runtime: RuntimeLaunchResult) -> LiveLaunchResult:
        account_context = self.account.account
        account_view = runtime.views.get(account_current_view_key(account_context), None)
        return LiveLaunchResult(
            launch_id=configured.launch_id,
            mode=RuntimeMode.LIVE,
            runtime=runtime.runtime,
            views=runtime.views,
            intents=runtime.intents,
            account=account_context,
            account_view=account_view,
            decision_trace=_decision_trace(runtime),
            risk_snapshots=_risk_snapshots(runtime),
        )

    @classmethod
    def from_configured(cls, configured: ConfiguredLive) -> "LiveAccountResources":
        account_config = configured.account_config
        primary_broker = account_config.venue
        primary_book = _primary_book(configured.launch_accounts, default="spot")
        account = AccountContext(AccountBookRef(primary_broker, account_config.account_id, primary_book), Environment.LIVE)
        market_resolver = MarketResolver(default_venue=primary_broker)
        broker_factory = configured.broker_factory or _default_live_broker
        trade_ref = account_config.trade_credential_ref()
        read_ref = account_config.read_credential_ref()
        broker = broker_factory(account.book, read_ref)
        trade_broker = broker if trade_ref == read_ref else broker_factory(account.book, trade_ref)
        directory = _launch_account_directory(
            configured.launch_accounts,
            account_configs=configured.launch_account_configs,
            fallback=account_config,
            fallback_broker=primary_broker,
            environment=Environment.LIVE,
            default_book=primary_book,
        )
        read_brokers = _live_brokers(
            directory,
            configured.launch_account_configs,
            fallback=account_config,
            broker_factory=broker_factory,
            primary=broker,
            primary_book=account.book,
            role="readonly",
        )
        trade_brokers = _live_brokers(
            directory,
            configured.launch_account_configs,
            fallback=account_config,
            broker_factory=broker_factory,
            primary=trade_broker,
            primary_book=account.book,
            role="trade",
            existing=read_brokers,
        )
        read_broker_resolver = _broker_resolver(read_brokers)
        trade_broker_resolver = _broker_resolver(trade_brokers)
        coordinator = ExecutionCoordinator(
            broker=trade_broker,
            broker_resolver=trade_broker_resolver,
            broker_symbol_resolver=market_resolver.broker_symbol,
        )
        capabilities = _capabilities(directory, configured.launch_account_configs, fallback=account_config)
        fees = _fees(directory, configured.launch_account_configs, fallback=account_config)
        routes = _routes(directory, configured.launch_account_configs, fallback=account_config)
        account_service = LiveAccountService(
            account,
            coordinator,
            broker=broker,
            broker_resolver=read_broker_resolver,
            parser=CcxtAccountPayloadAdapter(market_resolver),
            stream=broker if configured.private_sync.enabled else None,
            stream_resolver=read_broker_resolver if configured.private_sync.enabled else None,
            max_balance_events=configured.private_sync.max_balance_events,
            max_order_events=configured.private_sync.max_order_events,
            max_trade_events=configured.private_sync.max_trade_events,
            directory=directory,
            capabilities=capabilities,
            fees=fees,
            routes=routes,
        )
        execution = LiveExecutionService(
            coordinator,
            account=account,
            snapshot_provider=account_service.snapshot,
            safety_policy=configured.safety_policy,
            directory=directory,
            routes=routes,
        )
        return cls(account_service, execution, coordinator)


def _default_live_broker(book: AccountBookRef, credential: str | None) -> AccountBootstrapGateway:
    return default_broker_for_book(book, credential, mode_label="live", error_type=LiveConfigurationError)


def _primary_book(accounts: Mapping[str, LaunchAccountConfig], *, default: str) -> str:
    if not accounts:
        return default
    first = next(iter(accounts.values()))
    return first.books[0] if first.books else default


def _launch_account_directory(
    accounts: Mapping[str, LaunchAccountConfig],
    *,
    account_configs: Mapping[str, ConfiguredAccount],
    fallback: ConfiguredAccount,
    fallback_broker: str,
    environment: Environment,
    default_book: object,
) -> LaunchAccountDirectory:
    if not accounts:
        return LaunchAccountDirectory.from_contexts((AccountContext(AccountBookRef(fallback_broker, fallback.account_id, default_book), environment),))
    bindings: list[LaunchAccountBinding] = []
    for alias, config in accounts.items():
        account_config = account_configs.get(alias, fallback)
        broker = account_config.venue or fallback_broker
        books = config.books or default_account_books(broker, fallback=str(default_book))
        contexts = tuple(AccountContext(AccountBookRef(broker, account_config.account_id, book), environment) for book in books)
        bindings.append(LaunchAccountBinding(alias, config.index, contexts, ref=config.ref, trade=config.trade))
    return LaunchAccountDirectory(tuple(bindings))


def _capabilities(
    directory: LaunchAccountDirectory,
    account_configs: Mapping[str, ConfiguredAccount] | None = None,
    *,
    fallback: ConfiguredAccount | None = None,
) -> tuple[AccountCapability, ...]:
    return tuple(
        _capability(context.book, trade=binding.trade and _account_can_trade_with_credential(binding, account_configs, fallback))
        for binding in directory.bindings
        for context in binding.books
    )


def _capability(book: AccountBookRef, *, trade: bool = True) -> AccountCapability:
    route = account_book_route(book, broker=str(book.broker))
    kind = str(book.book)
    can_hold_position = kind not in {AccountBookKind.FUNDING.value, AccountBookKind.EARN.value}
    can_borrow = kind in {AccountBookKind.CROSS_MARGIN.value, AccountBookKind.ISOLATED_MARGIN.value}
    return AccountCapability(book, can_trade=trade and route.can_trade, can_hold_cash=True, can_hold_position=can_hold_position, can_borrow=can_borrow)


def _routes(
    directory: LaunchAccountDirectory,
    account_configs: Mapping[str, ConfiguredAccount],
    *,
    fallback: ConfiguredAccount,
) -> tuple[AccountBookRoute, ...]:
    routes = []
    for binding in directory.bindings:
        can_trade = binding.trade and _account_can_trade_with_credential(binding, account_configs, fallback)
        for context in binding.books:
            route = account_book_route(context.book, broker=str(context.identity.broker))
            routes.append(
                AccountBookRoute(
                    route.book,
                    balance_params=route.balance_params,
                    order_params=route.order_params,
                    can_trade=route.can_trade and can_trade,
                )
            )
    return tuple(routes)


def _fees(
    directory: LaunchAccountDirectory,
    account_configs: Mapping[str, ConfiguredAccount],
    *,
    fallback: ConfiguredAccount,
) -> tuple[AccountFeeSchedule, ...]:
    schedules: list[AccountFeeSchedule] = []
    for binding in directory.bindings:
        account_config = account_configs.get(binding.alias, fallback)
        for context in binding.books:
            schedules.append(AccountFeeSchedule(context.book, maker=account_config.fee_rate, taker=account_config.fee_rate))
    return tuple(schedules)


def _live_brokers(
    directory: LaunchAccountDirectory,
    account_configs: Mapping[str, ConfiguredAccount],
    *,
    fallback: ConfiguredAccount,
    broker_factory: object,
    primary: AccountBootstrapGateway,
    primary_book: AccountBookRef,
    role: str,
    existing: Mapping[AccountBookRef, AccountBootstrapGateway] | None = None,
) -> Mapping[AccountBookRef, AccountBootstrapGateway]:
    factory = broker_factory  # type: ignore[assignment]
    brokers: dict[AccountBookRef, AccountBootstrapGateway] = {}
    for binding in directory.bindings:
        config = account_configs.get(binding.alias, fallback)
        read_ref = config.read_credential_ref()
        selected_ref = config.trade_credential_ref() if role == "trade" else config.read_credential_ref()
        for context in binding.books:
            key = context.book
            if key in brokers:
                continue
            if key == primary_book:
                brokers[key] = primary
                continue
            if existing is not None and selected_ref == read_ref and key in existing:
                brokers[key] = existing[key]
                continue
            brokers[key] = factory(key, selected_ref)
    if not brokers:
        brokers[AccountBookRef(fallback.venue, fallback.account_id)] = primary
    return brokers


def _account_can_trade_with_credential(
    binding: LaunchAccountBinding,
    account_configs: Mapping[str, ConfiguredAccount] | None,
    fallback: ConfiguredAccount | None,
) -> bool:
    account = account_configs.get(binding.alias) if isinstance(account_configs, Mapping) else None
    if account is None:
        account = fallback
    if account is None:
        return True
    return account.has_trade_credential()


def _broker_resolver(brokers: Mapping[AccountBookRef, AccountBootstrapGateway]):
    def resolve(account: AccountBookRef) -> AccountBootstrapGateway | None:
        return brokers.get(account)

    return resolve


__all__ = ["BacktestAccountResources", "LiveAccountResources", "PaperAccountResources"]
