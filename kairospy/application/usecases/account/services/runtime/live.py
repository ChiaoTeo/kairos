from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol
from types import MappingProxyType

from kairospy.application.usecases.account.application.directory import AccountDirectory
from kairospy.application.support.messaging import Message
from kairospy.application.actor.support.connections import ConnectionManager
from kairospy.application.usecases.account.services.service import AccountService
from kairospy.application.usecases.account.protocol import AccountLoginPort, AccountLoginResult, AccountSession
from kairospy.application.usecases.account.services.read import AccountReadService
from kairospy.application.usecases.account.domain.routing import AccountBookRoute, account_book_route
from kairospy.application.usecases.account.services.runtime.private_stream import (
    LivePrivateStreamCollector,
    LivePrivateStreamState,
)
from kairospy.domain.account import AccountBookRef, AccountCapability, AccountContext, AccountFeeSchedule, AccountLedger, AccountSnapshot, AccountSource, AccountState, Environment, derive_account_state


class LiveAccountGatewayResolver(Protocol):
    def resolve_account_reader(self, account: AccountBookRef) -> object | None:
        ...


@dataclass(frozen=True, slots=True)
class AccountIncident:
    kind: str
    message: str
    raw: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))

    def resolve_private_stream(self, account: AccountBookRef) -> object | None:
        ...


@dataclass(frozen=True, slots=True)
class _AccountReaderConnectionFactory:
    resolver: LiveAccountGatewayResolver
    account: AccountBookRef
    fallback: object | None

    def create_connection(self) -> object | None:
        return self.resolver.resolve_account_reader(self.account) or self.fallback


@dataclass(frozen=True, slots=True)
class _PrivateStreamConnectionFactory:
    resolver: LiveAccountGatewayResolver | None
    account: AccountBookRef
    fallback: object | None

    def create_connection(self) -> object | None:
        if self.resolver is None:
            return self.fallback
        return self.resolver.resolve_private_stream(self.account) or self.fallback


