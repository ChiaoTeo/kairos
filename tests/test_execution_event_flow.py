from __future__ import annotations

import asyncio
from dataclasses import replace

import flatbuffers
import pytest

from kairospy.application.execution.application import ExecutionApplication
from kairospy.application.execution.events import (
    ExecutionChangeRecord,
    ExecutionEventRecord,
    IntentUpdateEvent,
)
from kairospy.domain_types import AccountId
from kairospy.infrastructure.transport.execution import decode_execution_event
from kairospy.infrastructure.transport.generated.kairos.common.v1 import MessageHeader
from kairospy.infrastructure.transport.generated.kairos.execution.v1 import (
    ExecutionChange,
    ExecutionEventMessage,
)
from kairospy.infrastructure.transport.generated.kairos.intent.v1 import Intent


def _intent_payload() -> bytes:
    builder = flatbuffers.Builder(1024)
    intent_id = builder.CreateString("intent-1")
    strategy_id = builder.CreateString("strategy")
    launch_id = builder.CreateString("launch")
    instance_id = builder.CreateString("instance")
    instrument_id = builder.CreateString("instrument:btc")
    status = builder.CreateString("accepted")
    account_id = builder.CreateString("main")
    Intent.IntentStartAccountIdsVector(builder, 1)
    builder.PrependUOffsetTRelative(account_id)
    account_ids = builder.EndVector()
    Intent.IntentStart(builder)
    Intent.IntentAddIntentId(builder, intent_id)
    Intent.IntentAddStrategyId(builder, strategy_id)
    Intent.IntentAddLaunchId(builder, launch_id)
    Intent.IntentAddInstanceId(builder, instance_id)
    Intent.IntentAddInstrumentId(builder, instrument_id)
    Intent.IntentAddAccountIds(builder, account_ids)
    Intent.IntentAddSourceEventSequence(builder, 7)
    Intent.IntentAddStatus(builder, status)
    intent = Intent.IntentEnd(builder)
    kind = builder.CreateString("intent_update")
    change_strategy = builder.CreateString("strategy")
    ExecutionChange.ExecutionChangeStart(builder)
    ExecutionChange.ExecutionChangeAddKind(builder, kind)
    ExecutionChange.ExecutionChangeAddStrategyId(builder, change_strategy)
    ExecutionChange.ExecutionChangeAddIntent(builder, intent)
    change = ExecutionChange.ExecutionChangeEnd(builder)
    ExecutionEventMessage.ExecutionEventMessageStartChangesVector(builder, 1)
    builder.PrependUOffsetTRelative(change)
    changes = builder.EndVector()
    message_id = builder.CreateString("execution:1")
    stream_id = builder.CreateString("execution.events")
    producer_id = builder.CreateString("execution")
    header_instance = builder.CreateString("instance")
    MessageHeader.MessageHeaderStart(builder)
    MessageHeader.MessageHeaderAddMessageId(builder, message_id)
    MessageHeader.MessageHeaderAddStreamId(builder, stream_id)
    MessageHeader.MessageHeaderAddProducerId(builder, producer_id)
    MessageHeader.MessageHeaderAddInstanceId(builder, header_instance)
    MessageHeader.MessageHeaderAddSequence(builder, 1)
    header = MessageHeader.MessageHeaderEnd(builder)
    ExecutionEventMessage.ExecutionEventMessageStart(builder)
    ExecutionEventMessage.ExecutionEventMessageAddHeader(builder, header)
    ExecutionEventMessage.ExecutionEventMessageAddChanges(builder, changes)
    ExecutionEventMessage.ExecutionEventMessageAddOccurredAtUnixNanos(builder, 10)
    event = ExecutionEventMessage.ExecutionEventMessageEnd(builder)
    builder.Finish(event, file_identifier=b"EXE1")
    return bytes(builder.Output())


class _Records:
    def __init__(self, *records: ExecutionEventRecord) -> None:
        self.records = records

    async def events(self, after_sequence: int = 0):
        for record in self.records:
            if record.sequence > after_sequence:
                yield record


class _LiveRecords(_Records):
    join_from_latest = True


def _record(sequence: int, strategy: str = "strategy") -> ExecutionEventRecord:
    return ExecutionEventRecord(
        "execution.events",
        sequence,
        "execution",
        "instance",
        (
            ExecutionChangeRecord(
                "intent_update",
                strategy,
                "main",
                {
                    "intent": {
                        "intent_id": "intent-1",
                        "instrument_id": "instrument:btc",
                        "account_ids": ["main"],
                        "target_quantity": "1",
                        "reason": "",
                    },
                    "status": "accepted",
                    "order_ids": [],
                },
            ),
        ),
        10,
    )


