from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Callable, Protocol

from kairospy.market.canonical import CanonicalEventEnvelope
from kairospy.market.stream import BoundedEventChannel
from kairospy.infrastructure.storage.codec import to_primitive


@dataclass(frozen=True, slots=True)
class MarketDataSoakResult:
    source: str
    stream_id: str
    started_at: datetime
    ended_at: datetime
    target_duration_seconds: float
    actual_duration_seconds: float
    event_count: int
    raw_message_count: int
    canonical_event_count: int
    ignored_message_count: int
    reconnect_count: int
    first_source_sequence: int | None
    last_source_sequence: int | None
    channel_capacity: int
    peak_channel_depth: int
    peak_channel_utilization: float
    channel_dropped: int
    raw_journal_bytes: int
    canonical_capture_bytes: int
    capture_segment_count: int
    sequence_regressions: int
    maximum_interarrival_seconds: float
    tail_silence_seconds: float
    minimum_events: int
    maximum_silence_seconds: float
    maximum_channel_utilization: float
    failures: tuple[str, ...]
    passed: bool
    audit_hash: str
    artifact: str


class MarketSoakService(Protocol):
    stream_id: str
    reconnects: int
    session: object

    async def run(self) -> None: ...


@dataclass(frozen=True, slots=True)
class MarketDataRestartCampaignResult:
    source: str
    stream_id: str
    target_duration_seconds: float
    actual_duration_seconds: float
    leg_count: int
    restart_count: int
    event_count: int
    reconnect_count: int
    boundary_sequence_regressions: int
    leg_artifacts: tuple[str, ...]
    leg_audit_hashes: tuple[str, ...]
    failures: tuple[str, ...]
    restart_drill_passed: bool
    passed: bool
    audit_hash: str
    artifact: str


