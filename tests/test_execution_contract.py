from pathlib import Path

import flatbuffers
import pytest

from kairospy.infrastructure.contracts.execution import (
    ExecutionViewKey,
    ExecutionViewKind,
    decode_event,
    decode_view,
)
from kairospy.infrastructure.contracts.execution.source import decode_execution_event


def test_execution_view_key_matches_rust_contract_path() -> None:
    key = ExecutionViewKey(
        workspace_id="workspace:demo",
        launch_id="launch-1",
        instance_id="instance-1",
        kind=ExecutionViewKind.CURRENT_EXECUTION,
    )

    assert key.canonical_key() == (
        "workspace=workspace:demo;launch=launch-1;"
        "instance=instance-1;view=current-execution"
    )
    assert key.resource_path("/tmp/workspace") == Path(
        "/tmp/workspace/execution/views/workspace%3Ademo/launch=launch-1/instance=instance-1/current-execution/current.snapshot"
    )


def test_execution_view_key_rejects_incomplete_identity() -> None:
    with pytest.raises(ValueError, match="view workspace identity is incomplete"):
        ExecutionViewKey("", ExecutionViewKind.CURRENT_EXECUTION)


def test_execution_root_decoders_reject_unknown_identifiers() -> None:
    for identifier in (b"NOPE", b"EXV2"):
        with pytest.raises(ValueError, match="unknown Execution v2 event identifier"):
            decode_event(b"\x00\x00\x00\x00" + identifier)
    with pytest.raises(
        ValueError, match="invalid Execution current-execution view identifier"
    ):
        decode_view(b"\x00\x00\x00\x00NOPE", ExecutionViewKind.CURRENT_EXECUTION)


