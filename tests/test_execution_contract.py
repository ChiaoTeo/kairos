from pathlib import Path

import flatbuffers
import pytest

from kairospy.infrastructure.contracts.execution import (
    ExecutionViewKey,
    ExecutionViewKind,
    decode_event,
    decode_view,
)


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
        "/tmp/workspace/execution/views/workspace%3Ademo/"
        "active-orders/current.snapshot"
    )


def test_execution_view_key_rejects_incomplete_identity() -> None:
    with pytest.raises(ValueError, match="view workspace identity is incomplete"):
        ExecutionViewKey("", ExecutionViewKind.ACTIVE_INTENTS)


def test_execution_root_decoders_reject_unknown_identifiers() -> None:
    with pytest.raises(ValueError, match="unknown Execution v2 event identifier"):
        decode_event(b"\x00\x00\x00\x00NOPE")
    with pytest.raises(ValueError, match="invalid Execution active-orders view identifier"):
        decode_view(b"\x00\x00\x00\x00NOPE", ExecutionViewKind.ACTIVE_ORDERS)


def test_execution_event_decoder_returns_generated_v2_root() -> None:
    from kairospy.infrastructure.transport.generated.kairos.common.v2 import EventMetadata
    from kairospy.infrastructure.transport.generated.kairos.execution.v2 import IntentAccepted

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
