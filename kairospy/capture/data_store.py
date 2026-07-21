from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from kairospy.backtest.feed import MarketReplayDataset, build_manifest
from kairospy.data.market_snapshot_storage import MarketSnapshotStorageDriver
from kairospy.storage.codec import from_primitive, to_primitive


@dataclass(frozen=True, slots=True)
class CollectionSession:
    content_hash: str
    collected_at: datetime
    start: datetime
    end: datetime
    slice_count: int
    source: str
    synthetic: bool


@dataclass(frozen=True, slots=True)
class CollectionManifest:
    schema_version: int
    dataset_id: str
    sessions: tuple[CollectionSession, ...]

    @property
    def real_session_count(self) -> int:
        return sum(not item.synthetic for item in self.sessions)


class MarketSnapshotCollectionPublisher:
    """Append-only collection publisher over the internal MarketSnapshot storage driver."""

    def __init__(self, repository: MarketSnapshotStorageDriver) -> None:
        self.repository = repository

    def save_session(
        self,
        chunk: MarketReplayDataset,
        *,
        append: bool,
        collected_at: datetime | None = None,
    ) -> MarketReplayDataset:
        timestamp = collected_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise ValueError("collection timestamp must be timezone-aware")
        target = self.repository.root / chunk.manifest.dataset_id / "dataset.json"
        existing = self.repository.load(chunk.manifest.dataset_id) if append and target.exists() else None
        merged = merge_datasets(existing, chunk) if existing else chunk
        self.repository.save(merged)
        session = CollectionSession(
            chunk.manifest.content_hash, timestamp, chunk.manifest.start, chunk.manifest.end,
            chunk.manifest.slice_count, chunk.manifest.source, chunk.manifest.synthetic,
        )
        manifest = self.load_collection(chunk.manifest.dataset_id)
        sessions = manifest.sessions if append and manifest else ()
        if not any(item.content_hash == session.content_hash for item in sessions):
            sessions = (*sessions, session)
        self._save_collection(CollectionManifest(1, chunk.manifest.dataset_id, sessions))
        return merged

    def load_collection(self, dataset_id: str) -> CollectionManifest | None:
        path = self.repository.root / dataset_id / "collection.json"
        if not path.exists():
            return None
        return from_primitive(json.loads(path.read_text(encoding="utf-8")), CollectionManifest)

    def _save_collection(self, manifest: CollectionManifest) -> None:
        directory = self.repository.root / manifest.dataset_id
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "collection.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(to_primitive(manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(target)


def merge_datasets(existing: MarketReplayDataset, chunk: MarketReplayDataset) -> MarketReplayDataset:
    left, right = existing.manifest, chunk.manifest
    if left.dataset_id != right.dataset_id:
        raise ValueError("cannot merge different dataset IDs")
    if left.split != right.split or left.synthetic != right.synthetic:
        raise ValueError("dataset split and synthetic provenance must remain stable across append sessions")
    if left.sampling_seconds != right.sampling_seconds:
        raise ValueError("sampling interval cannot change during append")

    definitions = {}
    for item in (*existing.definitions, *chunk.definitions):
        key = (item.instrument_id, item.effective_from)
        previous = definitions.get(key)
        if previous is not None and previous != item:
            raise ValueError(f"conflicting instrument definition: {item.instrument_id}")
        definitions[key] = item
    ordered_definitions = tuple(sorted(definitions.values(), key=lambda item: (item.instrument_id.value, item.effective_from)))
    products = tuple(sorted(set((*existing.products, *chunk.products)), key=lambda item: (item.product_id.value, item.effective_from)))
    references = tuple(sorted(set((*existing.references, *chunk.references)), key=lambda item: (item.source_instrument_id.value, item.role.value, item.effective_from)))
    settlements = tuple(sorted(set((*existing.settlements, *chunk.settlements)), key=lambda item: (item.settlement_terms_id, item.effective_from)))

    contracts = {item.instrument_id: item for item in existing.contracts}
    for item in chunk.contracts:
        previous = contracts.get(item.instrument_id)
        if previous is None or item.settlement_confirmed or not previous.settlement_confirmed:
            contracts[item.instrument_id] = item
    ordered_contracts = tuple(sorted(contracts.values(), key=lambda item: item.instrument_id.value))

    slices = {(item.timestamp, item.sequence): item for item in existing.slices}
    for item in chunk.slices:
        key = (item.timestamp, item.sequence)
        previous = slices.get(key)
        if previous is not None and previous != item:
            raise ValueError(f"conflicting market slice/snapshot at {item.timestamp} sequence={item.sequence}")
        slices[key] = item
    ordered_slices = tuple(sorted(slices.values(), key=lambda item: (item.timestamp, item.sequence)))
    source = left.source if left.source == right.source else "+".join(sorted(set(left.source.split("+") + right.source.split("+"))))
    manifest = build_manifest(
        left.dataset_id, ordered_slices, ordered_contracts, ordered_definitions,
        sampling_seconds=left.sampling_seconds, source=source, market_data_type=left.market_data_type,
        code_version=right.code_version, split=left.split, synthetic=left.synthetic,
        products=products, references=references, settlements=settlements,
    )
    return MarketReplayDataset(
        manifest, ordered_slices, ordered_contracts, ordered_definitions, products, references, settlements,
    )
