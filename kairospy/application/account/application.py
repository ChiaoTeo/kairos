from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from kairospy.primitives.account import AccountId

from .errors import AccountNotEnabledError
from .events import AccountEvent
from .mapping import map_account_event
from .models import (
    AccountSnapshot,
    AccountsSnapshot,
    DataFreshness,
    SegmentSyncLifecycle,
)


class AccountApplication:
    """Typed read-only Account access scoped to one Strategy launch.

    Each configured AccountId owns a distinct current_view reader and mmap. A
    reader returns every segment for that logical account in one generation.
    """

    def __init__(
        self,
        current_views: Mapping[AccountId, Any],
        event_source: Any | None = None,
        *,
        launch_id: str | None = None,
        instance_id: str | None = None,
        required_segments: Mapping[AccountId, tuple[str, ...]] | None = None,
    ) -> None:
        self._current_views = dict(current_views)
        self._event_source = event_source
        self._launch_id = launch_id
        self._instance_id = instance_id
        self._required_segments = dict(required_segments or {})
        self._event_cursors: dict[str, int] = {}
        self._event_source_ready = event_source is None

    @property
    def account_ids(self) -> tuple[AccountId, ...]:
        """Account identities in launch configuration order."""

        return tuple(self._current_views)

    @property
    def accounts(self) -> tuple[AccountSnapshot, ...]:
        """Read each enabled Account mmap once and return immutable snapshots."""

        return self.snapshot().accounts

    def snapshot(self) -> AccountsSnapshot:
        """Read one snapshot per enabled Account.

        Every AccountSnapshot is internally generation-consistent across its
        segments. Separate accounts have independent Actors and generations.
        """

        return AccountsSnapshot(
            tuple(
                current_view.snapshot(account_id)
                for account_id, current_view in self._current_views.items()
            )
        )

    def account(self, account: AccountId | str) -> AccountSnapshot:
        """Read one logical Account and all of its segments from its mmap."""

        account_id = _account_id(account)
        try:
            current_view = self._current_views[account_id]
        except KeyError as error:
            raise AccountNotEnabledError(account_id) from error
        return current_view.snapshot(account_id)

    def _check_event_source_ready(self) -> None:
        if not self._event_source_ready:
            check_ready = getattr(self._event_source, "check_ready", None)
            if callable(check_ready):
                check_ready()
            self._event_source_ready = True
        for account_id, required_segments in self._required_segments.items():
            account = self.account(account_id)
            for segment_key in required_segments:
                segment = account.segment(segment_key)
                if (
                    segment.sync_lifecycle
                    not in {
                        SegmentSyncLifecycle.LIVE,
                        SegmentSyncLifecycle.SNAPSHOT_CURRENT,
                    }
                    or segment.freshness is not DataFreshness.FRESH
                ):
                    raise RuntimeError(
                        f"Account {account_id} required segment {segment_key} "
                        f"is not ready: lifecycle={segment.sync_lifecycle.value}, "
                        f"freshness={segment.freshness.value}"
                    )

    async def _events(self) -> AsyncIterator[AccountEvent]:
        if self._event_source is None:
            return
        async for record in self._event_source.subscribe_live():
            if AccountId(record.account_id) not in self._current_views:
                continue
            expected_stream_id = f"account.events/account:{record.account_id}"
            if record.stream_id != expected_stream_id:
                raise RuntimeError(
                    "Account event stream identity is invalid: "
                    f"expected {expected_stream_id}, received {record.stream_id}"
                )
            self._validate_event_scope(record.launch_id, record.instance_id)
            cursor = self._event_cursors.get(record.account_id, 0)
            if cursor == 0:
                cursor = record.sequence - 1
            if record.sequence <= cursor:
                continue
            expected = cursor + 1
            if record.sequence != expected:
                raise RuntimeError(
                    f"Account {record.account_id} event stream is not contiguous: "
                    f"expected {expected}, received {record.sequence}"
                )
            self._event_cursors[record.account_id] = record.sequence
            for event in map_account_event(record):
                yield event

    def _validate_event_scope(
        self, launch_id: str | None, instance_id: str | None
    ) -> None:
        if self._launch_id is not None and launch_id != self._launch_id:
            raise RuntimeError("Account event belongs to another launch")
        if self._instance_id is not None and instance_id != self._instance_id:
            raise RuntimeError("Account event belongs to another launch instance")


def _account_id(value: AccountId | str) -> AccountId:
    return value if isinstance(value, AccountId) else AccountId(value)
