from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping

from kairospy.application.usecases.account.application.runtime import default_account_books
from kairospy.application.usecases.account.application.provisioning import AccountProvisioningService
from kairospy.application.usecases.account.application.directory import AccountBinding, AccountDirectory
from kairospy.application.usecases.account.application.runtime import SimulatedAccount, AccountBookRoute, account_book_route
from kairospy.application.usecases.execution.application.runtime import (
    build_backtest_runtime,
    build_execution_coordinator,
    build_immediate_fill_model,
    build_live_runtime,
    build_percentage_commission_model,
    build_paper_runtime,
)
from kairospy.application.usecases.account.application.runtime import BacktestAccountService
from kairospy.application.support.launch.application.configuration import ConfiguredBacktest
from kairospy.application.support.composition.application.integrations import connect_binance_spot_account, connect_binance_spot_execution, integration_application
from kairospy.application.support.launch.application.configuration import slippage_model
from kairospy.application.usecases.account.application.runtime import LiveAccountService
from kairospy.application.usecases.account.protocol import AccountLoginPort, AccountLoginRequest, AccountLoginResult, AccountReadRequest, AccountSession
from kairospy.application.support.launch.application.configuration import ConfiguredLive, LiveConfigurationError
from kairospy.application.usecases.account.application.runtime import PaperAccountService
from kairospy.application.usecases.earn.application import EarnApplication
from kairospy.application.support.launch.application.configuration import ConfiguredPaper, ConfiguredAccount
from kairospy.application.support.launch.application.configuration import LaunchAccountConfig
from kairospy.domain.account import AccountBookRef, AccountCapability, AccountContext, AccountFeeSchedule, Environment
from kairospy.domain.reference import MarketResolver
from kairospy.infrastructure.integrations.application.account import (
    ConnectionAccountMarketProfileData,
    ConnectionAccountMarketProfileRequest,
    ConnectionAccountReadRequest,
    ConnectionAccountStreamRequest,
    AccountConnection,
    AccountStreamConnection,
)
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionApplication, IntegrationConnectionSpec, RuntimeMode as IntegrationRuntimeMode
from kairospy.infrastructure.integrations.domain import (
    AccessScope,
    BrokerId,
    BrokerRef,
    CredentialRef,
    ExchangeId,
    ExchangeRef,
    IntegrationCapability,
    IntegrationRoute,
    ProductFamily,
    TransportKind,
)


@dataclass(frozen=True, slots=True)
class BacktestAccountResources:
    account: BacktestAccountService
    execution: object

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
        account_service = BacktestAccountService(account, coordinator.ledger)
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



