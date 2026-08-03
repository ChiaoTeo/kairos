from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping

from kairospy.application.usecases.account.domain.books import default_account_books
from kairospy.application.usecases.account.application.provisioning import AccountProvisioningService
from kairospy.application.support.runtime.domain.accounts import RuntimeAccountBinding, RuntimeAccountDirectory
from kairospy.application.support.runtime.domain.modes import RuntimeMode
from kairospy.application.support.runtime.application.launch import RuntimeLaunchResult
from kairospy.application.usecases.account.domain.simulated import SimulatedAccount
from kairospy.application.usecases.account.domain.routing import AccountBookRoute, account_book_route
from kairospy.application.usecases.execution.application.runtime import (
    build_backtest_runtime,
    build_execution_coordinator,
    build_immediate_fill_model,
    build_live_runtime,
    build_percentage_commission_model,
    build_paper_runtime,
)
from kairospy.application.usecases.account.application.runtime import BacktestAccountService
from kairospy.application.support.launch.application.configuration import BacktestLaunchResult, ConfiguredBacktest
from kairospy.application.support.composition.services.backtest_metrics import MetricsModel, closed_trades_from_fills, equity_point_from_account_view
from kairospy.application.support.composition.application.integrations import connect_binance_spot, integration_application
from kairospy.application.support.launch.application.configuration import slippage_model
from kairospy.application.usecases.account.application.runtime import LiveAccountService
from kairospy.application.usecases.account.application.bootstrap import (
    AccountBootstrapGatewayData,
    AccountBootstrapRequest,
)
from kairospy.application.support.launch.application.configuration import ConfiguredLive, LiveConfigurationError, LiveLaunchResult
from kairospy.application.usecases.account.application.runtime import PaperAccountService
from kairospy.application.support.launch.application.configuration import ConfiguredPaper, PaperLaunchResult, ConfiguredAccount
from kairospy.application.support.system.application.config import LaunchAccountConfig
from kairospy.domain.account import AccountBookRef, AccountCapability, AccountContext, AccountFeeSchedule, Environment, account_current_view_key
from kairospy.domain.reference import MarketResolver
from kairospy.infrastructure.integrations.application.account import ConnectionAccountBootstrapRequest, AccountConnection, AccountStreamConnection
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionApplication, IntegrationConnectionSpec, RuntimeMode as IntegrationRuntimeMode
from kairospy.infrastructure.integrations.application.execution import OrderConnection
from kairospy.infrastructure.integrations.domain import (
    AccessScope,
    BrokerId,
    BrokerRef,
    CredentialRef,
    ExchangeId,
    ExchangeRef,
    ProductFamily,
    TransportKind,
)


