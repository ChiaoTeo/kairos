from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Mapping
from typing import Protocol

from kairospy.application.support.launch.accounts import LaunchAccountDirectory
from kairospy.application.support.runtime.events import RuntimeEnvelope, RuntimeIncident
from kairospy.application.support.runtime.connections import ConnectionManager
from kairospy.application.support.runtime.contracts import AccountCatalog, AccountRuntime, AccountRuntimeEnvelope
from kairospy.application.usecases.account.bootstrap import bootstrap_account
from kairospy.application.usecases.account.bootstrap import AccountBootstrapGateway, AccountBootstrapParser
from kairospy.application.usecases.account.routing import AccountBookRoute, account_book_route
from kairospy.application.usecases.account.live_stream import (
    LiveAccountStreamGateway,
    LivePrivateStreamCollector,
    LivePrivateStreamState,
    LivePrivateStreamPayloadAdapter,
)
from kairospy.core.account import AccountBookRef, AccountCapability, AccountContext, AccountFeeSchedule, AccountSnapshot, AccountSource, AccountState, Environment


class AccountBrokerResolver(Protocol):
    def __call__(self, account: AccountBookRef) -> AccountBootstrapGateway | None:
        ...


class AccountStreamResolver(Protocol):
    def __call__(self, account: AccountBookRef) -> LiveAccountStreamGateway | None:
        ...


