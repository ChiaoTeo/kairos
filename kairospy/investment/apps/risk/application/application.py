from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast

from kairospy.infrastructure.contracts.risk.events import RiskEvent as NativeRiskEvent
from kairospy.primitives.account import AccountId

from .models import RiskStatus
from .mapping import map_risk_status

if TYPE_CHECKING:
    from kairospy.strategy.api.risk import RiskEvent


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
        self._event_cursor_key: tuple[str, str, int] | None = None
        self._notification_gap_count = 0
        self._notification_incarnation_change_count = 0
        self._event_source_ready = event_source is None

    def check_event_source_ready(self) -> None:
        """Validate the configured Risk event source without reading the indexed view."""

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
            if not isinstance(record, NativeRiskEvent):
                raise TypeError("Risk event source must yield owner-native RiskEvent values")
            if record.stream_id != "risk.events":
                raise RuntimeError(
                    f"Risk event stream identity is invalid: {record.stream_id}"
                )
            if self._launch_id is not None and record.launch_id != self._launch_id:
                raise RuntimeError("Risk event belongs to another launch")
            if self._instance_id is not None and record.instance_id != self._instance_id:
                raise RuntimeError("Risk event belongs to another launch instance")
            cursor_key = (
                record.stream_id,
                str(record.producer),
                int(record.producer_incarnation),
            )
            if self._event_cursor_key is not None and cursor_key != self._event_cursor_key:
                self._notification_incarnation_change_count += 1
                cursor = record.sequence - 1
            elif self._event_cursor_key is None:
                cursor = record.sequence - 1
            self._event_cursor_key = cursor_key
            if cursor == 0:
                cursor = record.sequence - 1
            if record.sequence <= cursor:
                continue
            if record.sequence != cursor + 1:
                self._notification_gap_count += 1
            cursor = record.sequence
            self._event_cursor = cursor
            if record.kind == "policy_activated":
                continue
            if (
                record.account_id is not None
                and self._account_ids
                and AccountId(record.account_id) not in self._account_ids
            ):
                continue
            if (
                record.strategy_id is not None
                and self._strategy_id
                and record.strategy_id != self._strategy_id
            ):
                continue
            yield cast("RiskEvent", record)

    def notification_health(self) -> dict[str, object]:
        """Return diagnostics for best-effort Risk notifications."""

        return {
            "cursor": self._event_cursor,
            "producer": None
            if self._event_cursor_key is None
            else self._event_cursor_key[1],
            "producer_incarnation": None
            if self._event_cursor_key is None
            else self._event_cursor_key[2],
            "gap_count": self._notification_gap_count,
            "incarnation_change_count": self._notification_incarnation_change_count,
        }

    def status(self, *, account: AccountId | str) -> RiskStatus:
        if self._latest_view is None:
            raise RuntimeError("Risk latest view is unavailable")
        account_id = account if isinstance(account, AccountId) else AccountId(account)
        snapshot = self._latest_view.snapshot()
        return map_risk_status(snapshot, account_id=account_id)