@dataclass(frozen=True, slots=True)
class BacktestAccountResources:
    account: BacktestAccountService
    execution: object

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
        coordinator = build_execution_coordinator()
        account_service = BacktestAccountService(account, coordinator)
        execution = build_backtest_runtime(
            coordinator,
            account=account.context,
            cash_currency=account.cash_currency,
            price_field=account.price_field,
            fill_model=build_immediate_fill_model(volume_field=None if backtest.get("volume_field") is None else str(backtest["volume_field"])),
            slippage_model=slippage_model(configured.execution_config),
            commission_model=build_percentage_commission_model(account.fee_rate),
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
    execution: object

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
        coordinator = build_execution_coordinator()
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
        execution = build_paper_runtime(
            coordinator,
            account=account.context,
            cash_currency=account.cash_currency,
            price_field=account.price_field,
            fill_model=build_immediate_fill_model(
                volume_field=(
                    None
                    if (configured.execution_config.get("volume_field") or paper.get("volume_field")) is None
                    else str(configured.execution_config.get("volume_field") or paper["volume_field"])
                )
            ),
            slippage_model=slippage_model(configured.execution_config),
            commission_model=build_percentage_commission_model(account.fee_rate),
            directory=directory,
        )
        return cls(account_service, execution)


@dataclass(frozen=True, slots=True)
class LiveAccountResources:
    account: LiveAccountService
    execution: object
    coordinator: object
    integration_connections: Mapping[str, object] = field(default_factory=dict)

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
    def from_configured(
        cls,
        configured: ConfiguredLive,
        *,
        integration_application: IntegrationConnectionApplication | None = None,
    ) -> "LiveAccountResources":
        if integration_application is not None and configured.broker_factory is None:
            return _from_configured_integration(cls, configured, integration_application)
        if integration_application is None:
            from kairospy.application.support.composition.application.integrations import integration_application as build_integration_application

            integration_application = build_integration_application()
        account_application = integration_application.account
        account_config = configured.account_config
        primary_broker = account_config.venue
        primary_book = _primary_book(configured.launch_accounts, default="spot")
        account = AccountContext(AccountBookRef(primary_broker, account_config.account_id, primary_book), Environment.LIVE)
        market_resolver = MarketResolver(default_venue=primary_broker)
        account_application = replace(account_application, market_resolver=market_resolver)
        parser = account_application.payload_translator
        broker_factory = configured.broker_factory or _live_broker_factory()
        trade_ref = account_config.trade_credential_ref()
        read_ref = account_config.read_credential_ref()
        broker_client = broker_factory(account.book, read_ref)
        trade_broker = broker_client if trade_ref == read_ref else broker_factory(account.book, trade_ref)
        directory = _launch_account_directory(
            configured.launch_accounts,
            account_configs=configured.launch_account_configs,
            fallback=account_config,
            fallback_broker=primary_broker,
            environment=Environment.LIVE,
            default_book=primary_book,
        )
        read_clients = _live_brokers(
            directory,
            configured.launch_account_configs,
            fallback=account_config,
            broker_factory=broker_factory,
            primary=broker_client,
            primary_book=account.book,
            role="readonly",
        )
        trade_clients = _live_brokers(
            directory,
            configured.launch_account_configs,
            fallback=account_config,
            broker_factory=broker_factory,
            primary=trade_broker,
            primary_book=account.book,
            role="trade",
            existing=read_clients,
        )
        read_brokers = _account_bootstrap_gateways(read_clients, account_application)
        broker = read_brokers[account.book]
        private_streams = _account_private_streams(read_clients, account_application)
        account_gateway_resolver = _MappedLiveAccountGatewayResolver(read_brokers, private_streams)
        trade_broker_resolver = _client_resolver(trade_clients)
        coordinator = build_execution_coordinator()
        capabilities = _capabilities(directory, configured.launch_account_configs, fallback=account_config)
        fees = _fees(directory, configured.launch_account_configs, fallback=account_config)
        routes = _routes(directory, configured.launch_account_configs, fallback=account_config)
        account_service = LiveAccountService(
            account,
            coordinator,
            broker=broker,
            gateway_resolver=account_gateway_resolver,
            parser=parser,
            stream=private_streams.get(account.book) if configured.private_sync.enabled else None,
            max_balance_events=configured.private_sync.max_balance_events,
            max_order_events=configured.private_sync.max_order_events,
            max_trade_events=configured.private_sync.max_trade_events,
            directory=directory,
            capabilities=capabilities,
            fees=fees,
            routes=routes,
        )
        execution = build_live_runtime(
            coordinator,
            account=account,
            order_execution=_live_order_execution(
                account.book,
                credential=trade_ref,
                custom_client=trade_broker if configured.broker_factory is not None else None,
                custom_client_resolver=trade_broker_resolver if configured.broker_factory is not None else None,
                integration_app=integration_application,
            ),
            symbol_resolver=market_resolver.broker_symbol,
            account_state=account_service,
            safety_policy=configured.safety_policy,
            directory=directory,
            routes=routes,
        )
        return cls(account_service, execution, coordinator)


def _from_configured_integration(
    resource_type,
    configured: ConfiguredLive,
    integration: IntegrationConnectionApplication,
) -> LiveAccountResources:
    account_config = configured.account_config
    primary_broker = account_config.venue
    primary_book = _primary_book(configured.launch_accounts, default="spot")
    account = AccountContext(AccountBookRef(primary_broker, account_config.account_id, primary_book), Environment.LIVE)
    market_resolver = MarketResolver(default_venue=primary_broker)
    directory = _launch_account_directory(
        configured.launch_accounts,
        account_configs=configured.launch_account_configs,
        fallback=account_config,
        fallback_broker=primary_broker,
        environment=Environment.LIVE,
        default_book=primary_book,
    )
    read_connections: dict[AccountBookRef, object] = {}
    private_connections: dict[AccountBookRef, object] = {}
    execution_connections: dict[AccountBookRef, object] = {}
    resources: dict[str, object] = {}
    for binding in directory.bindings:
        config = configured.launch_account_configs.get(binding.alias, account_config)
        read_credential = config.read_credential_ref()
        trade_credential = config.trade_credential_ref()
        for context in binding.books:
            book = context.book
            credential = CredentialRef(trade_credential or read_credential)
            rest_connection = integration.connect(
                IntegrationConnectionSpec(
                    connection_id=f"live.binance.spot.{book.value}.private-rest",
                    participant=BrokerRef(BrokerId.BINANCE).participant,
                    product=ProductFamily.SPOT,
                    access=AccessScope.PRIVATE,
                    transport=TransportKind.REST,
                    credential=credential,
                    mode=IntegrationRuntimeMode.LIVE,
                )
            )
            stream_connection = integration.connect(
                IntegrationConnectionSpec(
                    connection_id=f"live.binance.spot.{book.value}.private-stream",
                    participant=BrokerRef(BrokerId.BINANCE).participant,
                    product=ProductFamily.SPOT,
                    access=AccessScope.PRIVATE,
                    transport=TransportKind.USER_STREAM,
                    credential=credential,
                    mode=IntegrationRuntimeMode.LIVE,
                )
            )
            read_connections[book] = rest_connection
            private_connections[book] = stream_connection
            execution_connections[book] = rest_connection
            resources[rest_connection.identity.connection_id] = rest_connection
            resources[stream_connection.identity.connection_id] = stream_connection
    if not read_connections:
        raise LiveConfigurationError("live account configuration produced no account connections")
    coordinator = build_execution_coordinator()
    primary_read = read_connections.get(account.book) or next(iter(read_connections.values()))
    primary_private = private_connections.get(account.book) or next(iter(private_connections.values()))
    capabilities = _capabilities(directory, configured.launch_account_configs, fallback=account_config)
    fees = _fees(directory, configured.launch_account_configs, fallback=account_config)
    routes = _routes(directory, configured.launch_account_configs, fallback=account_config)
    bootstrap_gateways = {
        book: _AccountBootstrapGateway(access)
        for book, access in read_connections.items()
    }
    account_gateway_resolver = _MappedLiveAccountGatewayResolver(bootstrap_gateways, private_connections)
    account_service = LiveAccountService(
        account,
        coordinator,
        broker=primary_read,
        gateway_resolver=account_gateway_resolver,
        # Integration private streams now return typed snapshots and
        # ExecutionUpdate values; no vendor payload translator crosses here.
        parser=None,
        stream=primary_private if configured.private_sync.enabled else None,
        max_balance_events=configured.private_sync.max_balance_events,
        max_order_events=configured.private_sync.max_order_events,
        max_trade_events=configured.private_sync.max_trade_events,
        directory=directory,
        capabilities=capabilities,
        fees=fees,
        routes=routes,
    )
    execution = build_live_runtime(
        coordinator,
        account=account,
        order_execution=_MappedExecutionPort(execution_connections),
        symbol_resolver=market_resolver.broker_symbol,
        account_state=account_service,
        safety_policy=configured.safety_policy,
        directory=directory,
        routes=routes,
    )
    return resource_type(account_service, execution, coordinator, resources)


def _default_live_broker(book: AccountBookRef, credential: str | None) -> object:
    connection = connect_binance_spot(
        f"live.binance.spot.{book.value}.{credential or 'default'}",
        market=False,
        account=True,
        execution=True,
        credential=credential,
        mode=IntegrationRuntimeMode.LIVE,
    )
    return connection


def _live_broker_factory():
    return _default_live_broker


def _live_order_execution(
    book: AccountBookRef,
    *,
    credential: str | None,
    custom_client: object | None = None,
    custom_client_resolver: object | None = None,
    integration_app: IntegrationConnectionApplication | None = None,
) -> object:
    if custom_client is not None or custom_client_resolver is not None:
        raise ValueError("custom live clients are not supported by the Binance Spot connection assembly")
    return _default_live_broker(book, credential)


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
) -> RuntimeAccountDirectory:
    if not accounts:
        return RuntimeAccountDirectory.from_contexts((AccountContext(AccountBookRef(fallback_broker, fallback.account_id, default_book), environment),))
    bindings: list[RuntimeAccountBinding] = []
    for alias, config in accounts.items():
        account_config = account_configs.get(alias, fallback)
        broker = account_config.venue or fallback_broker
        books = config.books or default_account_books(broker, fallback=str(default_book))
        contexts = tuple(AccountContext(AccountBookRef(broker, account_config.account_id, book), environment) for book in books)
        bindings.append(RuntimeAccountBinding(alias, config.index, contexts, ref=config.ref, trade=config.trade))
    return RuntimeAccountDirectory(tuple(bindings))