def _application(*records: ExecutionEventRecord) -> ExecutionApplication:
    return ExecutionApplication(
        None,
        None,
        _Records(*records),
        strategy_id="strategy",
        instance_id="instance",
        account_ids=(AccountId("main"),),
    )


def test_decodes_and_maps_execution_intent_event() -> None:
    application = _application(decode_execution_event(_intent_payload()))

    async def collect():
        return [event async for event in application.events()]

    events = asyncio.run(collect())
    assert len(events) == 1
    assert isinstance(events[0], IntentUpdateEvent)
    assert str(events[0].data.id) == "intent-1"
    assert events[0].data.source_event_sequence == 7


def test_execution_scope_filters_other_strategy() -> None:
    application = _application(_record(1, "other"))

    async def collect():
        return [event async for event in application.events()]

    assert asyncio.run(collect()) == []


def test_execution_gap_fails_without_snapshot_recovery() -> None:
    application = _application(_record(1), _record(3))

    async def collect():
        return [event async for event in application.events()]

    with pytest.raises(RuntimeError, match="expected 2, received 3"):
        asyncio.run(collect())


def test_execution_ignores_duplicates_and_rejects_wrong_stream_identity() -> None:
    duplicate = _application(_record(1), _record(2), _record(1), _record(3))

    async def collect(application):
        return [event async for event in application.events()]

    assert len(asyncio.run(collect(duplicate))) == 3
    invalid = _record(1)
    invalid = ExecutionEventRecord(
        "other.events",
        invalid.sequence,
        invalid.producer,
        invalid.instance_id,
        invalid.changes,
        invalid.occurred_at_unix_nanos,
    )
    with pytest.raises(RuntimeError, match="stream identity"):
        asyncio.run(collect(_application(invalid)))


def test_live_execution_source_joins_latest_then_enforces_continuity() -> None:
    application = ExecutionApplication(
        None,
        None,
        _LiveRecords(_record(40), _record(41)),
        strategy_id="strategy",
        instance_id="instance",
        account_ids=(AccountId("main"),),
    )

    async def collect(source):
        return [item async for item in source.events()]

    assert len(asyncio.run(collect(application))) == 2
    application = ExecutionApplication(
        None,
        None,
        _LiveRecords(_record(40), _record(42)),
        strategy_id="strategy",
        instance_id="instance",
        account_ids=(AccountId("main"),),
    )
    with pytest.raises(RuntimeError, match="expected 41, received 42"):
        asyncio.run(collect(application))


def test_execution_rejects_another_launch_before_scope_filtering() -> None:
    application = ExecutionApplication(
        None,
        None,
        _Records(replace(_record(1), launch_id="other")),
        strategy_id="strategy",
        instance_id="instance",
        launch_id="launch",
        account_ids=(AccountId("main"),),
    )

    async def collect():
        return [item async for item in application.events()]

    with pytest.raises(RuntimeError, match="another launch"):
        asyncio.run(collect())


def test_execution_rejects_missing_instance_identity() -> None:
    application = ExecutionApplication(
        None,
        None,
        _Records(replace(_record(1), instance_id=None)),
        strategy_id="strategy",
        instance_id="instance",
        account_ids=(AccountId("main"),),
    )

    async def collect():
        return [item async for item in application.events()]

    with pytest.raises(RuntimeError, match="another launch instance"):
        asyncio.run(collect())


def test_execution_intent_scope_rejects_partially_owned_account_set() -> None:
    record = _record(1)
    change = replace(
        record.changes[0],
        account_id="other",
        payload={
            **record.changes[0].payload,
            "intent": {
                **record.changes[0].payload["intent"],
                "account_ids": ["other", "main"],
            },
        },
    )
    application = _application(replace(record, changes=(change,)))

    async def collect():
        return [item async for item in application.events()]

    assert asyncio.run(collect()) == []


def test_execution_intent_scope_accepts_fully_owned_cross_account_set() -> None:
    record = _record(1)
    change = replace(
        record.changes[0],
        payload={
            **record.changes[0].payload,
            "intent": {
                **record.changes[0].payload["intent"],
                "account_ids": ["main", "hedge"],
            },
        },
    )
    application = ExecutionApplication(
        None,
        None,
        _Records(replace(record, changes=(change,))),
        strategy_id="strategy",
        instance_id="instance",
        account_ids=(AccountId("main"), AccountId("hedge")),
    )

    async def collect():
        return [item async for item in application.events()]

    assert len(asyncio.run(collect())) == 1


def test_execution_order_scope_rejects_missing_account_identity() -> None:
    record = _record(1)
    change = replace(record.changes[0], kind="order_update", account_id=None)
    application = _application(replace(record, changes=(change,)))

    async def collect():
        return [item async for item in application.events()]

    with pytest.raises(ValueError, match="scope requires account_id"):
        asyncio.run(collect())
