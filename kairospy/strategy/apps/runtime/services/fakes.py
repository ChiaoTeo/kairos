from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Mapping

from kairospy.investment.apps.market.application import SubscriptionRequest
from kairospy.investment.apps.account.application import AccountApplication
from kairospy.investment.apps.execution.application import ExecutionApplication
from kairospy.investment.apps.market.application import MarketApplication
from kairospy.investment.apps.reference.application import ReferenceApplication
from kairospy.investment.apps.risk.application import RiskApplication
from ..domain.lifecycle import StrategyLifecycle
from ..domain.messages import CommandHandle, LifecycleRecord
from .journal import StrategyLifecycleJournal


@dataclass(frozen=True, slots=True)
class RecordedApplicationRequest:
    operation: str
    payload: object
    strategy_id: str
    request_id: str
    instance_id: str


@dataclass(frozen=True, slots=True)
class _MarketSubscriptionResponse:
    subscription_id: str
    owner_id: str
    state: str
    satisfied_selectors: tuple[str, ...] = ()
    missing_selectors: tuple[str, ...] = ()
    resolved_providers: tuple[str, ...] = ()
    pending_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _MarketCommandStatus:
    status: str


@dataclass(frozen=True, slots=True)
class _MarketReleaseOwnerResponse:
    released_subscription_ids: tuple[str, ...] = ()


class InMemoryApplicationPorts:
    """Deterministic Market and Execution port fake for runtime tests."""

    def __init__(self) -> None:
        self.requests: list[RecordedApplicationRequest] = []
        self._handles: dict[str, object] = {}

    def _record(
        self,
        operation: str,
        payload: object,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        *,
        status: str = "accepted",
    ) -> CommandHandle:
        self.requests.append(
            RecordedApplicationRequest(
                operation, payload, strategy_id, request_id, instance_id
            )
        )
        handle = CommandHandle(request_id, status)
        self._handles[request_id] = handle
        return handle

    def subscribe(
        self,
        request: SubscriptionRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        launch_id: str | None = None,
    ) -> _MarketSubscriptionResponse:
        del launch_id
        self.requests.append(
            RecordedApplicationRequest(
                "market.subscribe", request, strategy_id, request_id, instance_id
            )
        )
        response = _MarketSubscriptionResponse(
            request_id, f"{strategy_id}:{instance_id}", "pending"
        )
        self._handles[request_id] = response
        return response

    def data_routes(self, query: Mapping[str, object] | None = None) -> dict[str, object]:
        del query
        return {"sources": []}

    def unsubscribe(
        self,
        subscription: object,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        launch_id: str | None = None,
    ) -> _MarketCommandStatus:
        del launch_id
        self.requests.append(
            RecordedApplicationRequest(
                "market.unsubscribe", subscription, strategy_id, request_id, instance_id
            )
        )
        response = _MarketCommandStatus("applied")
        self._handles[request_id] = response
        return response

    def target_position(self, request: object, **identity: str) -> CommandHandle:
        return self._record("intent.target_position", request, **identity)

    def cancel_intent(
        self, intent_id: str, *, reason: str, **identity: str
    ) -> CommandHandle:
        return self._record(
            "intent.cancel", {"intent_id": intent_id, "reason": reason}, **identity
        )

    def submit_order(self, request: object, **identity: str) -> CommandHandle:
        return self._record("execution.submit_order", request, **identity)

    def cancel_order(
        self, order_id: str, *, reason: str, **identity: str
    ) -> CommandHandle:
        return self._record(
            "execution.cancel_order",
            {"order_id": order_id, "reason": reason},
            **identity,
        )

    def replace_order(
        self, order_id: str, request: object, **identity: str
    ) -> CommandHandle:
        return self._record("execution.replace_order", (order_id, request), **identity)

    def cancel_all(
        self,
        *,
        instrument_id: str | None,
        account_id: str | None,
        reason: str,
        **identity: str,
    ) -> CommandHandle:
        return self._record(
            "execution.cancel_all",
            {
                "instrument_id": instrument_id,
                "account_id": account_id,
                "reason": reason,
            },
            **identity,
        )

    def status(self, request_id: str) -> object:
        return self._handles.get(
            request_id, CommandHandle(request_id, "missing", error="request not found")
        )

    def release_owner(
        self,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        launch_id: str | None = None,
    ) -> _MarketReleaseOwnerResponse:
        del launch_id
        self.requests.append(
            RecordedApplicationRequest(
                "market.release_owner", None, strategy_id, request_id, instance_id
            )
        )
        response = _MarketReleaseOwnerResponse()
        self._handles[request_id] = response
        return response

    def resolve(
        self,
        request_id: str,
        *,
        status: str = "ready",
        result: Mapping[str, object] | None = None,
        error: str | None = None,
    ) -> None:
        if request_id not in self._handles:
            raise KeyError(request_id)
        current = self._handles[request_id]
        if isinstance(current, _MarketSubscriptionResponse):
            raw_providers = (result or {}).get("resolved_providers", ())
            if not isinstance(raw_providers, (list, tuple)):
                raise TypeError("resolved_providers must be a sequence")
            providers = tuple(
                str(value) for value in raw_providers
            )
            self._handles[request_id] = replace(
                current, state=status, resolved_providers=providers
            )
        else:
            self._handles[request_id] = CommandHandle(
                request_id, status, result or {}, error
            )


