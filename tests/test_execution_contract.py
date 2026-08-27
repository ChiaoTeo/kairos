from pathlib import Path

import flatbuffers

from kairospy.infrastructure.contracts.execution import (
    decode_event,
    execution_indexed_environment_path,
)
from kairospy.infrastructure.contracts.execution.source import decode_execution_event


def test_execution_indexed_path_matches_rust_contract() -> None:
    assert execution_indexed_environment_path("/tmp/workspace") == Path(
        "/tmp/workspace/views/v3/Execution/execution-main/epoch-1/current.lmdb"
    )


def test_execution_event_decoder_returns_generated_v2_root() -> None:
    from kairospy.infrastructure.protocol.generated.kairos.common.v2 import (
        EventMetadata,
    )
    from kairospy.infrastructure.protocol.generated.kairos.execution.v2 import (
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
    from kairospy.infrastructure.protocol.generated.kairos.common.v2 import (
        Decimal64,
        EventMetadata,
    )
    from kairospy.infrastructure.protocol.generated.kairos.execution.v2 import (
        ExecutionBenchmarkObservation,
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
    ExecutionBenchmarkObservation.Start(builder)
    ExecutionBenchmarkObservation.AddKind(builder, 1)
    ExecutionBenchmarkObservation.AddLegId(builder, strings["leg-1"])
    ExecutionBenchmarkObservation.AddInstrumentId(
        builder, strings["instrument:test:BTCUSDT"]
    )
    ExecutionBenchmarkObservation.AddMarketId(
        builder, strings["market:test:BTCUSDT"]
    )
    benchmark_price = Decimal64.CreateDecimal64(builder, 100, 0)
    ExecutionBenchmarkObservation.AddPrice(builder, benchmark_price)
    ExecutionBenchmarkObservation.AddObservedAtUnixNanos(builder, 10)
    benchmark = ExecutionBenchmarkObservation.End(builder)
    ExecutionIntent.ExecutionIntentStartExecutionBenchmarksVector(builder, 1)
    builder.PrependUOffsetTRelative(benchmark)
    execution_benchmarks = builder.EndVector()
    ExecutionIntent.ExecutionIntentStart(builder)
    ExecutionIntent.ExecutionIntentAddIntentId(builder, strings["intent-1"])
    ExecutionIntent.ExecutionIntentAddStrategyId(builder, strings["strategy-a"])
    ExecutionIntent.ExecutionIntentAddLaunchId(builder, strings["launch-1"])
    ExecutionIntent.ExecutionIntentAddInstanceId(builder, strings["instance-1"])
    ExecutionIntent.ExecutionIntentAddLegs(builder, legs)
    ExecutionIntent.ExecutionIntentAddEvidence(builder, evidence)
    ExecutionIntent.ExecutionIntentAddExecutionBenchmarks(
        builder, execution_benchmarks
    )
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
    assert change.payload["intent"]["execution_benchmarks"] == [
        {
            "kind": "arrival",
            "leg_id": "leg-1",
            "instrument_id": "instrument:test:BTCUSDT",
            "market_id": "market:test:BTCUSDT",
            "price": "100",
            "observed_at_unix_nanos": 10,
        }
    ]
