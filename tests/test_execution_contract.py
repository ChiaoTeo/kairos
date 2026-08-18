from pathlib import Path

import flatbuffers
import pytest

from kairospy.infrastructure.contracts.execution import (
    ExecutionViewKey,
    ExecutionViewKind,
    decode_event,
    decode_view,
)
from kairospy.infrastructure.transport.execution import decode_execution_event


def test_execution_view_key_matches_rust_contract_path() -> None:
    key = ExecutionViewKey(
        workspace_id="workspace:demo",
        launch_id="launch-1",
        instance_id="instance-1",
        kind=ExecutionViewKind.ACTIVE_ORDERS,
    )

    assert key.canonical_key() == (
        "workspace=workspace:demo;launch=launch-1;"
        "instance=instance-1;view=active-orders"
    )
    assert key.resource_path("/tmp/workspace") == Path(
        "/tmp/workspace/execution/views/workspace%3Ademo/active-orders/current.snapshot"
    )


def test_execution_view_key_rejects_incomplete_identity() -> None:
    with pytest.raises(ValueError, match="view workspace identity is incomplete"):
        ExecutionViewKey("", ExecutionViewKind.ACTIVE_INTENTS)


def test_execution_root_decoders_reject_unknown_identifiers() -> None:
    with pytest.raises(ValueError, match="unknown Execution v2 event identifier"):
        decode_event(b"\x00\x00\x00\x00NOPE")
    with pytest.raises(
        ValueError, match="invalid Execution active-orders view identifier"
    ):
        decode_view(b"\x00\x00\x00\x00NOPE", ExecutionViewKind.ACTIVE_ORDERS)


def test_execution_event_decoder_returns_generated_v2_root() -> None:
    from kairospy.infrastructure.transport.generated.kairos.common.v2 import (
        EventMetadata,
    )
    from kairospy.infrastructure.transport.generated.kairos.execution.v2 import (
        IntentAccepted,
    )

    builder = flatbuffers.Builder(256)
    event_id = builder.CreateString("event-1")
    stream_id = builder.CreateString("execution.events")
    producer_id = builder.CreateString("execution")
    workspace_id = builder.CreateString("workspace:demo")
    instance_id = builder.CreateString("instance-1")
    intent_id = builder.CreateString("intent-1")

    EventMetadata.EventMetadataStart(builder)
    EventMetadata.EventMetadataAddEventId(builder, event_id)
    EventMetadata.EventMetadataAddStreamId(builder, stream_id)
    EventMetadata.EventMetadataAddSequence(builder, 1)
    EventMetadata.EventMetadataAddProducerId(builder, producer_id)
    EventMetadata.EventMetadataAddWorkspaceId(builder, workspace_id)
    EventMetadata.EventMetadataAddInstanceId(builder, instance_id)
    EventMetadata.EventMetadataAddOccurredAtUnixNanos(builder, 10)
    metadata = EventMetadata.EventMetadataEnd(builder)

    IntentAccepted.IntentAcceptedStart(builder)
    IntentAccepted.IntentAcceptedAddMetadata(builder, metadata)
    IntentAccepted.IntentAcceptedAddIntentId(builder, intent_id)
    event = IntentAccepted.IntentAcceptedEnd(builder)
    builder.Finish(event, file_identifier=b"EIA2")

    decoded = decode_event(bytes(builder.Output()))
    assert decoded.IntentId() == b"intent-1"
    assert decoded.Metadata().StreamId() == b"execution.events"


