from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import AsyncIterator

from kairospy.contracts import (
    BarPayload, CanonicalEventEnvelope, FundingRatePayload, GenericMarketPayload, MarketEventKind,
    OpenInterestPayload, OrderBookDeltaPayload, OrderBookSnapshotPayload, PricePayload, QuotePayload,
    TradePayload,
)
from kairospy.storage.codec import from_primitive, to_primitive


@dataclass(frozen=True, slots=True)
class CanonicalCaptureManifest:
    session_id: str
    source: str
    event_count: int
    first_available_time: datetime | None
    last_available_time: datetime | None
    content_sha256: str
    event_path: str
    finalized_at: datetime


class CaptureResourceExceeded(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RotatingCanonicalCaptureManifest:
    session_id: str
    source: str
    segment_count: int
    event_count: int
    total_bytes: int
    first_available_time: datetime | None
    last_available_time: datetime | None
    content_sha256: str
    segments: tuple[CanonicalCaptureManifest, ...]
    finalized_at: datetime


class CanonicalCaptureWriter:
    """Append-only canonical session evidence suitable for replay publication."""

    def __init__(self, path: str | Path, *, session_id: str, source: str) -> None:
        if not session_id.strip() or not source.strip():
            raise ValueError("canonical capture session and source cannot be empty")
        self.path = Path(path)
        self.session_id = session_id
        self.source = source
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            raise FileExistsError(f"canonical capture already exists: {self.path}")
        self._digest = sha256()
        self._ids: set[str] = set()
        self._count = 0
        self._first: datetime | None = None
        self._last: datetime | None = None
        self._finalized: CanonicalCaptureManifest | None = None

    def append(self, event: CanonicalEventEnvelope) -> None:
        if self._finalized is not None:
            raise RuntimeError("canonical capture is already finalized")
        identity = str(event.message_id)
        if identity in self._ids:
            raise ValueError(f"duplicate canonical event in capture session: {identity}")
        if self._last is not None and event.available_time < self._last:
            raise ValueError("canonical capture events must be ordered by available time")
        encoded = json.dumps(
            to_primitive(event), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()
        with self.path.open("ab") as handle:
            handle.write(encoded + b"\n")
        self._digest.update(encoded + b"\n")
        self._ids.add(identity)
        self._count += 1
        self._first = self._first or event.available_time
        self._last = event.available_time

    def finalize(self) -> CanonicalCaptureManifest:
        if self._finalized is not None:
            return self._finalized
        manifest = CanonicalCaptureManifest(
            self.session_id, self.source, self._count, self._first, self._last,
            self._digest.hexdigest(), str(self.path), datetime.now(timezone.utc),
        )
        target = self.path.with_suffix(self.path.suffix + ".manifest.json")
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(
            to_primitive(manifest), ensure_ascii=False, indent=2, sort_keys=True,
        ) + "\n", encoding="utf-8")
        temporary.replace(target)
        self._finalized = manifest
        return manifest


class CapturedCanonicalEventSource:
    """Replay an immutable canonical JSONL session through the live EventSource port."""

    _PAYLOAD_TYPES = {
        MarketEventKind.QUOTE: QuotePayload,
        MarketEventKind.TRADE: TradePayload,
        MarketEventKind.BAR: BarPayload,
        MarketEventKind.ORDER_BOOK_DELTA: OrderBookDeltaPayload,
        MarketEventKind.ORDER_BOOK_SNAPSHOT: OrderBookSnapshotPayload,
        MarketEventKind.MARK_PRICE: PricePayload,
        MarketEventKind.INDEX_PRICE: PricePayload,
        MarketEventKind.FUNDING_RATE: FundingRatePayload,
        MarketEventKind.OPEN_INTEREST: OpenInterestPayload,
    }

    def __init__(self, path: str | Path, *, verify_manifest: bool = True) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        if verify_manifest:
            manifest_path = self.path.with_suffix(self.path.suffix + ".manifest.json")
            if not manifest_path.exists():
                raise FileNotFoundError(manifest_path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            digest = sha256(self.path.read_bytes()).hexdigest()
            if digest != manifest["content_sha256"]:
                raise ValueError("canonical capture content hash does not match manifest")

    async def events(self) -> AsyncIterator[CanonicalEventEnvelope]:
        previous = None
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = json.loads(line)
                kind = MarketEventKind(raw["kind"])
                payload_type = self._PAYLOAD_TYPES.get(kind, GenericMarketPayload)
                payload = from_primitive(raw["payload"], payload_type)
                event = from_primitive({**raw, "payload": payload}, CanonicalEventEnvelope)
                if previous is not None and event.event_key < previous:
                    raise ValueError("canonical capture replay order is not deterministic")
                previous = event.event_key
                yield event


class RotatingCanonicalCaptureWriter:
    """Bounded segmented capture suitable for multi-day runtime sessions."""

    def __init__(
        self,
        path: str | Path,
        *,
        session_id: str,
        source: str,
        maximum_segment_events: int = 100_000,
        maximum_segment_bytes: int = 256 * 1024 * 1024,
        maximum_total_bytes: int | None = None,
    ) -> None:
        if maximum_segment_events < 1 or maximum_segment_bytes < 1:
            raise ValueError("capture segment limits must be positive")
        if maximum_total_bytes is not None and maximum_total_bytes < maximum_segment_bytes:
            raise ValueError("capture total byte budget cannot be smaller than one segment")
        self.path = Path(path)
        self.session_id = session_id
        self.source = source
        self.maximum_segment_events = maximum_segment_events
        self.maximum_segment_bytes = maximum_segment_bytes
        self.maximum_total_bytes = maximum_total_bytes
        self._segments: list[CanonicalCaptureManifest] = []
        self._writer: CanonicalCaptureWriter | None = None
        self._segment_events = 0
        self._total_events = 0
        self._total_bytes = 0
        self._finalized: RotatingCanonicalCaptureManifest | None = None

    def append(self, event: CanonicalEventEnvelope) -> None:
        if self._finalized is not None:
            raise RuntimeError("rotating canonical capture is already finalized")
        if self._writer is None:
            self._open_segment()
        elif (self._segment_events >= self.maximum_segment_events
              or self._writer.path.stat().st_size >= self.maximum_segment_bytes):
            self._close_segment()
            self._open_segment()
        assert self._writer is not None
        before = self._writer.path.stat().st_size if self._writer.path.exists() else 0
        self._writer.append(event)
        after = self._writer.path.stat().st_size
        self._segment_events += 1
        self._total_events += 1
        self._total_bytes += after - before
        if self.maximum_total_bytes is not None and self._total_bytes > self.maximum_total_bytes:
            raise CaptureResourceExceeded(
                f"canonical capture exceeded total byte budget {self.maximum_total_bytes}",
            )

    def finalize(self) -> RotatingCanonicalCaptureManifest:
        if self._finalized is not None:
            return self._finalized
        if self._writer is not None:
            self._close_segment()
        first = next((item.first_available_time for item in self._segments if item.first_available_time), None)
        last = next((item.last_available_time for item in reversed(self._segments) if item.last_available_time), None)
        material = [{
            "index": index,
            "event_count": item.event_count,
            "first_available_time": item.first_available_time,
            "last_available_time": item.last_available_time,
            "content_sha256": item.content_sha256,
        } for index, item in enumerate(self._segments, 1)]
        digest = sha256(json.dumps(
            to_primitive(material), sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        manifest = RotatingCanonicalCaptureManifest(
            self.session_id, self.source, len(self._segments), self._total_events,
            self._total_bytes, first, last, digest, tuple(self._segments), datetime.now(timezone.utc),
        )
        target = self.manifest_path
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(
            to_primitive(manifest), ensure_ascii=False, indent=2, sort_keys=True,
        ) + "\n", encoding="utf-8")
        temporary.replace(target)
        self._finalized = manifest
        return manifest

    @property
    def manifest_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".rotation.manifest.json")

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def segment_count(self) -> int:
        return len(self._segments) + (1 if self._writer is not None else 0)

    def _open_segment(self) -> None:
        index = len(self._segments) + 1
        suffix = "".join(self.path.suffixes) or ".jsonl"
        stem = self.path.name[:-len(suffix)] if suffix else self.path.name
        segment = self.path.with_name(f"{stem}.{index:06d}{suffix}")
        self._writer = CanonicalCaptureWriter(
            segment, session_id=f"{self.session_id}:segment:{index:06d}", source=self.source,
        )
        self._segment_events = 0

    def _close_segment(self) -> None:
        assert self._writer is not None
        self._segments.append(self._writer.finalize())
        self._writer = None
        self._segment_events = 0


class RotatingCapturedCanonicalEventSource:
    """Verify and replay all segments from a rotating capture manifest."""

    def __init__(self, manifest_path: str | Path) -> None:
        self.manifest_path = Path(manifest_path)
        raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        material = [{
            "index": index,
            "event_count": item["event_count"],
            "first_available_time": item["first_available_time"],
            "last_available_time": item["last_available_time"],
            "content_sha256": item["content_sha256"],
        } for index, item in enumerate(raw["segments"], 1)]
        digest = sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if digest != raw["content_sha256"]:
            raise ValueError("rotating canonical capture manifest hash does not match")
        self._segments = tuple(Path(item["event_path"]) for item in raw["segments"])
        self.event_count = int(raw["event_count"])

    async def events(self) -> AsyncIterator[CanonicalEventEnvelope]:
        previous = None
        ids: set[str] = set()
        count = 0
        for path in self._segments:
            async for event in CapturedCanonicalEventSource(path).events():
                if previous is not None and event.event_key < previous:
                    raise ValueError("rotating canonical capture segments are not globally ordered")
                identity = str(event.message_id)
                if identity in ids:
                    raise ValueError("duplicate canonical event across capture segments")
                ids.add(identity)
                previous = event.event_key
                count += 1
                yield event
        if count != self.event_count:
            raise ValueError("rotating canonical capture event count does not match manifest")