class LiveAccountService:
    def __init__(
        self,
        account: AccountContext,
        ledger: AccountLedger | None,
        *,
        broker: object | None = None,
        login_port: AccountLoginPort | None = None,
        gateway_resolver: LiveAccountGatewayResolver | None = None,
        parser: object | None = None,
        snapshot: AccountSnapshot | None = None,
        balance_params: Mapping[str, object] | None = None,
        open_order_symbols: tuple[str | None, ...] = (None,),
        open_order_params: Mapping[str, object] | None = None,
        stream: object | None = None,
        stream_symbol: str | None = None,
        max_balance_events: int = 0,
        max_order_events: int = 0,
        max_trade_events: int = 0,
        private_stream_state: LivePrivateStreamState | None = None,
        directory: AccountDirectory | None = None,
        capabilities: tuple[AccountCapability, ...] = (),
        fees: tuple[AccountFeeSchedule, ...] = (),
        routes: tuple[AccountBookRoute, ...] = (),
        market_profile_port: object | None = None,
        connections: ConnectionManager | None = None,
    ) -> None:
        if account.environment not in {Environment.LIVE, Environment.TESTNET}:
            raise ValueError("live account service requires a live or testnet account")
        self.account = account
        self.ledger = ledger
        self.broker = broker
        self.gateway_resolver = gateway_resolver
        self.parser = parser
        self.balance_params = dict(balance_params or {})
        self.open_order_symbols = open_order_symbols
        self.open_order_params = dict(open_order_params or {})
        self.stream = stream
        self.stream_symbol = stream_symbol
        self.max_balance_events = max_balance_events
        self.max_order_events = max_order_events
        self.max_trade_events = max_trade_events
        self.private_stream_state = private_stream_state or LivePrivateStreamState()
        self._directory = directory
        contexts = (account,) if directory is None else directory.contexts()
        self.accounts_service = AccountService(
            contexts,
            ledger=ledger,
            login_port=login_port,
            capabilities=capabilities,
            fees=fees,
            market_profile_port=market_profile_port,  # type: ignore[arg-type]
            provision_missing_capabilities=False,
        )
        self._routes = routes
        self.connections = connections
        self._sequence = 0
        if snapshot is not None:
            self.update_snapshot(snapshot)
        if self.connections is not None:
            if self.broker is not None:
                self.broker = self.connections.register(self._connection_key("broker", account.book), self.broker, role="account_broker")
            if self.stream is not None:
                self.stream = self.connections.register(self._connection_key("private_stream", account.book), self.stream, role="account_private_stream")

    def set_connection_manager(self, connections: ConnectionManager | None) -> None:
        self.connections = connections
        if self.connections is None:
            return
        if self.broker is not None:
            self.broker = self.connections.register(self._connection_key("broker", self.account.book), self.broker, role="account_broker")
        if self.stream is not None:
            self.stream = self.connections.register(self._connection_key("private_stream", self.account.book), self.stream, role="account_private_stream")

    async def events(self) -> AsyncIterator[Message]:
        if self.broker is None and self.gateway_resolver is None:
            if False:
                yield
            return
        snapshots = self.refresh_all()
        for snapshot in snapshots:
            yield self._account_event(snapshot.observed_at or datetime.now(timezone.utc), snapshot)
        if self.stream is None or not self.max_balance_events:
            if self.gateway_resolver is None:
                return
        for snapshot in snapshots:
            stream = self._stream_for(snapshot.context.book)
            if stream is None:
                continue
            route = self._route(snapshot.context.book)
            typed_stream = callable(getattr(stream, "account_snapshots", None))
            if self.parser is None and not typed_stream:
                raise RuntimeError("live account service requires an account payload parser")
            collector = LivePrivateStreamCollector(
                stream,
                snapshot.context,
                self._account_event,
                self._incident_event,
                self.private_stream_state,
            )
            try:
                collected = await collector.collect(
                    snapshot,
                    symbol=self.stream_symbol,
                    balance_params={**dict(route.balance_params), **self.balance_params},
                    max_balance_events=self.max_balance_events,
                )
            except Exception as error:
                key = self._connection_key("private_stream", snapshot.context.book)
                self._record_connection_error(key, error)
                self._reconnect(key)
                raise
            for event in collected:
                if event.domain == "account" and isinstance(event.payload, AccountSnapshot):
                    self.update_snapshot(event.payload)
                yield event

    def accounts(self) -> tuple[AccountContext, ...]:
        return self.accounts_service.accounts()

    def login(self, account: AccountBookRef | None = None, *, credential_ref: str | None = None, connection_ids: tuple[str, ...] = (), at: datetime | None = None) -> AccountLoginResult:
        return self.accounts_service.login(account, credential_ref=credential_ref, connection_ids=connection_ids, at=at)

    def logout(self, session: AccountSession) -> None:
        self.accounts_service.logout(session)

    def directory(self) -> AccountDirectory:
        return self._directory or AccountDirectory.from_contexts((self.account,))

    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]:
        return self.accounts_service.capabilities(account)

    def fees(self, account: AccountBookRef | None = None) -> tuple[AccountFeeSchedule, ...]:
        return self.accounts_service.fees(account)

    def market_profile(self, account: AccountBookRef, market: object, *, at: datetime | None = None, refresh: bool = False):
        return self.accounts_service.market_profile(account, market, at=at, refresh=refresh)  # type: ignore[arg-type]

    def update_market_profile(self, profile: object) -> None:
        self.accounts_service.update_market_profile(profile)  # type: ignore[arg-type]

    def market_profiles(self, account: AccountBookRef | None = None):
        return self.accounts_service.market_profiles(account)

    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None:
        context = self._context_for(account)
        if context is None:
            return None
        if context.book != self.account.book:
            return self.accounts_service.snapshot(context.book) or AccountSnapshot(context, balances=(), observed_at=None, source=AccountSource.VENUE)
        return self.accounts_service.snapshot(context.book)

    def state(self, account: AccountBookRef | None = None) -> AccountState | None:
        context = self._context_for(account)
        if context is None:
            return None
        if context.book != self.account.book:
            snapshot = self.accounts_service.snapshot(context.book)
            if snapshot is not None:
                return derive_account_state(context, ledger=self.ledger, venue=snapshot)
            return AccountState(context, (), (), (), (), None, AccountSource.VENUE)
        return derive_account_state(self.account, ledger=self.ledger, venue=self.accounts_service.snapshot(context.book))

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        if snapshot.context not in self.accounts():
            raise ValueError("live account snapshot context does not match service account directory")
        self.accounts_service.update_snapshot(snapshot)

    def read(
        self,
        account: AccountBookRef | None = None,
        *,
        symbol: str | None = None,
        at: datetime | None = None,
        balance_options: Mapping[str, object] | None = None,
        order_options: Mapping[str, object] | None = None,
        fetch_orders: bool = True,
    ):
        context = self._context_for(account)
        if context is None:
            raise RuntimeError("account is not configured")
        broker = self._broker_for(context.book)
        if broker is None:
            raise RuntimeError(f"live account service requires a broker integration for account: {context.book.value}")
        route = self._route(context.book)
        result = AccountReadService(broker).read(
            context,
            symbol=symbol,
            at=at,
            options={**dict(route.balance_params), **dict(route.order_params), **dict(balance_options or {}), **dict(order_options or {})},
            fetch_orders=fetch_orders,
        )
        self.update_snapshot(result.snapshot)
        return result

    def refresh(self, *, observed_at: datetime | None = None) -> AccountSnapshot:
        snapshots = self.refresh_all(observed_at=observed_at)
        if not snapshots:
            raise RuntimeError("live account refresh produced no snapshots")
        return snapshots[0]

    def refresh_all(self, *, observed_at: datetime | None = None) -> tuple[AccountSnapshot, ...]:
        if self.broker is None and self.gateway_resolver is None:
            raise RuntimeError("live account service requires a broker integration to refresh")
        at = observed_at or datetime.now(timezone.utc)
        snapshots: list[AccountSnapshot] = []
        for context in self.accounts():
            broker = self._broker_for(context.book)
            if broker is None:
                raise RuntimeError(f"live account service requires a broker integration for account: {context.book.value}")
            route = self._route(context.book)
            try:
                result = AccountReadService(broker).read(
                    context,
                    symbol=self.open_order_symbols[0] if len(self.open_order_symbols) == 1 and route.can_trade else None,
                    at=at,
                    options={**dict(route.balance_params), **dict(route.order_params), **self.balance_params, **self.open_order_params},
                    fetch_orders=route.can_trade,
                )
            except Exception as error:
                key = self._connection_key("broker", context.book)
                self._record_connection_error(key, error)
                self._reconnect(key)
                raise
            self.update_snapshot(result.snapshot)
            snapshots.append(result.snapshot)
        return tuple(snapshots)

    def _route(self, account: AccountBookRef) -> AccountBookRoute:
        for route in self._routes:
            if route.book == account:
                return route
        return account_book_route(account)

    def _broker_for(self, account: AccountBookRef) -> object | None:
        if self.gateway_resolver is None:
            return self.broker
        if self.connections is not None:
            return self.connections.resolve(
                self._connection_key("broker", account),
                role="account_broker",
                factory=_AccountReaderConnectionFactory(self.gateway_resolver, account, self.broker),
            )
        return self.gateway_resolver.resolve_account_reader(account) or self.broker

    def _stream_for(self, account: AccountBookRef) -> object | None:
        if self.connections is not None:
            return self.connections.resolve(
                self._connection_key("private_stream", account),
                role="account_private_stream",
                factory=_PrivateStreamConnectionFactory(self.gateway_resolver, account, self.stream),
            )
        if self.gateway_resolver is not None:
            return self.gateway_resolver.resolve_private_stream(account) or self.stream
        return self.stream

    def _connection_key(self, role: str, account: AccountBookRef) -> str:
        return f"live.{role}.{account.value}"

    def _record_connection_error(self, key: str, error: Exception) -> None:
        recorder = None if self.connections is None else getattr(self.connections, "record_error", None)
        if callable(recorder):
            recorder(key, error)

    def _reconnect(self, key: str) -> None:
        if self.connections is not None:
            self.connections.reconnect(key)

    def _account_event(self, time: datetime, snapshot: AccountSnapshot) -> Message:
        self._sequence += 1
        return Message(topic="account.snapshot", payload=snapshot, published_at=time, producer="account.service", producer_sequence=self._sequence)

    def _incident_event(self, kind: str, error: Exception, raw: Mapping[str, object], at: datetime | None) -> Message:
        self._sequence += 1
        return Message(topic=f"system.{kind}", payload=AccountIncident(kind, str(error), {"raw": dict(raw)}), published_at=at or datetime.now(timezone.utc), producer="account.service", producer_sequence=self._sequence)

    def _context_for(self, account: AccountBookRef | None) -> AccountContext | None:
        if account is None:
            return self.account
        if account == self.account.book:
            return self.account
        for context in self.accounts():
            if context.book == account:
                return context
        return None


__all__ = ["LiveAccountGatewayResolver", "LiveAccountService"]
