from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

from trading.domain.identity import InstrumentId
from trading.storage.codec import from_primitive, to_primitive


@dataclass(frozen=True, slots=True)
class ExternalInstrumentMapping:
    provider: str
    source_namespace: str
    external_instrument_id: str
    internal_instrument_id: InstrumentId
    effective_from: datetime
    effective_to: datetime | None = None
    publisher_id: str | None = None

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.source_namespace.strip() or not self.external_instrument_id.strip():
            raise ValueError("external mapping identity fields cannot be empty")
        if self.effective_from.tzinfo is None or self.effective_to is not None and self.effective_to.tzinfo is None:
            raise ValueError("external mapping timestamps must be timezone-aware")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("external mapping effective_to must be after effective_from")

    def active_at(self, timestamp: datetime) -> bool:
        return self.effective_from <= timestamp and (self.effective_to is None or timestamp < self.effective_to)


class ExternalMappingRepository:
    def __init__(self, path: str | Path = "data/reference/external_mappings.json") -> None:
        self.path = Path(path)
        self._mappings: list[ExternalInstrumentMapping] = []
        if self.path.exists():
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if value.get("schema_version") != 1:
                raise ValueError("unsupported external mapping schema version")
            self._mappings = [from_primitive(item, ExternalInstrumentMapping) for item in value["mappings"]]

    def add(self, mapping: ExternalInstrumentMapping) -> None:
        for existing in self._mappings:
            same_external = existing.provider == mapping.provider and existing.source_namespace == mapping.source_namespace and existing.external_instrument_id == mapping.external_instrument_id and existing.publisher_id == mapping.publisher_id
            if same_external and _overlaps(existing, mapping):
                if existing == mapping:
                    return
                raise ValueError(f"conflicting external mapping: {mapping.provider}/{mapping.external_instrument_id}")
        self._mappings.append(mapping)
        self._mappings.sort(key=lambda item: (item.provider, item.source_namespace, item.external_instrument_id, item.effective_from))

    def resolve(self, provider: str, source_namespace: str, external_id: str, at: datetime, *, publisher_id: str | None = None) -> InstrumentId:
        matches = [item for item in self._mappings if item.provider == provider and item.source_namespace == source_namespace and item.external_instrument_id == external_id and item.publisher_id == publisher_id and item.active_at(at)]
        if len(matches) != 1:
            raise LookupError(f"external instrument not found or ambiguous: {provider}/{source_namespace}/{external_id} at {at}")
        return matches[0].internal_instrument_id

    def mappings(self) -> tuple[ExternalInstrumentMapping, ...]:
        return tuple(self._mappings)

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps({"schema_version": 1, "mappings": to_primitive(self._mappings)}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return self.path


def _overlaps(left: ExternalInstrumentMapping, right: ExternalInstrumentMapping) -> bool:
    latest_start = max(left.effective_from, right.effective_from)
    if left.effective_to is None and right.effective_to is None:
        return True
    earliest_end = right.effective_to if left.effective_to is None else left.effective_to if right.effective_to is None else min(left.effective_to, right.effective_to)
    return latest_start < earliest_end