async def run_binance_market_soak(
    service: MarketSoakService,
    output: BoundedEventChannel[CanonicalEventEnvelope],
    *,
    duration_seconds: float,
    minimum_events: int,
    maximum_silence_seconds: float,
    artifact_path: str | Path,
    maximum_channel_utilization: float = 0.9,
) -> MarketDataSoakResult:
    if (duration_seconds <= 0 or minimum_events < 1 or maximum_silence_seconds <= 0
            or not 0 < maximum_channel_utilization <= 1):
        raise ValueError("market-data soak thresholds must be positive")
    started_at = datetime.now(timezone.utc)
    event_count = 0
    previous: CanonicalEventEnvelope | None = None
    last: CanonicalEventEnvelope | None = None
    first_source_sequence: int | None = None
    maximum_interarrival = 0.0
    sequence_regressions = 0

    async def consume() -> None:
        nonlocal event_count, previous, last, maximum_interarrival, sequence_regressions, first_source_sequence
        async for event in output.events():
            if event_count == 0:
                first_source_sequence = event.source_sequence
            if previous is not None:
                maximum_interarrival = max(
                    maximum_interarrival,
                    max(0.0, (event.receive_time - previous.receive_time).total_seconds()),
                )
                if (event.source_sequence is not None and previous.source_sequence is not None
                        and event.partition_key == previous.partition_key
                        and event.source_sequence < previous.source_sequence):
                    sequence_regressions += 1
            event_count += 1
            previous = last = event

    producer = asyncio.create_task(service.run(), name="market-soak-producer")
    consumer = asyncio.create_task(consume(), name="market-soak-consumer")
    producer_error: Exception | None = None
    try:
        await asyncio.sleep(duration_seconds)
    finally:
        service.session.stop()  # type: ignore[attr-defined]
        try:
            await producer
        except Exception as error:
            producer_error = error
        await consumer
    ended_at = datetime.now(timezone.utc)
    tail_silence = max(0.0, (ended_at - last.receive_time).total_seconds()) if last else (
        ended_at - started_at
    ).total_seconds()
    failures = []
    if producer_error is not None:
        failures.append(f"producer failed: {type(producer_error).__name__}: {producer_error}")
    ignored = int(getattr(service, "ignored_messages", 0))
    if ignored:
        failures.append(f"{ignored} raw messages were not normalized")
    if event_count < minimum_events:
        failures.append(f"event count {event_count} is below minimum {minimum_events}")
    if sequence_regressions:
        failures.append(f"source sequence regressed {sequence_regressions} times")
    if maximum_interarrival > maximum_silence_seconds:
        failures.append(
            f"maximum interarrival {maximum_interarrival:.6f}s exceeds {maximum_silence_seconds:.6f}s",
        )
    if tail_silence > maximum_silence_seconds:
        failures.append(f"tail silence {tail_silence:.6f}s exceeds {maximum_silence_seconds:.6f}s")
    channel = output.metrics
    utilization = channel.peak_depth / channel.capacity
    if channel.dropped:
        failures.append(f"bounded channel dropped {channel.dropped} events")
    if utilization > maximum_channel_utilization:
        failures.append(
            f"peak channel utilization {utilization:.6f} exceeds {maximum_channel_utilization:.6f}",
        )
    capture = getattr(service, "canonical_capture", None)
    capture_bytes = int(getattr(capture, "total_bytes", 0))
    if not capture_bytes:
        capture_path = getattr(capture, "path", None)
        capture_bytes = Path(capture_path).stat().st_size if capture_path and Path(capture_path).exists() else 0
    journal = getattr(service.session, "journal", None)
    raw_bytes = Path(journal).stat().st_size if journal and Path(journal).exists() else 0
    payload = {
        "source": "binance",
        "stream_id": service.stream_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "target_duration_seconds": duration_seconds,
        "actual_duration_seconds": (ended_at - started_at).total_seconds(),
        "event_count": event_count,
        "raw_message_count": int(getattr(service, "raw_messages", event_count)),
        "canonical_event_count": int(getattr(service, "canonical_events", event_count)),
        "ignored_message_count": ignored,
        "reconnect_count": service.reconnects,
        "first_source_sequence": first_source_sequence,
        "last_source_sequence": last.source_sequence if last is not None else None,
        "channel_capacity": channel.capacity,
        "peak_channel_depth": channel.peak_depth,
        "peak_channel_utilization": utilization,
        "channel_dropped": channel.dropped,
        "raw_journal_bytes": raw_bytes,
        "canonical_capture_bytes": capture_bytes,
        "capture_segment_count": int(getattr(capture, "segment_count", 1 if capture_bytes else 0)),
        "sequence_regressions": sequence_regressions,
        "maximum_interarrival_seconds": maximum_interarrival,
        "tail_silence_seconds": tail_silence,
        "minimum_events": minimum_events,
        "maximum_silence_seconds": maximum_silence_seconds,
        "maximum_channel_utilization": maximum_channel_utilization,
        "failures": tuple(failures),
        "passed": not failures,
    }
    material = json.dumps(to_primitive(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    audit_hash = sha256(material.encode()).hexdigest()
    target = Path(artifact_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {**payload, "audit_hash": audit_hash, "artifact": str(target)}
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(to_primitive(document), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(target)
    return MarketDataSoakResult(**document)


async def run_binance_market_restart_campaign(
    service_factory: Callable[[int], tuple[MarketSoakService, BoundedEventChannel[CanonicalEventEnvelope]]],
    *,
    stream_id: str,
    duration_seconds: float,
    restart_interval_seconds: float,
    minimum_events: int,
    maximum_silence_seconds: float,
    artifact_path: str | Path,
    maximum_channel_utilization: float = 0.9,
) -> MarketDataRestartCampaignResult:
    if duration_seconds <= 0 or restart_interval_seconds <= 0:
        raise ValueError("restart campaign durations must be positive")
    if duration_seconds <= restart_interval_seconds:
        raise ValueError("restart campaign duration must exceed restart interval")
    started_at = datetime.now(timezone.utc)
    results: list[MarketDataSoakResult] = []
    remaining = duration_seconds
    index = 1
    target = Path(artifact_path)
    while remaining > 0:
        leg_duration = min(restart_interval_seconds, remaining)
        service, output = service_factory(index)
        leg_artifact = target.with_name(f"{target.stem}.leg-{index:03d}{target.suffix}")
        results.append(await run_binance_market_soak(
            service, output, duration_seconds=leg_duration, minimum_events=1,
            maximum_silence_seconds=maximum_silence_seconds,
            artifact_path=leg_artifact,
            maximum_channel_utilization=maximum_channel_utilization,
        ))
        remaining -= leg_duration
        index += 1
    ended_at = datetime.now(timezone.utc)
    failures = [
        f"leg {index} failed: {'; '.join(result.failures)}"
        for index, result in enumerate(results, 1) if not result.passed
    ]
    event_count = sum(item.event_count for item in results)
    if event_count < minimum_events:
        failures.append(f"campaign event count {event_count} is below minimum {minimum_events}")
    boundary_regressions = sum(
        current.first_source_sequence is not None and previous.last_source_sequence is not None
        and current.first_source_sequence < previous.last_source_sequence
        for previous, current in zip(results, results[1:])
    )
    if boundary_regressions:
        failures.append(f"source sequence regressed across {boundary_regressions} restart boundaries")
    restart_passed = len(results) >= 2 and boundary_regressions == 0 and all(item.passed for item in results)
    if not restart_passed:
        failures.append("restart drill did not complete with continuous healthy legs")
    payload = {
        "source": "binance",
        "stream_id": stream_id,
        "target_duration_seconds": duration_seconds,
        "actual_duration_seconds": (ended_at - started_at).total_seconds(),
        "leg_count": len(results),
        "restart_count": max(0, len(results) - 1),
        "event_count": event_count,
        "reconnect_count": sum(item.reconnect_count for item in results),
        "boundary_sequence_regressions": boundary_regressions,
        "leg_artifacts": tuple(item.artifact for item in results),
        "leg_audit_hashes": tuple(item.audit_hash for item in results),
        "failures": tuple(failures),
        "restart_drill_passed": restart_passed,
        "passed": not failures,
    }
    material = json.dumps(to_primitive(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    audit_hash = sha256(material.encode()).hexdigest()
    document = {**payload, "audit_hash": audit_hash, "artifact": str(target)}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(
        to_primitive(document), ensure_ascii=False, indent=2, sort_keys=True,
    ) + "\n", encoding="utf-8")
    temporary.replace(target)
    return MarketDataRestartCampaignResult(**document)
