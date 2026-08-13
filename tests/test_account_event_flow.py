from __future__ import annotations

import asyncio
from dataclasses import replace

import flatbuffers
import pytest

from kairospy.application.account.application import AccountApplication
from kairospy.application.account.events import (
    AccountChangeRecord,
    AccountEventRecord,
    AccountStatusChangedEvent,
    BalanceChangedEvent,
    EquityChangedEvent,
)
from kairospy.domain_types import AccountId
from kairospy.infrastructure.transport.account import decode_account_event
from kairospy.infrastructure.transport.generated.kairos.account.v1 import (
    AccountChange,
    AccountEvent,
)
from kairospy.infrastructure.transport.generated.kairos.common.v1 import MessageHeader


def _status_event_payload() -> bytes:
    builder = flatbuffers.Builder(1024)
    kind = builder.CreateString("status_changed")
    segment_key = builder.CreateString("spot")
    status = builder.CreateString("ready")
    AccountChange.AccountChangeStart(builder)
    AccountChange.AccountChangeAddKind(builder, kind)
    AccountChange.AccountChangeAddSegmentKey(builder, segment_key)
    AccountChange.AccountChangeAddStatus(builder, status)
    AccountChange.AccountChangeAddTradingEnabled(builder, True)
    change = AccountChange.AccountChangeEnd(builder)
    AccountEvent.AccountEventStartChangesVector(builder, 1)
    builder.PrependUOffsetTRelative(change)
    changes = builder.EndVector()
    message_id = builder.CreateString("account:main:1")
    stream_id = builder.CreateString("account.events:main")
    producer_id = builder.CreateString("account")
    MessageHeader.MessageHeaderStart(builder)
    MessageHeader.MessageHeaderAddMessageId(builder, message_id)
    MessageHeader.MessageHeaderAddStreamId(builder, stream_id)
    MessageHeader.MessageHeaderAddProducerId(builder, producer_id)
    MessageHeader.MessageHeaderAddSequence(builder, 1)
    header = MessageHeader.MessageHeaderEnd(builder)
    account_id = builder.CreateString("main")
    AccountEvent.AccountEventStart(builder)
    AccountEvent.AccountEventAddHeader(builder, header)
    AccountEvent.AccountEventAddAccountId(builder, account_id)
    AccountEvent.AccountEventAddChanges(builder, changes)
    AccountEvent.AccountEventAddOccurredAtUnixNanos(builder, 10)
    event = AccountEvent.AccountEventEnd(builder)
    builder.Finish(event, file_identifier=b"ACE1")
    return bytes(builder.Output())


class _Records:
    def __init__(self, *records: AccountEventRecord) -> None:
        self.records = records

    async def events(self, after_sequence: int = 0):
        for record in self.records:
            if record.sequence > after_sequence:
                yield record


class _LiveRecords(_Records):
    join_from_latest = True


def _record(sequence: int, account_id: str = "main") -> AccountEventRecord:
    return AccountEventRecord(
        f"account.events:{account_id}",
        sequence,
        "account",
        account_id,
        (
            AccountChangeRecord(
                "status_changed",
                "spot",
                {"status": "ready", "stale": False, "trading_enabled": True},
            ),
        ),
        10,
    )


def test_decodes_typed_account_status_event() -> None:
    record = decode_account_event(_status_event_payload())
    assert record.account_id == "main"
    assert record.sequence == 1
    application = AccountApplication({AccountId("main"): object()}, _Records(record))

    async def collect():
        return [event async for event in application._events()]

    events = asyncio.run(collect())
    assert len(events) == 1
    assert isinstance(events[0], AccountStatusChangedEvent)
    assert str(events[0].data.segment_key) == "spot"
    assert events[0].data.trading_enabled is True


def test_account_scope_filters_other_accounts() -> None:
    application = AccountApplication(
        {AccountId("main"): object()}, _Records(_record(1, "other"), _record(1))
    )

    async def collect():
        return [event async for event in application._events()]

    events = asyncio.run(collect())
    assert [event.data.account_id for event in events] == [AccountId("main")]


def test_account_gap_fails_without_reading_snapshot() -> None:
    application = AccountApplication(
        {AccountId("main"): object()}, _Records(_record(1), _record(3))
    )

    async def collect():
        return [event async for event in application._events()]

    with pytest.raises(RuntimeError, match="expected 2, received 3"):
        asyncio.run(collect())


def test_account_rejects_a_stream_identity_for_another_scope() -> None:
    invalid = _record(1)
    invalid = AccountEventRecord(
        "account.events:other",
        invalid.sequence,
        invalid.producer,
        invalid.account_id,
        invalid.changes,
        invalid.occurred_at_unix_nanos,
    )
    application = AccountApplication({AccountId("main"): object()}, _Records(invalid))

    async def collect():
        return [event async for event in application._events()]

    with pytest.raises(RuntimeError, match="stream identity"):
        asyncio.run(collect())


def test_one_account_record_can_map_to_multiple_typed_callbacks() -> None:
    event = AccountEventRecord(
        "account.events:main",
        1,
        "account",
        "main",
        (
            AccountChangeRecord(
                "balance_changed",
                "spot",
                {"asset": "USD", "total": "10", "available": "8"},
            ),
            AccountChangeRecord("equity_changed", "spot", {"equity": "10"}),
        ),
        10,
    )
    application = AccountApplication({AccountId("main"): object()}, _Records(event))

    async def collect():
        return [item async for item in application._events()]

    events = asyncio.run(collect())
    assert [type(item) for item in events] == [
        BalanceChangedEvent,
        EquityChangedEvent,
    ]
    assert {item.metadata.sequence for item in events} == {1}
    assert {str(item.data.segment_key) for item in events} == {"spot"}


def test_live_account_source_joins_at_first_observed_event_then_detects_gap() -> None:
    application = AccountApplication(
        {AccountId("main"): object()},
        _LiveRecords(_record(40), _record(41)),
    )

    async def collect(source):
        return [item async for item in source._events()]

    assert len(asyncio.run(collect(application))) == 2
    application = AccountApplication(
        {AccountId("main"): object()},
        _LiveRecords(_record(40), _record(42)),
    )
    with pytest.raises(RuntimeError, match="expected 41, received 42"):
        asyncio.run(collect(application))


def test_account_ignores_duplicate_and_stale_redelivery() -> None:
    application = AccountApplication(
        {AccountId("main"): object()},
        _Records(_record(1), _record(2), _record(1), _record(3)),
    )

    async def collect():
        return [item async for item in application._events()]

    assert [item.metadata.sequence for item in asyncio.run(collect())] == [1, 2, 3]


def test_account_rejects_another_launch_instance_before_mapping() -> None:
    application = AccountApplication(
        {AccountId("main"): object()},
        _Records(replace(_record(1), launch_id="launch", instance_id="other")),
        launch_id="launch",
        instance_id="instance",
    )

    async def collect():
        return [item async for item in application._events()]

    with pytest.raises(RuntimeError, match="another launch instance"):
        asyncio.run(collect())