def _capabilities(
    directory: RuntimeAccountDirectory,
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
    return AccountProvisioningService().capability(book, trade_enabled=trade)


def configured_account_book_route(
    book: AccountBookRef,
    *,
    broker: object | None = None,
    base_params: Mapping[str, object] | None = None,
) -> AccountBookRoute:
    broker_name = broker or book.broker
    return account_book_route(book, broker=str(broker_name), base_params=base_params)


def _routes(
    directory: RuntimeAccountDirectory,
    account_configs: Mapping[str, ConfiguredAccount],
    *,
    fallback: ConfiguredAccount,
) -> tuple[AccountBookRoute, ...]:
    routes = []
    for binding in directory.bindings:
        can_trade = binding.trade and _account_can_trade_with_credential(binding, account_configs, fallback)
        for context in binding.books:
            route = configured_account_book_route(context.book, broker=context.identity.broker)
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
    directory: RuntimeAccountDirectory,
    account_configs: Mapping[str, ConfiguredAccount],
    *,
    fallback: ConfiguredAccount,
) -> tuple[AccountFeeSchedule, ...]:
    schedules: list[AccountFeeSchedule] = []
    for binding in directory.bindings:
        account_config = account_configs.get(binding.alias, fallback)
        for context in binding.books:
            schedules.append(AccountProvisioningService().fee_schedule(context.book, fee_rate=account_config.fee_rate))
    return tuple(schedules)


