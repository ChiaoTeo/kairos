from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.runtime.services import AccountService
from kairospy.application.service.domain.account import (
    LiveAccountStreamGateway,
    LivePrivateStreamState,
    LivePrivateStreamCollector,
    account_snapshot_envelope,
    bootstrap_account,
)
from kairospy.core.account import AccountContext, AccountRef, AccountSnapshot, AccountSource, AccountState, Environment
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.views import ViewFieldSchema, ViewSchema
from kairospy.infrastructure.integrations.payloads.ccxt_account import CcxtAccountPayloadAdapter
from kairospy.infrastructure.integrations.protocols import BrokerClient


@dataclass(frozen=True, slots=True)
class LiveAccountServiceView:
    account: AccountContext
    has_snapshot: bool
    source: AccountSource | str | None = None


class LiveAccountService(AccountService):
    key = "account.service"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("account", "live account context", "runtime state", "live account service"),
            ViewFieldSchema("has_snapshot", "whether a venue snapshot is available", "runtime state", "live account service"),
            ViewFieldSchema("source", "latest account source", "event time", "live account snapshot"),
        ),
        mutability="runtime_writable",
        evidence="runtime live account service",
    )

    def __init__(
        self,
        account: AccountContext,
        coordinator: ExecutionCoordinator,
        *,
        broker: BrokerClient | None = None,
        parser: CcxtAccountPayloadAdapter | None = None,
        snapshot: AccountSnapshot | None = None,
        balance_params: Mapping[str, object] | None = None,
        open_order_symbols: tuple[str | None, ...] = (None,),
        open_order_params: Mapping[str, object] | None = None,
        stream: LiveAccountStreamGateway | None = None,
        stream_symbol: str | None = None,
        max_balance_events: int = 0,
        max_order_events: int = 0,
        max_trade_events: int = 0,
        private_stream_state: LivePrivateStreamState | None = None,
    ) -> None:
        if account.environment not in {Environment.LIVE, Environment.TESTNET}:
            raise ValueError("live account service requires a live or testnet account")
        self.account = account
        self.coordinator = coordinator
        self.broker = broker
        self.parser = parser or CcxtAccountPayloadAdapter()
        self.balance_params = dict(balance_params or {})
        self.open_order_symbols = open_order_symbols
        self.open_order_params = dict(open_order_params or {})
        self.stream = stream
        self.stream_symbol = stream_symbol
        self.max_balance_events = max_balance_events
        self.max_order_events = max_order_events
        self.max_trade_events = max_trade_events
        self.private_stream_state = private_stream_state or LivePrivateStreamState()
        self._sequence = 0
        self._snapshot: AccountSnapshot | None = None
        if snapshot is not None:
            self.update_snapshot(snapshot)

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if self.broker is None:
            if False:
                yield
            return
        snapshot = self.refresh()
        yield self._account_event(snapshot.observed_at or datetime.now(timezone.utc), snapshot)
        if self.stream is None or not (self.max_balance_events or self.max_order_events or self.max_trade_events):
            return
        collector = LivePrivateStreamCollector(
            self.stream,
            self.account,
            self.coordinator,
            self.parser,
            self._account_event,
            self._incident_event,
            self.private_stream_state,
        )
        for event in await collector.collect(
            snapshot,
            symbol=self.stream_symbol,
            balance_params=self.balance_params,
            order_params=self.open_order_params,
            max_balance_events=self.max_balance_events,
            max_order_events=self.max_order_events,
            max_trade_events=self.max_trade_events,
        ):
            if event.domain == "account" and isinstance(event.payload, AccountSnapshot):
                self.update_snapshot(event.payload)
            yield event

    def accounts(self) -> tuple[AccountContext, ...]:
        return (self.account,)

    def snapshot(self, account: AccountRef | None = None) -> AccountSnapshot | None:
        if account is not None and account != self.account.account:
            return None
        return self._snapshot

    def state(self, account: AccountRef | None = None) -> AccountState | None:
        if account is not None and account != self.account.account:
            return None
        return self.coordinator.account_projection(self.account, venue_snapshot=self._snapshot)

    def on_event(self, event: RuntimeEnvelope) -> None:
        payload = event.payload
        if event.domain != "account" or not isinstance(payload, AccountSnapshot):
            return
        if payload.context == self.account:
            self.update_snapshot(payload)

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        if snapshot.context != self.account:
            raise ValueError("live account snapshot context does not match service account")
        self._snapshot = snapshot

    def refresh(self, *, observed_at: datetime | None = None) -> AccountSnapshot:
        if self.broker is None:
            raise RuntimeError("live account service requires a broker integration to refresh")
        at = observed_at or datetime.now(timezone.utc)
        result = bootstrap_account(
            self.account,
            self.broker,
            self.coordinator,
            self.parser,
            symbol=self.open_order_symbols[0] if len(self.open_order_symbols) == 1 else None,
            at=at,
            balance_params=self.balance_params,
            order_params=self.open_order_params,
        )
        self.update_snapshot(result.snapshot)
        return result.snapshot

    def view(self) -> LiveAccountServiceView:
        snapshot = self._snapshot
        return LiveAccountServiceView(self.account, snapshot is not None, None if snapshot is None else snapshot.source)

    def _account_event(self, time: datetime, snapshot: AccountSnapshot) -> RuntimeEnvelope:
        self._sequence += 1
        return account_snapshot_envelope(time, snapshot, sequence=self._sequence)

    def _incident_event(self, kind: str, error: Exception, raw: Mapping[str, object], at: datetime | None) -> RuntimeEnvelope:
        self._sequence += 1
        return RuntimeEnvelope(
            "system",
            kind,
            at or datetime.now(timezone.utc),
            self._sequence,
            {"error": str(error), "raw": dict(raw)},
        )


__all__ = ["LiveAccountService", "LiveAccountServiceView"]