def test_intent_lifecycle_changed_is_strategy_scoped_and_decision_correlated() -> None:
    from kairospy.infrastructure.transport.generated.kairos.common.v2 import (
        EventMetadata,
    )
    from kairospy.infrastructure.transport.generated.kairos.execution.v2 import (
        ExecutionIntent,
        IntentLeg,
        IntentLifecycleChanged,
    )

    builder = flatbuffers.Builder(1024)
    strings = {
        value: builder.CreateString(value)
        for value in (
            "event-2",
            "execution.events",
            "execution",
            "workspace:demo",
            "launch-1",
            "instance-1",
            "intent-1",
            "strategy-a",
            "strategy-a:decision:1",
            "leg-1",
            "main",
            "spot",
            "instrument:test:BTCUSDT",
            "market:test:BTCUSDT",
            "route-1",
            "order-1",
            "filled",
        )
    }

    IntentLeg.IntentLegStart(builder)
    IntentLeg.IntentLegAddLegId(builder, strings["leg-1"])
    IntentLeg.IntentLegAddAccountId(builder, strings["main"])
    IntentLeg.IntentLegAddSegmentKey(builder, strings["spot"])
    IntentLeg.IntentLegAddInstrumentId(builder, strings["instrument:test:BTCUSDT"])
    IntentLeg.IntentLegAddMarketId(builder, strings["market:test:BTCUSDT"])
    IntentLeg.IntentLegAddExecutionRouteId(builder, strings["route-1"])
    leg = IntentLeg.IntentLegEnd(builder)
    ExecutionIntent.ExecutionIntentStartLegsVector(builder, 1)
    builder.PrependUOffsetTRelative(leg)
    legs = builder.EndVector()
    ExecutionIntent.ExecutionIntentStartEvidenceVector(builder, 0)
    evidence = builder.EndVector()
    ExecutionIntent.ExecutionIntentStart(builder)
    ExecutionIntent.ExecutionIntentAddIntentId(builder, strings["intent-1"])
    ExecutionIntent.ExecutionIntentAddStrategyId(builder, strings["strategy-a"])
    ExecutionIntent.ExecutionIntentAddLaunchId(builder, strings["launch-1"])
    ExecutionIntent.ExecutionIntentAddInstanceId(builder, strings["instance-1"])
    ExecutionIntent.ExecutionIntentAddLegs(builder, legs)
    ExecutionIntent.ExecutionIntentAddEvidence(builder, evidence)
    ExecutionIntent.ExecutionIntentAddStrategyDecisionId(
        builder, strings["strategy-a:decision:1"]
    )
    intent = ExecutionIntent.ExecutionIntentEnd(builder)

    IntentLifecycleChanged.IntentLifecycleChangedStartOrderIdsVector(builder, 1)
    builder.PrependUOffsetTRelative(strings["order-1"])
    order_ids = builder.EndVector()
    IntentLifecycleChanged.IntentLifecycleChangedStartDependencyEvidenceVector(
        builder, 0
    )
    dependency_evidence = builder.EndVector()

    EventMetadata.EventMetadataStart(builder)
    EventMetadata.EventMetadataAddEventId(builder, strings["event-2"])
    EventMetadata.EventMetadataAddStreamId(builder, strings["execution.events"])
    EventMetadata.EventMetadataAddSequence(builder, 2)
    EventMetadata.EventMetadataAddProducerId(builder, strings["execution"])
    EventMetadata.EventMetadataAddWorkspaceId(builder, strings["workspace:demo"])
    EventMetadata.EventMetadataAddLaunchId(builder, strings["launch-1"])
    EventMetadata.EventMetadataAddInstanceId(builder, strings["instance-1"])
    EventMetadata.EventMetadataAddOccurredAtUnixNanos(builder, 20)
    metadata = EventMetadata.EventMetadataEnd(builder)

    IntentLifecycleChanged.IntentLifecycleChangedStart(builder)
    IntentLifecycleChanged.IntentLifecycleChangedAddMetadata(builder, metadata)
    IntentLifecycleChanged.IntentLifecycleChangedAddIntent(builder, intent)
    IntentLifecycleChanged.IntentLifecycleChangedAddPreviousLifecycle(builder, 4)
    IntentLifecycleChanged.IntentLifecycleChangedAddLifecycle(builder, 7)
    IntentLifecycleChanged.IntentLifecycleChangedAddOrderIds(builder, order_ids)
    IntentLifecycleChanged.IntentLifecycleChangedAddReason(builder, strings["filled"])
    IntentLifecycleChanged.IntentLifecycleChangedAddDependencyEvidence(
        builder, dependency_evidence
    )
    event = IntentLifecycleChanged.IntentLifecycleChangedEnd(builder)
    builder.Finish(event, file_identifier=b"EIL2")

    record = decode_execution_event(bytes(builder.Output()))
    change = record.changes[0]
    assert change.kind == "intent_update"
    assert change.strategy_id == "strategy-a"
    assert change.payload["status"] == "satisfied"
    assert change.payload["previous_status"] == "executing"
    assert change.payload["order_ids"] == ["order-1"]
    assert change.payload["intent"]["strategy_decision_id"] == ("strategy-a:decision:1")
    assert change.payload["intent"]["account_ids"] == ["main"]
