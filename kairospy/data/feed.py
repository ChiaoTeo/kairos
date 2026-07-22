from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Iterable, Iterator

from kairospy.market.canonical import CanonicalEventEnvelope, canonicalize_market_event
from kairospy.identity import InstrumentId
from kairospy.market.source_events import MarketEventEnvelope, MarketEventType
from kairospy.market.repository import ParquetMarketEventRepository
from kairospy.market.snapshots import MarketReplayDataset, MarketSnapshot, MarketSnapshotReplayFeed

from .contracts import DataView, DatasetRelease


@dataclass(frozen=True, slots=True)
class ReplaySpec:
    release: DatasetRelease
    start: datetime
    end: datetime
    instruments: tuple[InstrumentId, ...] = ()
    event_types: tuple[MarketEventType, ...] = ()
    view: DataView = DataView.RAW_AS_RECEIVED

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise ValueError("replay requires timezone-aware [start,end) with start before end")
        if self.release.content_hash is None:
            raise ValueError("deterministic replay requires a release with a frozen content hash")


class ReplayEventFeed:
    """Deterministic event replay over one immutable release.

    Resolution happens before construction. Iteration never consults aliases, Catalog state,
    acquisition services, or wall-clock time.
    """

    def __init__(self, repository: ParquetMarketEventRepository, spec: ReplaySpec) -> None:
        self.repository, self.spec = repository, spec

    @property
    def release_id(self) -> str:
        return self.spec.release.release_id

    @property
    def content_hash(self) -> str:
        assert self.spec.release.content_hash is not None
        return self.spec.release.content_hash

    def __iter__(self) -> Iterator[MarketEventEnvelope]:
        previous = None
        for event in self.repository.scan(
            self.spec.release.release_id, self.spec.start, self.spec.end,
            instruments=self.spec.instruments or None, event_types=self.spec.event_types or None,
            view=self.spec.view.value,
        ):
            if previous is not None and event.event_key < previous:
                raise RuntimeError("market event repository produced a non-deterministic replay order")
            previous = event.event_key
            yield event

    async def events(self) -> AsyncIterator[CanonicalEventEnvelope]:
        """Expose the frozen release through the shared asynchronous runtime port."""

        for event in self:
            yield canonicalize_market_event(event, source_instance=f"release:{self.release_id}")


def replay_spec(release: DatasetRelease, start: datetime, end: datetime, *,
                instruments: Iterable[str | InstrumentId] = (),
                event_types: Iterable[str | MarketEventType] = (),
                view: DataView | str = DataView.RAW_AS_RECEIVED) -> ReplaySpec:
    return ReplaySpec(
        release, start, end,
        tuple(item if isinstance(item, InstrumentId) else InstrumentId(item) for item in instruments),
        tuple(item if isinstance(item, MarketEventType) else MarketEventType(item) for item in event_types),
        DataView(view),
    )


class ReplaySnapshotFeed:
    """Deterministic MarketSnapshot feed tied to a Catalog release and dataset hash."""

    def __init__(self, release: DatasetRelease, dataset: MarketReplayDataset) -> None:
        if release.content_hash is None:
            raise ValueError("deterministic replay requires a release with a frozen content hash")
        if dataset.manifest.dataset_id != release.release_id:
            raise ValueError("MarketReplayDataset identity does not match the frozen Catalog release")
        if dataset.manifest.content_hash != release.content_hash:
            raise ValueError("MarketReplayDataset content hash does not match the frozen Catalog release")
        self.release, self.dataset = release, dataset
        self._feed = MarketSnapshotReplayFeed(dataset)

    @property
    def release_id(self) -> str:
        return self.release.release_id

    @property
    def content_hash(self) -> str:
        assert self.release.content_hash is not None
        return self.release.content_hash

    def between(self, start: datetime, end: datetime) -> Iterator[MarketSnapshot]:
        yield from self._feed.between(start, end)
