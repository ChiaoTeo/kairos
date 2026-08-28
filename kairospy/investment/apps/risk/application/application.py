from __future__ import annotations

from collections.abc import Callable

from kairospy.contracts.risk.events import RiskEventVariant
from kairospy.contracts.risk.types import RiskCurrentView
from kairospy.infrastructure.protocol import LiveEventSource
from kairospy.primitives.account import AccountId
from kairospy.primitives.runtime import InstanceIdRead, LaunchIdRead, StrategyIdRead

from .models import RiskStatus
from .mapping import map_risk_status

class RiskApplication:
    """Concrete read-only Risk latest view scoped to one strategy launch."""

    def __init__(
        self,
        latest_view: RiskCurrentView | None,
        event_source: LiveEventSource[RiskEventVariant] | None = None,
        *,
        account_ids: tuple[AccountId, ...] = (),
        strategy_id: str = "",
        launch_id: str | None = None,
        instance_id: str | None = None,
    ) -> None:
        self._latest_view = latest_view
        self._event_source = event_source
        self._account_ids = frozenset(account_ids)
        self._strategy_id = StrategyIdRead(strategy_id) if strategy_id else None
        self._launch_id = LaunchIdRead(launch_id) if launch_id is not None else None
        self._instance_id = (
            InstanceIdRead(instance_id) if instance_id is not None else None
        )
        self._event_cursor = 0
        self._event_cursor_key: tuple[str, str, int] | None = None
        self._notification_gap_count = 0
        self._notification_incarnation_change_count = 0
        self._event_source_ready = event_source is None

    def check_event_source_ready(self) -> None:
        """Validate the configured Risk event source without reading the indexed view."""

        if self._event_source_ready:
            return
        self._event_source_ready = True

    def visit_live(
        self,
        visitor: Callable[[RiskEventVariant], None],
        *,
        fragment_limit: int = 64,
    ) -> int:
        if self._event_source is None:
            return 0
        cursor = self._event_cursor

        def accept(record: RiskEventVariant) -> None:
            nonlocal cursor
            metadata = record.metadata
            if metadata.stream_id != "risk.events":
                raise RuntimeError(
                    f"Risk event stream identity is invalid: {metadata.stream_id}"
                )
            if self._launch_id is not None and metadata.launch_id != self._launch_id:
                raise RuntimeError("Risk event belongs to another launch")
            if self._instance_id is not None and metadata.instance_id != self._instance_id:
                raise RuntimeError("Risk event belongs to another launch instance")
            cursor_key = (
                metadata.stream_id,
                str(metadata.producer),
                int(metadata.producer_incarnation),
            )
            if self._event_cursor_key is not None and cursor_key != self._event_cursor_key:
                self._notification_incarnation_change_count += 1
                cursor = int(metadata.sequence) - 1
            elif self._event_cursor_key is None:
                cursor = int(metadata.sequence) - 1
            self._event_cursor_key = cursor_key
            sequence = int(metadata.sequence)
            if cursor == 0:
                cursor = sequence - 1
            if sequence <= cursor:
                return
            if sequence != cursor + 1:
                self._notification_gap_count += 1
            cursor = sequence
            self._event_cursor = cursor
            if (
                record.account_id is not None
                and self._account_ids
                and AccountId(record.account_id) not in self._account_ids
            ):
                return
            if (
                record.strategy_id is not None
                and self._strategy_id
                and record.strategy_id != self._strategy_id
            ):
                return
            visitor(record)

        return self._event_source.poll_visit(accept, fragment_limit=fragment_limit)

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

    def close_live(self) -> None:
        if self._event_source is not None:
            self._event_source.close()

    def status(self, *, account: AccountId | str) -> RiskStatus:
        if self._latest_view is None:
            raise RuntimeError("Risk latest view is unavailable")
        account_id = account if isinstance(account, AccountId) else AccountId(account)
        snapshot = self._latest_view.snapshot()
        return map_risk_status(snapshot, account_id=account_id)