class LiveAccountService(AccountRuntime, AccountCatalog):
    def __init__(
        self,
        account: AccountContext,
        coordinator: object,
        *,
        broker: AccountBootstrapGateway | None = None,
        broker_resolver: AccountBrokerResolver | None = None,
        parser: AccountBootstrapParser | LivePrivateStreamPayloadAdapter | None = None,
        snapshot: AccountSnapshot | None = None,
        balance_params: Mapping[str, object] | None = None,
        open_order_symbols: tuple[str | None, ...] = (None,),
        open_order_params: Mapping[str, object] | None = None,
        stream: LiveAccountStreamGateway | None = None,
        stream_resolver: AccountStreamResolver | None = None,
        stream_symbol: str | None = None,
        max_balance_events: int = 0,
        max_order_events: int = 0,
        max_trade_events: int = 0,
        private_stream_state: LivePrivateStreamState | None = None,
        directory: LaunchAccountDirectory | None = None,
        capabilities: tuple[AccountCapability, ...] = (),
        fees: tuple[AccountFeeSchedule, ...] = (),
        routes: tuple[AccountBookRoute, ...] = (),
        connections: ConnectionManager | None = None,
    ) -> None:
        if account.environment not in {Environment.LIVE, Environment.TESTNET}:
            raise ValueError("live account service requires a live or testnet account")
        self.account = account
        self.coordinator = coordinator
        self.broker = broker
        self.broker_resolver = broker_resolver
        self.parser = parser
        self.balance_params = dict(balance_params or {})
        self.open_order_symbols = open_order_symbols
        self.open_order_params = dict(open_order_params or {})
        self.stream = stream
        self.stream_resolver = stream_resolver
        self.stream_symbol = stream_symbol
        self.max_balance_events = max_balance_events
        self.max_order_events = max_order_events
        self.max_trade_events = max_trade_events
        self.private_stream_state = private_stream_state or LivePrivateStreamState()
        self._directory = directory
        self._capabilities = capabilities
        self._fees = fees
        self._routes = routes
        self.connections = connections
        self._sequence = 0
        self._snapshots: dict[AccountBookRef, AccountSnapshot] = {}
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

    async def events(self) -> AsyncIterator[AccountRuntimeEnvelope]:
        if self.broker is None and self.broker_resolver is None:
            if False:
                yield
            return
        snapshots = self.refresh_all()
        for snapshot in snapshots:
            yield self._account_event(snapshot.observed_at or datetime.now(timezone.utc), snapshot)
        if self.stream is None or not (self.max_balance_events or self.max_order_events or self.max_trade_events):
            if self.stream_resolver is None:
                return
        for snapshot in snapshots:
            stream = self._stream_for(snapshot.context.book)
            if stream is None:
                continue
            route = self._route(snapshot.context.book)
            if self.parser is None:
                raise RuntimeError("live account service requires an account payload parser")
            collector = LivePrivateStreamCollector(
                stream,
                snapshot.context,
                self.coordinator,
                self.parser,
                self._account_event,
                self._incident_event,
                self.private_stream_state,
            )
            try:
                collected = await collector.collect(
                    snapshot,
                    symbol=self.stream_symbol,
                    balance_params={**dict(route.balance_params), **self.balance_params},
                    order_params={**dict(route.order_params), **self.open_order_params},
                    max_balance_events=self.max_balance_events,
                    max_order_events=self.max_order_events,
                    max_trade_events=self.max_trade_events,
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
        return (self.account,) if self._directory is None else self._directory.contexts()

    def directory(self) -> LaunchAccountDirectory:
        return self._directory or LaunchAccountDirectory.from_contexts((self.account,))

    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]:
        if account is None:
            return self._capabilities
        return tuple(item for item in self._capabilities if item.book == account)

    def fees(self, account: AccountBookRef | None = None) -> tuple[AccountFeeSchedule, ...]:
        if account is None:
            return self._fees
        return tuple(item for item in self._fees if item.book == account)

    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None:
        context = self._context_for(account)
        if context is None:
            return None
        if context.book != self.account.book:
            return self._snapshots.get(context.book) or AccountSnapshot(context, balances=(), observed_at=None, source=AccountSource.VENUE)
        return self._snapshots.get(context.book)

    def state(self, account: AccountBookRef | None = None) -> AccountState | None:
        context = self._context_for(account)
        if context is None:
            return None
        if context.book != self.account.book:
            snapshot = self._snapshots.get(context.book)
            if snapshot is not None:
                return self.coordinator.account_projection(context, venue_snapshot=snapshot)
            return AccountState(context, (), (), (), (), None, AccountSource.VENUE)
        return self.coordinator.account_projection(self.account, venue_snapshot=self._snapshots.get(context.book))

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        if snapshot.context not in self.accounts():
            raise ValueError("live account snapshot context does not match service account directory")
        self._snapshots[snapshot.context.book] = snapshot

    def refresh(self, *, observed_at: datetime | None = None) -> AccountSnapshot:
        snapshots = self.refresh_all(observed_at=observed_at)
        if not snapshots:
            raise RuntimeError("live account refresh produced no snapshots")
        return snapshots[0]

    def refresh_all(self, *, observed_at: datetime | None = None) -> tuple[AccountSnapshot, ...]:
        if self.broker is None and self.broker_resolver is None:
            raise RuntimeError("live account service requires a broker integration to refresh")
        if self.parser is None:
            raise RuntimeError("live account service requires an account payload parser")
        at = observed_at or datetime.now(timezone.utc)
        snapshots: list[AccountSnapshot] = []
        for context in self.accounts():
            broker = self._broker_for(context.book)
            if broker is None:
                raise RuntimeError(f"live account service requires a broker integration for account: {context.book.value}")
            route = self._route(context.book)
            try:
                result = bootstrap_account(
                    context,
                    broker,
                    self.coordinator,
                    self.parser,
                    symbol=self.open_order_symbols[0] if len(self.open_order_symbols) == 1 and route.can_trade else None,
                    at=at,
                    balance_params={**dict(route.balance_params), **self.balance_params},
                    order_params={**dict(route.order_params), **self.open_order_params},
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

    def _broker_for(self, account: AccountBookRef) -> AccountBootstrapGateway | None:
        if self.broker_resolver is None:
            return self.broker
        if self.connections is not None:
            return self.connections.resolve(
                self._connection_key("broker", account),
                role="account_broker",
                factory=lambda: self.broker_resolver(account) or self.broker,
            )
        return self.broker_resolver(account) or self.broker

    def _stream_for(self, account: AccountBookRef) -> LiveAccountStreamGateway | None:
        if self.connections is not None:
            return self.connections.resolve(
                self._connection_key("private_stream", account),
                role="account_private_stream",
                factory=lambda: (self.stream_resolver(account) if self.stream_resolver is not None else None) or self.stream,
            )
        if self.stream_resolver is not None:
            return self.stream_resolver(account) or self.stream
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

    def _account_event(self, time: datetime, snapshot: AccountSnapshot) -> AccountRuntimeEnvelope:
        self._sequence += 1
        return RuntimeEnvelope("account", "snapshot", time, self._sequence, snapshot)

    def _incident_event(self, kind: str, error: Exception, raw: Mapping[str, object], at: datetime | None) -> RuntimeEnvelope[RuntimeIncident]:
        self._sequence += 1
        return RuntimeEnvelope(
            "system",
            kind,
            at or datetime.now(timezone.utc),
            self._sequence,
            RuntimeIncident(kind, str(error), {"raw": dict(raw)}),
        )

    def _context_for(self, account: AccountBookRef | None) -> AccountContext | None:
        if account is None:
            return self.account
        if account == self.account.book:
            return self.account
        for context in self.accounts():
            if context.book == account:
                return context
        return None


__all__ = ["LiveAccountService"]