@dataclass(frozen=True, slots=True)
class PaperAccountResources:
    account: PaperAccountService
    execution: object

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
        account_service = PaperAccountService(account, coordinator.ledger, directory=directory, capabilities=capabilities, fees=fees)
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
    earn: EarnApplication | None = None

    @classmethod
    def from_configured(
        cls,
        configured: ConfiguredLive,
        *,
        integration_application: IntegrationConnectionApplication | None = None,
    ) -> "LiveAccountResources":
        if configured.broker_factory is None:
            if integration_application is None:
                from kairospy.application.support.composition.application.integrations import integration_application as build_integration_application

                integration_application = build_integration_application()
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
        read_brokers = _account_readers(read_clients, account_application)
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
            coordinator.ledger,
            broker=broker,
            login_port=_MappedAccountLoginPort(read_brokers, private_streams),
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
            market_profile_port=account_gateway_resolver,
        )
        execution = build_live_runtime(
            coordinator,
            account=account,
            order_connection=_live_order_connection(
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
    earn_applications: dict[AccountBookRef, EarnApplication] = {}
    for binding in directory.bindings:
        config = configured.launch_account_configs.get(binding.alias, account_config)
        read_credential = config.read_credential_ref()
        trade_credential = config.trade_credential_ref()
        for context in binding.books:
            book = context.book
            credential = CredentialRef(trade_credential or read_credential)
            product = _integration_product(book)
            broker_name = _canonical_broker_name(context.book.broker)
            route = IntegrationRoute(broker=BrokerRef(BrokerId(broker_name)))
            account_capability = (
                IntegrationCapability.EARN
                if product is ProductFamily.EARN
                else IntegrationCapability.ACCOUNT_MARKET_PROFILE_READ
            )
            rest_connection = integration.connect(
                IntegrationConnectionSpec(
                    connection_id=f"live.{broker_name}.{product.value}.{book.value}.private-rest",
                    route=route,
                    product=product,
                    access=AccessScope.PRIVATE,
                    transport=TransportKind.REST,
                    capability=account_capability,
                    credential=credential,
                    mode=IntegrationRuntimeMode.LIVE,
                )
            )
            stream_connection = None
            if product is ProductFamily.SPOT:
                stream_connection = integration.connect(
                    IntegrationConnectionSpec(
                        connection_id=f"live.{broker_name}.{product.value}.{book.value}.private-stream",
                        route=route,
                        product=product,
                        access=AccessScope.PRIVATE,
                        transport=TransportKind.USER_STREAM,
                        capability=IntegrationCapability.ACCOUNT_STREAM,
                        credential=credential,
                        mode=IntegrationRuntimeMode.LIVE,
                    )
                )
            read_connections[book] = rest_connection
            if product is ProductFamily.EARN:
                earn_applications[book] = EarnApplication(rest_connection)  # type: ignore[arg-type]
            if stream_connection is not None:
                private_connections[book] = stream_connection
            if product is not ProductFamily.EARN:
                execution_connection = integration.connect(
                    IntegrationConnectionSpec(
                        connection_id=f"live.{broker_name}.{product.value}.{book.value}.execution-rest",
                        route=route,
                        product=product,
                        access=AccessScope.PRIVATE,
                        transport=TransportKind.REST,
                        capability=IntegrationCapability.ORDER_ENTRY,
                        credential=credential,
                        mode=IntegrationRuntimeMode.LIVE,
                    )
                )
                execution_connections[book] = execution_connection
            resources[rest_connection.identity.connection_id] = rest_connection
            if stream_connection is not None:
                resources[stream_connection.identity.connection_id] = stream_connection
            if product is not ProductFamily.EARN:
                resources[execution_connection.identity.connection_id] = execution_connection
    if not read_connections:
        raise LiveConfigurationError("live account configuration produced no account connections")
    coordinator = build_execution_coordinator()
    primary_read = read_connections.get(account.book) or next(iter(read_connections.values()))
    primary_private = private_connections.get(account.book)
    capabilities = _capabilities(directory, configured.launch_account_configs, fallback=account_config)
    fees = _fees(directory, configured.launch_account_configs, fallback=account_config)
    routes = _routes(directory, configured.launch_account_configs, fallback=account_config)
    account_readers = {
        book: _AccountReader(access)
        for book, access in read_connections.items()
    }
    account_gateway_resolver = _MappedLiveAccountGatewayResolver(account_readers, private_connections)
    account_service = LiveAccountService(
        account,
        coordinator.ledger,
        broker=primary_read,
        login_port=_MappedAccountLoginPort(account_readers, private_connections),
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
        market_profile_port=account_gateway_resolver,
    )
    execution = build_live_runtime(
        coordinator,
        account=account,
        order_connection=execution_connections,
        update_source=_MappedExecutionUpdateSource(execution_connections),
        symbol_resolver=market_resolver.broker_symbol,
        account_state=account_service,
        safety_policy=configured.safety_policy,
        directory=directory,
        routes=routes,
    )
    return resource_type(account_service, execution, coordinator, resources, earn_applications.get(account.book))


def _default_live_broker(book: AccountBookRef, credential: str | None) -> object:
    connection = connect_binance_spot_account(
        f"live.binance.spot.{book.value}.{credential or 'default'}",
        credential=credential,
        mode=IntegrationRuntimeMode.LIVE,
    )
    return connection


def _live_broker_factory():
    return _default_live_broker


def _live_order_connection(
    book: AccountBookRef,
    *,
    credential: str | None,
    custom_client: object | None = None,
    custom_client_resolver: object | None = None,
    integration_app: IntegrationConnectionApplication | None = None,
) -> object:
    if custom_client is not None or custom_client_resolver is not None:
        raise ValueError("custom live clients are not supported by the Binance Spot connection assembly")
    return connect_binance_spot_execution(
        f"live.binance.spot.{book.value}.execution.{credential or 'default'}",
        credential=credential,
        mode=IntegrationRuntimeMode.LIVE,
    )


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
) -> AccountDirectory:
    if not accounts:
        return AccountDirectory.from_contexts((AccountContext(AccountBookRef(fallback_broker, fallback.account_id, default_book), environment),))
    bindings: list[AccountBinding] = []
    for alias, config in accounts.items():
        account_config = account_configs.get(alias, fallback)
        broker = account_config.venue or fallback_broker
        books = config.books or default_account_books(broker, fallback=str(default_book))
        contexts = tuple(AccountContext(AccountBookRef(broker, account_config.account_id, book), environment) for book in books)
        bindings.append(AccountBinding(alias, config.index, contexts, ref=config.ref, trade=config.trade))
    return AccountDirectory(tuple(bindings))


def _capabilities(
    directory: AccountDirectory,
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


def _integration_product(book: AccountBookRef) -> ProductFamily:
    value = str(book.book)
    if value in {"swap", "perpetual", "perpetuals"}:
        return ProductFamily.USD_M_FUTURES
    if value == "usd_m_futures":
        return ProductFamily.USD_M_FUTURES
    if value == "options":
        return ProductFamily.OPTIONS
    if value == "earn":
        return ProductFamily.EARN
    if value == "coin_m_futures":
        return ProductFamily.COIN_M_FUTURES
    return ProductFamily.SPOT


def _canonical_broker_name(value: object) -> str:
    normalized = str(value).strip().lower()
    if normalized in {"okex", "ouyi"}:
        return "okx"
    if normalized in {"binance", "okx", "hyperliquid"}:
        return normalized
    raise LiveConfigurationError(f"CCXT integration does not support broker: {value}")


def configured_account_book_route(
    book: AccountBookRef,
    *,
    broker: object | None = None,
    base_params: Mapping[str, object] | None = None,
) -> AccountBookRoute:
    broker_name = broker or book.broker
    return account_book_route(book, broker=str(broker_name), base_params=base_params)


def _routes(
    directory: AccountDirectory,
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
    directory: AccountDirectory,
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
    directory: AccountDirectory,
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
    binding: AccountBinding,
    account_configs: Mapping[str, ConfiguredAccount] | None,
    fallback: ConfiguredAccount | None,
) -> bool:
    account = account_configs.get(binding.alias) if isinstance(account_configs, Mapping) else None
    if account is None:
        account = fallback
    if account is None:
        return True
    return account.has_trade_credential()


def _account_readers(
    clients: Mapping[AccountBookRef, object],
    application,
) -> Mapping[AccountBookRef, AccountConnection]:
    del application
    return {book: _AccountReader(connection) for book, connection in clients.items()}


def _account_private_streams(clients: Mapping[AccountBookRef, object], application):
    del application
    return {book: _AccountStreamAdapter(connection) for book, connection in clients.items()}


@dataclass(frozen=True, slots=True)
class _MappedLiveAccountGatewayResolver:
    account_readers: Mapping[AccountBookRef, object]
    private_streams: Mapping[AccountBookRef, object]

    def resolve_account_reader(self, account: AccountBookRef) -> object | None:
        return self.account_readers.get(account)

    def resolve_private_stream(self, account: AccountBookRef) -> object | None:
        return self.private_streams.get(account)

    def read_market_profile(self, request):
        reader = self.account_readers.get(request.context.book)
        if reader is None:
            raise RuntimeError(f"no account reader configured for account: {request.context.book.value}")
        return reader.read_market_profile(request)


@dataclass(frozen=True, slots=True)
class _MappedAccountLoginPort:
    readers: Mapping[AccountBookRef, object]
    streams: Mapping[AccountBookRef, object]

    def login(self, request: AccountLoginRequest) -> AccountLoginResult:
        reader = self.readers.get(request.context.book)
        if reader is None:
            raise RuntimeError(f"no account reader configured for account: {request.context.book.value}")
        observed_at = request.observed_at
        if observed_at is None:
            raise ValueError("account login requires an observed_at timestamp")
        snapshot = reader.read_account(
            AccountReadRequest(
                context=request.context,
                observed_at=observed_at,
                fetch_orders=True,
            )
        )
        stream = self.streams.get(request.context.book)
        connection_ids = request.connection_ids or tuple(
            value
            for value in (getattr(reader, "identity", None), getattr(stream, "identity", None))
            if value is not None
        )
        ids = tuple(getattr(value, "connection_id", str(value)) for value in connection_ids)
        return AccountLoginResult(
            AccountSession(
                session_id=f"account.{request.context.book.value}",
                account=request.context.book,
                connection_ids=ids,
                logged_in_at=observed_at,
            ),
            snapshot,
        )

    def logout(self, session: AccountSession) -> None:
        del session


@dataclass(frozen=True, slots=True)
class _AccountReader:
    """Composition adapter from account-usecase requests to Integration DTOs."""

    access: AccountConnection

    def read_account(self, request: AccountReadRequest):
        data = self.access.read_account(
            ConnectionAccountReadRequest(
                context=request.context,
                observed_at=request.observed_at,
                symbol=request.symbol,
                fetch_orders=request.fetch_orders,
            )
        )
        return data.snapshot

    def read_market_profile(self, request):
        data = self.access.read_market_profile(
            ConnectionAccountMarketProfileRequest(
                context=request.context,
                market=request.market,
                observed_at=request.observed_at,
            )
        )
        return data.profile


@dataclass(frozen=True, slots=True)
class _AccountStreamAdapter:
    connection: AccountStreamConnection

    def account_snapshots(self, context: AccountContext, *, open_orders: tuple[object, ...] = ()):
        return self.connection.account_snapshots(
            ConnectionAccountStreamRequest(
                context=context,
                open_orders=tuple(open_orders),
            )
        )


@dataclass(frozen=True, slots=True)
class _MappedExecutionUpdateSource:
    connections: Mapping[AccountBookRef, object]

    def events(self, account: AccountContext, *, symbol: str | None = None):
        connection = self.connections.get(account.book)
        if connection is None:
            raise RuntimeError(f"no execution connection configured for account: {account.book.value}")
        request = ConnectionAccountStreamRequest(
            context=account,
            symbol=symbol,
        )
        return connection.execution_updates(request)


def _client_resolver(clients: Mapping[AccountBookRef, object]):
    def resolve(account: AccountBookRef) -> object | None:
        return clients.get(account)

    return resolve


__all__ = ["BacktestAccountResources", "LiveAccountResources", "PaperAccountResources"]
