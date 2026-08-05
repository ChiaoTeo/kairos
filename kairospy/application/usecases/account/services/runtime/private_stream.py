"""ExternalAccount-only private stream collection.

Execution updates from a shared user stream belong to the execution usecase;
this collector deliberately consumes only account snapshots.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

from kairospy.application.usecases.account.domain.private_stream import PrivateStreamCheckpoint
from kairospy.domain.account import AccountRuntimeContext, AccountSnapshot


@dataclass(slots=True)
class LivePrivateStreamState:
    _seen_balance_events: set[str] = field(default_factory=set)

    def checkpoint(self) -> PrivateStreamCheckpoint:
        return PrivateStreamCheckpoint(
            seen_order_updates=(),
            seen_trade_updates=tuple(sorted(self._seen_balance_events)),
            order_timestamps={},
        )

    def restore_checkpoint(self, checkpoint: PrivateStreamCheckpoint) -> None:
        self._seen_balance_events = set(checkpoint.seen_trade_updates)

    def snapshot(self) -> dict[str, object]:
        return self.checkpoint().to_dict()

    @classmethod
    def from_snapshot(cls, value: Mapping[str, object]) -> "LivePrivateStreamState":
        state = cls()
        state.restore_checkpoint(PrivateStreamCheckpoint.from_dict(value))
        return state

    def accept_balance(self, snapshot: AccountSnapshot) -> bool:
        key = f"{snapshot.context.segment.value}:{snapshot.observed_at.isoformat()}"
        if key in self._seen_balance_events:
            return False
        self._seen_balance_events.add(key)
        return True


@dataclass(slots=True)
class AccountPrivateStreamCollector:
    gateway: object
    account: AccountRuntimeContext
    account_event: object
    incident_event: object
    state: LivePrivateStreamState = field(default_factory=LivePrivateStreamState)

    async def collect(
        self,
        snapshot: AccountSnapshot,
        *,
        symbol: str | None = None,
        balance_params: Mapping[str, object] | None = None,
        max_balance_events: int = 0,
    ) -> tuple[object, ...]:
        del symbol
        events: list[object] = []
        if max_balance_events <= 0:
            return ()
        try:
            if callable(getattr(self.gateway, "account_snapshots", None)):
                stream = self.gateway.account_snapshots(self.account, open_orders=snapshot.open_orders)
            else:
                stream = self.gateway.watch_balance(params=balance_params)
            async for current in _take(stream, max_balance_events):
                if isinstance(current, AccountSnapshot):
                    if not self.state.accept_balance(current):
                        continue
                    events.append(self.account_event(current.observed_at or datetime.now(timezone.utc), current))
                    snapshot = current
                else:
                    events.append(self.account_event(datetime.now(timezone.utc), snapshot))
        except Exception as error:
            events.append(self.incident_event("live.account.balance.error", error, None, datetime.now(timezone.utc)))
        return tuple(events)


async def _take(events: AsyncIterator[object], limit: int) -> AsyncIterator[object]:
    count = 0
    async for event in events:
        yield event
        count += 1
        if count >= limit:
            break


# Compatibility name for runtime assembly; it now has account-only semantics.
LivePrivateStreamCollector = AccountPrivateStreamCollector


__all__ = ["AccountPrivateStreamCollector", "LivePrivateStreamCollector", "LivePrivateStreamState"]
