from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Callable
from typing import Mapping

from kairospy.application.launch import LaunchAccountDirectory
from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.ports import AccountPort
from kairospy.application.service.domain.account.bootstrap import bootstrap_account
from kairospy.application.service.domain.account.routing import AccountBookRoute, account_book_route
from kairospy.application.service.domain.account.live_stream import (
    LiveAccountStreamGateway,
    LivePrivateStreamCollector,
    LivePrivateStreamState,
)
from kairospy.core.account import AccountCapability, AccountContext, AccountFeeSchedule, AccountRef, AccountSnapshot, AccountSource, AccountState, Environment
from kairospy.core.execution import ExecutionCoordinator
from kairospy.infrastructure.integrations.payloads.ccxt_account import CcxtAccountPayloadAdapter
from kairospy.infrastructure.integrations.protocols import BrokerClient


class LiveAccountService(AccountPort):
    def __init__(
        self,
        account: AccountContext,
        coordinator: ExecutionCoordinator,
        *,
        broker: BrokerClient | None = None,
        broker_resolver: Callable[[AccountRef], BrokerClient | None] | None = None,
        parser: CcxtAccountPayloadAdapter | None = None,
        snapshot: AccountSnapshot | None = None,
        balance_params: Mapping[str, object] | None = None,
        open_order_symbols: tuple[str | None, ...] = (None,),
        open_order_params: Mapping[str, object] | None = None,
        stream: LiveAccountStreamGateway | None = None,
        stream_resolver: Callable[[AccountRef], LiveAccountStreamGateway | None] | None = None,
        stream_symbol: str | None = None,
        max_balance_events: int = 0,
        max_order_events: int = 0,
        max_trade_events: int = 0,
        private_stream_state: LivePrivateStreamState | None = None,
        directory: LaunchAccountDirectory | None = None,
        capabilities: tuple[AccountCapability, ...] = (),
        fees: tuple[AccountFeeSchedule, ...] = (),
        routes: tuple[AccountBookRoute, ...] = (),
    ) -> None:
        if account.environment not in {Environment.LIVE, Environment.TESTNET}:
            raise ValueError("live account service requires a live or testnet account")
        self.account = account
        self.coordinator = coordinator
        self.broker = broker
        self.broker_resolver = broker_resolver
        self.parser = parser or CcxtAccountPayloadAdapter()
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
        self._sequence = 0
        self._snapshots: dict[AccountRef, AccountSnapshot] = {}
        if snapshot is not None:
            self.update_snapshot(snapshot)

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
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
            stream = self._stream_for(snapshot.context.account)
            if stream is None:
                continue
            route = self._route(snapshot.context.account)
            collector = LivePrivateStreamCollector(
                stream,
                snapshot.context,
                self.coordinator,
                self.parser,
                self._account_event,
                self._incident_event,
                self.private_stream_state,
            )
            for event in await collector.collect(
                snapshot,
                symbol=self.stream_symbol,
                balance_params={**dict(route.balance_params), **self.balance_params},
                order_params={**dict(route.order_params), **self.open_order_params},
                max_balance_events=self.max_balance_events,
                max_order_events=self.max_order_events,
                max_trade_events=self.max_trade_events,
            ):
                if event.domain == "account" and isinstance(event.payload, AccountSnapshot):
                    self.update_snapshot(event.payload)
                yield event

    def accounts(self) -> tuple[AccountContext, ...]:
        return (self.account,) if self._directory is None else self._directory.contexts()

    def directory(self) -> LaunchAccountDirectory:
        return self._directory or LaunchAccountDirectory.from_contexts((self.account,))

    def capabilities(self, account: AccountRef | None = None) -> tuple[AccountCapability, ...]:
        if account is None:
            return self._capabilities
        return tuple(item for item in self._capabilities if item.book == account)

    def fees(self, account: AccountRef | None = None) -> tuple[AccountFeeSchedule, ...]:
        if account is None:
            return self._fees
        return tuple(item for item in self._fees if item.book == account)

    def snapshot(self, account: AccountRef | None = None) -> AccountSnapshot | None:
        context = self._context_for(account)
        if context is None:
            return None
        if context.account != self.account.account:
            return self._snapshots.get(context.account) or AccountSnapshot(context, balances=(), observed_at=None, source=AccountSource.VENUE)
        return self._snapshots.get(context.account)

    def state(self, account: AccountRef | None = None) -> AccountState | None:
        context = self._context_for(account)
        if context is None:
            return None
        if context.account != self.account.account:
            snapshot = self._snapshots.get(context.account)
            if snapshot is not None:
                return self.coordinator.account_projection(context, venue_snapshot=snapshot)
            return AccountState(context, (), (), (), (), None, AccountSource.VENUE)
        return self.coordinator.account_projection(self.account, venue_snapshot=self._snapshots.get(context.account))

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        if snapshot.context not in self.accounts():
            raise ValueError("live account snapshot context does not match service account directory")
        self._snapshots[snapshot.context.account] = snapshot

    def refresh(self, *, observed_at: datetime | None = None) -> AccountSnapshot:
        snapshots = self.refresh_all(observed_at=observed_at)
        if not snapshots:
            raise RuntimeError("live account refresh produced no snapshots")
        return snapshots[0]

    def refresh_all(self, *, observed_at: datetime | None = None) -> tuple[AccountSnapshot, ...]:
        if self.broker is None and self.broker_resolver is None:
            raise RuntimeError("live account service requires a broker integration to refresh")
        at = observed_at or datetime.now(timezone.utc)
        snapshots: list[AccountSnapshot] = []
        for context in self.accounts():
            broker = self._broker_for(context.account)
            if broker is None:
                raise RuntimeError(f"live account service requires a broker integration for account: {context.account.value}")
            route = self._route(context.account)
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
            self.update_snapshot(result.snapshot)
            snapshots.append(result.snapshot)
        return tuple(snapshots)

    def _route(self, account: AccountRef) -> AccountBookRoute:
        for route in self._routes:
            if route.book == account:
                return route
        return account_book_route(account)

    def _broker_for(self, account: AccountRef) -> BrokerClient | None:
        if self.broker_resolver is None:
            return self.broker
        return self.broker_resolver(account) or self.broker

    def _stream_for(self, account: AccountRef) -> LiveAccountStreamGateway | None:
        if self.stream_resolver is not None:
            return self.stream_resolver(account) or self.stream
        return self.stream

    def _account_event(self, time: datetime, snapshot: AccountSnapshot) -> RuntimeEnvelope:
        self._sequence += 1
        return RuntimeEnvelope("account", "snapshot", time, self._sequence, snapshot)

    def _incident_event(self, kind: str, error: Exception, raw: Mapping[str, object], at: datetime | None) -> RuntimeEnvelope:
        self._sequence += 1
        return RuntimeEnvelope(
            "system",
            kind,
            at or datetime.now(timezone.utc),
            self._sequence,
            {"error": str(error), "raw": dict(raw)},
        )

    def _context_for(self, account: AccountRef | None) -> AccountContext | None:
        if account is None:
            return self.account
        if account == self.account.account:
            return self.account
        for context in self.accounts():
            if context.account == account:
                return context
        return None


__all__ = ["LiveAccountService"]
