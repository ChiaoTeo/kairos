from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from kairospy.primitives.account import AccountId

from .models import RiskStatus
from .events import (
    ReservationChangedEvent,
    RiskCircuitChangedEvent,
    RiskDecisionEvent,
    RiskEvent,
)
from .mapping import map_risk_event, map_risk_status


class RiskApplication:
    """Concrete read-only Risk latest view scoped to one strategy launch."""

    def __init__(
        self,
        latest_view: Any | None,
        event_source: Any | None = None,
        *,
        account_ids: tuple[AccountId, ...] = (),
        strategy_id: str = "",
        launch_id: str | None = None,
        instance_id: str | None = None,
    ) -> None:
        self._latest_view = latest_view
        self._event_source = event_source
        self._account_ids = frozenset(account_ids)
        self._strategy_id = strategy_id
        self._launch_id = launch_id
        self._instance_id = instance_id
        self._event_cursor = 0
        self._event_source_ready = event_source is None

    def check_event_source_ready(self) -> None:
        """Validate the configured Risk event source without reading mmap."""

        if self._event_source_ready:
            return
        check_ready = getattr(self._event_source, "check_ready", None)
        if callable(check_ready):
            check_ready()
        self._event_source_ready = True

    async def events(self) -> AsyncIterator[RiskEvent]:
        if self._event_source is None:
            return
        cursor = self._event_cursor
        async for record in self._event_source.subscribe_live():
            typed = isinstance(
                record,
                (
                    ReservationChangedEvent,
                    RiskDecisionEvent,
                    RiskCircuitChangedEvent,
                ),
            )
            sequence = record.metadata.sequence if typed else record.sequence
            stream_id = record.metadata.stream_id if typed else record.stream_id
            if stream_id != "risk.events":
                raise RuntimeError(
                    f"Risk event stream identity is invalid: {stream_id}"
                )
            if not typed:
                if self._launch_id is not None and record.launch_id != self._launch_id:
                    raise RuntimeError("Risk event belongs to another launch")
                if (
                    self._instance_id is not None
                    and record.instance_id != self._instance_id
                ):
                    raise RuntimeError("Risk event belongs to another launch instance")
            if cursor == 0:
                cursor = sequence - 1
            if sequence <= cursor:
                continue
            expected = cursor + 1
            if sequence != expected:
                raise RuntimeError(
                    "Risk event stream is not contiguous: "
                    f"expected {expected}, received {sequence}"
                )
            cursor = sequence
            self._event_cursor = cursor
            event = record if typed else map_risk_event(record)
            if event is None:
                continue
            account_id = getattr(event.data, "account_id", None)
            strategy_id = getattr(event.data, "strategy_id", None)
            if (
                account_id is not None
                and self._account_ids
                and account_id not in self._account_ids
            ):
                continue
            if (
                strategy_id is not None
                and self._strategy_id
                and strategy_id != self._strategy_id
            ):
                continue
            yield event

    def status(self, *, account: AccountId | str) -> RiskStatus:
        if self._latest_view is None:
            raise RuntimeError("Risk latest view is unavailable")
        account_id = account if isinstance(account, AccountId) else AccountId(account)
        return map_risk_status(self._latest_view.status(account_id), account_id=account_id)
