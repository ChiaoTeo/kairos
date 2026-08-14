from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from kairospy.domain_types import AccountId

from .errors import AccountNotEnabledError
from .events import AccountEvent
from .mapping import map_account_event
from .models import AccountSnapshot, AccountsSnapshot


class AccountApplication:
    """Typed read-only Account access scoped to one Strategy launch.

    Each configured AccountId owns a distinct projection reader and mmap. A
    reader returns every segment for that logical account in one generation.
    """

    def __init__(
        self,
        projections: Mapping[AccountId, Any],
        event_source: Any | None = None,
        *,
        launch_id: str | None = None,
        instance_id: str | None = None,
    ) -> None:
        self._projections = dict(projections)
        self._event_source = event_source
        self._launch_id = launch_id
        self._instance_id = instance_id
        self._event_cursors: dict[str, int] = {}
        self._event_source_ready = event_source is None

    @property
    def account_ids(self) -> tuple[AccountId, ...]:
        """Account identities in launch configuration order."""

        return tuple(self._projections)

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
                projection.snapshot(account_id)
                for account_id, projection in self._projections.items()
            )
        )

    def account(self, account: AccountId | str) -> AccountSnapshot:
        """Read one logical Account and all of its segments from its mmap."""

        account_id = _account_id(account)
        try:
            projection = self._projections[account_id]
        except KeyError as error:
            raise AccountNotEnabledError(account_id) from error
        return projection.snapshot(account_id)

    def _check_event_source_ready(self) -> None:
        if self._event_source_ready:
            return
        check_ready = getattr(self._event_source, "check_ready", None)
        if callable(check_ready):
            check_ready()
        self._event_source_ready = True

    async def _events(self) -> AsyncIterator[AccountEvent]:
        if self._event_source is None:
            return
        async for record in self._event_source.events(after_sequence=0):
            if AccountId(record.account_id) not in self._projections:
                continue
            expected_stream_id = f"account.events/account:{record.account_id}"
            if record.stream_id != expected_stream_id:
                raise RuntimeError(
                    "Account event stream identity is invalid: "
                    f"expected {expected_stream_id}, received {record.stream_id}"
                )
            self._validate_event_scope(record.launch_id, record.instance_id)
            cursor = self._event_cursors.get(record.account_id, 0)
            if cursor == 0 and bool(
                getattr(self._event_source, "join_from_latest", False)
            ):
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