def test_current_execution_view_exposes_algorithm_run_quality() -> None:
    from kairospy.infrastructure.protocol.generated.kairos.common.v2 import (
        Decimal64,
        ViewMetadata,
    )
    from kairospy.infrastructure.protocol.generated.kairos.execution.v2 import (
        AlgorithmLegBenchmarkQuality,
        AlgorithmLegExecutionQuality,
        AlgorithmLegState,
        AlgorithmRunState,
        CurrentExecutionView,
    )

    builder = flatbuffers.Builder(1024)
    strings = {
        value: builder.CreateString(value)
        for value in (
            "snapshot-1",
            "execution",
            "workspace=workspace;launch=launch;instance=instance;view=current-execution",
            "workspace",
            "launch",
            "instance",
            "intent-quality:algorithm:1",
            "intent-quality",
            "immediate",
            "leg-quality",
            "instrument:test:BTCUSDT",
            "market:test:BTCUSDT",
        )
    }

    AlgorithmLegBenchmarkQuality.Start(builder)
    AlgorithmLegBenchmarkQuality.AddKind(builder, 1)
    AlgorithmLegBenchmarkQuality.AddInstrumentId(
        builder, strings["instrument:test:BTCUSDT"]
    )
    AlgorithmLegBenchmarkQuality.AddMarketId(
        builder, strings["market:test:BTCUSDT"]
    )
    benchmark_price = Decimal64.CreateDecimal64(builder, 100, 0)
    AlgorithmLegBenchmarkQuality.AddPrice(builder, benchmark_price)
    AlgorithmLegBenchmarkQuality.AddObservedAtUnixNanos(builder, 90)
    benchmark_notional = Decimal64.CreateDecimal64(builder, 1000, 0)
    AlgorithmLegBenchmarkQuality.AddBenchmarkNotional(builder, benchmark_notional)
    shortfall = Decimal64.CreateDecimal64(builder, 12, 0)
    AlgorithmLegBenchmarkQuality.AddImplementationShortfall(builder, shortfall)
    benchmark = AlgorithmLegBenchmarkQuality.End(builder)

    AlgorithmLegExecutionQuality.StartFeeTotalsVector(builder, 0)
    fees = builder.EndVector()
    AlgorithmLegExecutionQuality.Start(builder)
    AlgorithmLegExecutionQuality.AddOrderCount(builder, 1)
    AlgorithmLegExecutionQuality.AddFillCount(builder, 2)
    AlgorithmLegExecutionQuality.AddCancelAttemptCount(builder, 1)
    AlgorithmLegExecutionQuality.AddFirstOrderSubmittedAtUnixNanos(builder, 100)
    AlgorithmLegExecutionQuality.AddFirstFillAtUnixNanos(builder, 110)
    AlgorithmLegExecutionQuality.AddLastFillAtUnixNanos(builder, 120)
    AlgorithmLegExecutionQuality.AddTimeToFirstFillNanos(builder, 10)
    AlgorithmLegExecutionQuality.AddTimeToLastFillNanos(builder, 20)
    AlgorithmLegExecutionQuality.AddFeeTotals(builder, fees)
    average = Decimal64.CreateDecimal64(builder, 1012, 1)
    AlgorithmLegExecutionQuality.AddAverageFillPrice(builder, average)
    notional = Decimal64.CreateDecimal64(builder, 1012, 0)
    AlgorithmLegExecutionQuality.AddGrossNotional(builder, notional)
    filled = Decimal64.CreateDecimal64(builder, 10, 0)
    AlgorithmLegExecutionQuality.AddFilledQuantity(builder, filled)
    AlgorithmLegExecutionQuality.AddBenchmark(builder, benchmark)
    quality = AlgorithmLegExecutionQuality.End(builder)

    AlgorithmLegState.Start(builder)
    AlgorithmLegState.AddLegId(builder, strings["leg-quality"])
    AlgorithmLegState.AddRole(builder, 1)
    AlgorithmLegState.AddLifecycle(builder, 4)
    AlgorithmLegState.AddQuality(builder, quality)
    filled = Decimal64.CreateDecimal64(builder, 10, 0)
    AlgorithmLegState.AddFilledQuantity(builder, filled)
    committed = Decimal64.CreateDecimal64(builder, 0, 0)
    AlgorithmLegState.AddCommittedQuantity(builder, committed)
    target = Decimal64.CreateDecimal64(builder, 10, 0)
    AlgorithmLegState.AddTargetQuantity(builder, target)
    leg = AlgorithmLegState.End(builder)

    AlgorithmRunState.StartLegsVector(builder, 1)
    builder.PrependUOffsetTRelative(leg)
    legs = builder.EndVector()
    AlgorithmRunState.Start(builder)
    AlgorithmRunState.AddAlgorithmRunId(builder, strings["intent-quality:algorithm:1"])
    AlgorithmRunState.AddAlgorithmVersion(builder, 1)
    AlgorithmRunState.AddIntentId(builder, strings["intent-quality"])
    AlgorithmRunState.AddAlgorithmKind(builder, strings["immediate"])
    AlgorithmRunState.AddLifecycle(builder, 4)
    AlgorithmRunState.AddDecisionSequence(builder, 2)
    AlgorithmRunState.AddLegs(builder, legs)
    run = AlgorithmRunState.End(builder)

    CurrentExecutionView.StartAlgorithmRunsVector(builder, 1)
    builder.PrependUOffsetTRelative(run)
    runs = builder.EndVector()
    CurrentExecutionView.StartIntentsVector(builder, 0)
    intents = builder.EndVector()
    empty_vectors = []
    for start in (
        CurrentExecutionView.StartOrdersVector,
        CurrentExecutionView.StartFillsVector,
        CurrentExecutionView.StartOrderEventsVector,
        CurrentExecutionView.StartIntentEventsVector,
        CurrentExecutionView.StartUnknownRemoteOrdersVector,
        CurrentExecutionView.StartCommitmentsVector,
        CurrentExecutionView.StartRiskReservationsVector,
    ):
        start(builder, 0)
        empty_vectors.append(builder.EndVector())

    ViewMetadata.Start(builder)
    ViewMetadata.AddSnapshotId(builder, strings["snapshot-1"])
    ViewMetadata.AddResourceId(builder, strings["execution"])
    ViewMetadata.AddResourceEpoch(builder, 1)
    ViewMetadata.AddViewKey(
        builder,
        strings[
            "workspace=workspace;launch=launch;instance=instance;view=current-execution"
        ],
    )
    ViewMetadata.AddOwnerId(builder, strings["execution"])
    ViewMetadata.AddWorkspaceId(builder, strings["workspace"])
    ViewMetadata.AddLaunchId(builder, strings["launch"])
    ViewMetadata.AddInstanceId(builder, strings["instance"])
    metadata = ViewMetadata.End(builder)

    CurrentExecutionView.Start(builder)
    CurrentExecutionView.AddMetadata(builder, metadata)
    CurrentExecutionView.AddOrders(builder, empty_vectors[0])
    CurrentExecutionView.AddIntents(builder, intents)
    CurrentExecutionView.AddAlgorithmRuns(builder, runs)
    CurrentExecutionView.AddFills(builder, empty_vectors[1])
    CurrentExecutionView.AddOrderEvents(builder, empty_vectors[2])
    CurrentExecutionView.AddIntentEvents(builder, empty_vectors[3])
    CurrentExecutionView.AddUnknownRemoteOrders(builder, empty_vectors[4])
    CurrentExecutionView.AddCommitments(builder, empty_vectors[5])
    CurrentExecutionView.AddRiskReservations(builder, empty_vectors[6])
    view = CurrentExecutionView.End(builder)
    builder.Finish(view, file_identifier=b"ECV2")

    decoded = decode_view(bytes(builder.Output()), ExecutionViewKind.CURRENT_EXECUTION)
    assert decoded.AlgorithmRunsLength() == 1
    decoded_run = decoded.AlgorithmRuns(0)
    assert decoded_run.AlgorithmRunId() == b"intent-quality:algorithm:1"
    decoded_quality = decoded_run.Legs(0).Quality()
    assert decoded_quality.FillCount() == 2
    assert decoded_quality.FilledQuantity().Mantissa() == 10
    assert decoded_quality.GrossNotional().Mantissa() == 1012
    assert decoded_quality.AverageFillPrice().Scale() == 1
    assert decoded_quality.TimeToLastFillNanos() == 20
    decoded_benchmark = decoded_quality.Benchmark()
    assert decoded_benchmark.Kind() == 1
    assert decoded_benchmark.InstrumentId() == b"instrument:test:BTCUSDT"
    assert decoded_benchmark.MarketId() == b"market:test:BTCUSDT"
    assert decoded_benchmark.Price().Mantissa() == 100
    assert decoded_benchmark.BenchmarkNotional().Mantissa() == 1000
    assert decoded_benchmark.ImplementationShortfall().Mantissa() == 12


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
