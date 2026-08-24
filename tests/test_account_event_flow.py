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
from kairospy.primitives.account import AccountId
from kairospy.infrastructure.transport.account import decode_account_event
from kairospy.infrastructure.transport.generated.kairos.account.v2 import (
    AccountFactProvenance,
    AccountStatus,
    AccountStatusChanged,
    FreshnessState,
)
from kairospy.infrastructure.transport.generated.kairos.common.v2 import EventMetadata


def _status_event_payload() -> bytes:
    builder = flatbuffers.Builder(1024)
    segment_key = builder.CreateString("spot")
    account_id = builder.CreateString("main")
    event_id = builder.CreateString("account:main:1")
    stream_id = builder.CreateString("account.events/account:main")
    producer_id = builder.CreateString("account")
    source_id = builder.CreateString("binance:spot")
    provider_event_id = builder.CreateString("provider:10")
    EventMetadata.EventMetadataStart(builder)
    EventMetadata.EventMetadataAddEventId(builder, event_id)
    EventMetadata.EventMetadataAddStreamId(builder, stream_id)
    EventMetadata.EventMetadataAddSequence(builder, 1)
    EventMetadata.EventMetadataAddProducerId(builder, producer_id)
    EventMetadata.EventMetadataAddOccurredAtUnixNanos(builder, 10)
    metadata = EventMetadata.EventMetadataEnd(builder)
    AccountFactProvenance.AccountFactProvenanceStart(builder)
    AccountFactProvenance.AccountFactProvenanceAddSourceId(builder, source_id)
    AccountFactProvenance.AccountFactProvenanceAddProviderEventId(
        builder, provider_event_id
    )
    AccountFactProvenance.AccountFactProvenanceAddProviderSequence(builder, 10)
    provenance = AccountFactProvenance.AccountFactProvenanceEnd(builder)
    AccountStatusChanged.AccountStatusChangedStart(builder)
    AccountStatusChanged.AccountStatusChangedAddMetadata(builder, metadata)
    AccountStatusChanged.AccountStatusChangedAddAccountId(builder, account_id)
    AccountStatusChanged.AccountStatusChangedAddSegmentKey(builder, segment_key)
    AccountStatusChanged.AccountStatusChangedAddStatus(
        builder, AccountStatus.AccountStatus.ACTIVE
    )
    AccountStatusChanged.AccountStatusChangedAddFreshness(
        builder, FreshnessState.FreshnessState.FRESH
    )
    AccountStatusChanged.AccountStatusChangedAddProvenance(builder, provenance)
    event = AccountStatusChanged.AccountStatusChangedEnd(builder)
    builder.Finish(event, file_identifier=b"ASC2")
    return bytes(builder.Output())


class _Records:
    def __init__(self, *records: AccountEventRecord) -> None:
        self.records = records

    async def subscribe_live(self):
        for record in self.records:
            yield record


class _LiveRecords(_Records):
    join_from_latest = True


def _record(sequence: int, account_id: str = "main") -> AccountEventRecord:
    return AccountEventRecord(
        f"account.events/account:{account_id}",
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
    assert record.provenance is not None
    assert record.provenance.source_id == "binance:spot"
    assert record.provenance.provider_sequence == 10
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
        "account.events/account:other",
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
        "account.events/account:main",
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