def _live_brokers(
    directory: RuntimeAccountDirectory,
    account_configs: Mapping[str, ConfiguredAccount],
    *,
    fallback: ConfiguredAccount,
    broker_factory: object,
    primary: object,
    primary_book: AccountBookRef,
    role: str,
    existing: Mapping[AccountBookRef, object] | None = None,
) -> Mapping[AccountBookRef, object]:
    factory = broker_factory  # type: ignore[assignment]
    brokers: dict[AccountBookRef, object] = {}
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
    binding: RuntimeAccountBinding,
    account_configs: Mapping[str, ConfiguredAccount] | None,
    fallback: ConfiguredAccount | None,
) -> bool:
    account = account_configs.get(binding.alias) if isinstance(account_configs, Mapping) else None
    if account is None:
        account = fallback
    if account is None:
        return True
    return account.has_trade_credential()


def _account_bootstrap_gateways(
    clients: Mapping[AccountBookRef, object],
    application,
) -> Mapping[AccountBookRef, AccountConnection]:
    del application
    return dict(clients)  # type: ignore[return-value]


def _account_private_streams(clients: Mapping[AccountBookRef, object], application):
    del application
    return dict(clients)


@dataclass(frozen=True, slots=True)
class _MappedLiveAccountGatewayResolver:
    bootstrap_gateways: Mapping[AccountBookRef, object]
    private_streams: Mapping[AccountBookRef, object]

    def resolve_bootstrap_gateway(self, account: AccountBookRef) -> object | None:
        return self.bootstrap_gateways.get(account)

    def resolve_private_stream(self, account: AccountBookRef) -> object | None:
        return self.private_streams.get(account)


@dataclass(frozen=True, slots=True)
class _AccountBootstrapGateway:
    """Composition adapter from account-usecase requests to Integration DTOs."""

    access: AccountConnection

    def bootstrap(self, request: AccountBootstrapRequest) -> AccountBootstrapGatewayData:
        data = self.access.bootstrap(
            ConnectionAccountBootstrapRequest(
                context=request.context,
                observed_at=request.observed_at,
                symbol=request.symbol,
                fetch_orders=request.fetch_orders,
            )
        )
        return AccountBootstrapGatewayData(
            snapshot=data.snapshot,
            imported_updates=(),
        )


@dataclass(frozen=True, slots=True)
class _MappedExecutionPort:
    connections: Mapping[AccountBookRef, object]

    def submit(self, request):
        connection = self.connections.get(request.account)
        if connection is None:
            raise RuntimeError(f"no execution connection configured for account: {request.account.value}")
        return connection.submit(request)

    def cancel(self, request):
        connection = self.connections.get(request.account)
        if connection is None:
            raise RuntimeError(f"no execution connection configured for account: {request.account.value}")
        return connection.cancel(request)


def _client_resolver(clients: Mapping[AccountBookRef, object]):
    def resolve(account: AccountBookRef) -> object | None:
        return clients.get(account)

    return resolve


__all__ = ["BacktestAccountResources", "LiveAccountResources", "PaperAccountResources"]
