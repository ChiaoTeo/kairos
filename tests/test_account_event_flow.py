from __future__ import annotations

import asyncio
from base64 import b64decode
from importlib import import_module

import pytest

from kairospy.infrastructure.contracts.account import decode_event
from kairospy.investment.apps.account.application.application import AccountApplication
from kairospy.primitives.account import AccountId


_STATUS_EVENT = b64decode(
    "IAAAAEFTQzIAAAAAAAASABoAFAAQAAwACwAKAAAABAASAAAAIAAAAAAAAQHwAAAA4AAAAEAAAAAAAAoAGAAUABAABAAKAAAACgAAAAAAAAAAAAAASAAAAFQAAAAYACgAJAAgABQAEAAMAAAAAAAAAAAABAAYAAAACgAAAAAAAABAAAAATAAAAAEAAAAAAAAAAAAAAEgAAABkAAAACwAAAHByb3ZpZGVyOjEwAAwAAABiaW5hbmNlOnNwb3QAAAAACQAAAHdvcmtzcGFjZQAAAAcAAABhY2NvdW50ABsAAABhY2NvdW50LmV2ZW50cy9hY2NvdW50Om1haW4ADgAAAGFjY291bnQ6bWFpbjoxAAAEAAAAbWFpbgAAAAAEAAAAc3BvdAAAAAA="
)
_OTHER_STREAM_STATUS_EVENT = b64decode(
    "IAAAAEFTQzIAAAAAAAASABoAFAAQAAwACwAKAAAABAASAAAAIAAAAAAAAQHwAAAA4AAAAEAAAAAAAAoAGAAUABAABAAKAAAACgAAAAAAAAAAAAAARAAAAFAAAAAYACQAIAAcABQAEAAMAAAAAAAAAAAABAAYAAAACgAAAAAAAAA8AAAASAAAAAEAAAAAAAAASAAAAGgAAAALAAAAcHJvdmlkZXI6MTAADAAAAGJpbmFuY2U6c3BvdAAAAAAJAAAAd29ya3NwYWNlAAAABwAAAGFjY291bnQAHAAAAGFjY291bnQuZXZlbnRzL2FjY291bnQ6b3RoZXIAAAAADgAAAGFjY291bnQ6bWFpbjoxAAAEAAAAbWFpbgAAAAAEAAAAc3BvdAAAAAA="
)


def _status_event_payload(
    *, stream_id_value: str = "account.events/account:main"
) -> bytes:
    if stream_id_value == "account.events/account:main":
        return _STATUS_EVENT
    if stream_id_value == "account.events/account:other":
        return _OTHER_STREAM_STATUS_EVENT
    raise ValueError("unsupported Account event fixture stream")


class _Records:
    def __init__(self, *records: object) -> None:
        self.records = records

    async def subscribe_live(self):
        for record in self.records:
            yield record


class _LiveRecords(_Records):
    join_from_latest = True


def _record(
    sequence: int,
    account_id: str = "main",
    *,
    launch_id: str | None = None,
    instance_id: str | None = None,
):
    native = import_module("kairospy._native_account_contract")
    return native.AccountEvent.simulation_status(
        account_id,
        "spot",
        sequence,
        launch_id=launch_id,
        instance_id=instance_id,
    )


async def _collect(application: AccountApplication) -> list[object]:
    return [event async for event in application._events()]


def test_decodes_and_yields_owner_native_account_status_event() -> None:
    native = import_module("kairospy._native_account_contract")
    record = decode_event(_status_event_payload())
    assert isinstance(record, native.AccountEvent)
    assert record.account_id == "main"
    assert record.sequence == 1
    assert record.provenance.source_id == "binance:spot"
    assert record.provenance.provider_sequence == 10

    application = AccountApplication({AccountId("main"): object()}, _Records(record))
    events = asyncio.run(_collect(application))

    assert events == [record]
    assert record.change.kind == "status_changed"
    assert record.change.segment_key == "spot"
    assert record.change.status.trading_enabled is True


def test_account_scope_filters_other_accounts() -> None:
    application = AccountApplication(
        {AccountId("main"): object()}, _Records(_record(1, "other"), _record(1))
    )
    assert [event.account_id for event in asyncio.run(_collect(application))] == [
        "main"
    ]


def test_account_gap_fails_without_reading_snapshot() -> None:
    application = AccountApplication(
        {AccountId("main"): object()}, _Records(_record(1), _record(3))
    )
    with pytest.raises(RuntimeError, match="expected 2, received 3"):
        asyncio.run(_collect(application))


def test_account_rejects_a_stream_identity_for_another_scope() -> None:
    invalid = decode_event(
        _status_event_payload(stream_id_value="account.events/account:other")
    )
    application = AccountApplication({AccountId("main"): object()}, _Records(invalid))
    with pytest.raises(RuntimeError, match="stream identity"):
        asyncio.run(_collect(application))


def test_account_frames_remain_owner_native_without_callback_dto_mapping() -> None:
    native = import_module("kairospy._native_account_contract")
    balance = native.AccountEvent.simulation_balance(
        "main", "spot", 1, "USD", "10", "8"
    )
    valuation = native.AccountEvent.simulation_valuation("main", "spot", 2, "10")
    application = AccountApplication(
        {AccountId("main"): object()}, _Records(balance, valuation)
    )

    events = asyncio.run(_collect(application))
    assert events == [balance, valuation]
    assert [event.change.kind for event in events] == [
        "balance_changed",
        "equity_changed",
    ]


def test_live_account_source_joins_at_first_observed_event_then_detects_gap() -> None:
    application = AccountApplication(
        {AccountId("main"): object()}, _LiveRecords(_record(40), _record(41))
    )
    assert len(asyncio.run(_collect(application))) == 2

    application = AccountApplication(
        {AccountId("main"): object()}, _LiveRecords(_record(40), _record(42))
    )
    with pytest.raises(RuntimeError, match="expected 41, received 42"):
        asyncio.run(_collect(application))


def test_account_ignores_duplicate_and_stale_redelivery() -> None:
    application = AccountApplication(
        {AccountId("main"): object()},
        _Records(_record(1), _record(2), _record(1), _record(3)),
    )
    assert [
        event.metadata.sequence for event in asyncio.run(_collect(application))
    ] == [
        1,
        2,
        3,
    ]


def test_account_rejects_another_launch_instance_before_dispatch() -> None:
    application = AccountApplication(
        {AccountId("main"): object()},
        _Records(_record(1, launch_id="launch", instance_id="other")),
        launch_id="launch",
        instance_id="instance",
    )
    with pytest.raises(RuntimeError, match="another launch instance"):
        asyncio.run(_collect(application))