class InMemoryMarketSnapshotReader:
    def __init__(self, snapshots: Mapping[str, object] | None = None) -> None:
        self.snapshots = dict(snapshots or {})

    def get(self, view_key: object) -> object | None:
        canonical_key = getattr(view_key, "canonical_key")
        return self.snapshots.get(canonical_key())

    def quote(self, market_id: str, provider: str) -> object | None:
        return self.snapshots.get(f"{market_id}:{provider}:quote")

    def bar(self, market_id: str, provider: str, qualifier: str) -> object | None:
        return self.snapshots.get(f"{market_id}:{provider}:bar:{qualifier}")

    def greeks(self, market_id: str, provider: str) -> object | None:
        return self.snapshots.get(f"{market_id}:{provider}:greeks")


class InMemoryMarketEventSource:
    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id
        self._events: deque[object] = deque()
        self._waiters: list[asyncio.Future[None]] = []

    def append(self, event: object) -> None:
        metadata = getattr(event, "metadata")
        if metadata.stream_id != self.stream_id:
            raise ValueError("event belongs to a different stream")
        self._events.append(event)
        for waiter in self._waiters:
            if not waiter.done():
                waiter.set_result(None)
        self._waiters.clear()

    async def replay_from(self, after_sequence: int = 0) -> AsyncIterator[object]:
        next_sequence = after_sequence + 1
        while True:
            while (
                self._events
                and getattr(self._events[0], "metadata").sequence < next_sequence
            ):
                self._events.popleft()
            if (
                self._events
                and getattr(self._events[0], "metadata").sequence == next_sequence
            ):
                event = self._events.popleft()
                next_sequence += 1
                yield event
                continue
            waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._waiters.append(waiter)
            await waiter


class InMemoryLifecycleJournal(StrategyLifecycleJournal):
    def __init__(self) -> None:
        self.records: list[LifecycleRecord] = []

    def append(self, record: LifecycleRecord) -> None:
        self.records.append(record)


def build_in_memory_strategy_applications(
    commands: InMemoryApplicationPorts,
    snapshots: InMemoryMarketSnapshotReader,
    event_source: InMemoryMarketEventSource,
    *,
    strategy_id: str,
    instance_id: str,
) -> tuple[
    ReferenceApplication,
    MarketApplication,
    AccountApplication,
    RiskApplication,
    ExecutionApplication,
]:
    """Build deterministic module Applications for Strategy runtime tests."""

    return (
        ReferenceApplication(),
        MarketApplication(
            commands,
            snapshots,
            event_source,
            strategy_id=strategy_id,
            instance_id=instance_id,
        ),
        AccountApplication({}),
        RiskApplication(None),
        ExecutionApplication(
            commands,
            None,
            strategy_id=strategy_id,
            instance_id=instance_id,
        ),
    )
