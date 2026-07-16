from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime

from trading.domain.identity import InstrumentId, VenueId
from trading.domain.instrument import InstrumentDefinition


class InstrumentCatalog:
    def __init__(self) -> None:
        self._definitions: dict[InstrumentId, list[InstrumentDefinition]] = defaultdict(list)

    def add(self, definition: InstrumentDefinition) -> None:
        versions = self._definitions[definition.instrument_id]
        for existing in versions:
            if _overlaps(existing.effective_from, existing.effective_to, definition.effective_from, definition.effective_to):
                raise ValueError(f"overlapping instrument definition: {definition.instrument_id}")
        versions.append(definition)
        versions.sort(key=lambda item: item.effective_from)

    def get(self, instrument_id: InstrumentId, at: datetime) -> InstrumentDefinition:
        matches = [item for item in self._definitions.get(instrument_id, ()) if item.active_at(at)]
        if len(matches) != 1:
            raise LookupError(f"instrument definition not found or ambiguous: {instrument_id} at {at}")
        return matches[0]

    def resolve(self, venue_id: VenueId, external_id: str, at: datetime) -> InstrumentDefinition:
        matches = []
        for versions in self._definitions.values():
            for definition in versions:
                if not definition.active_at(at):
                    continue
                if any(item.venue_id == venue_id and item.external_id == external_id and item.active_at(at) for item in definition.listings):
                    matches.append(definition)
        if len(matches) != 1:
            raise LookupError(f"venue instrument not found or ambiguous: {venue_id}/{external_id}")
        return matches[0]

    def definitions(self, at: datetime | None = None) -> tuple[InstrumentDefinition, ...]:
        values = [item for versions in self._definitions.values() for item in versions]
        if at is not None:
            values = [item for item in values if item.active_at(at)]
        return tuple(sorted(values, key=lambda item: (item.instrument_id.value, item.effective_from)))

    def supersede(self, definition: InstrumentDefinition, effective_at: datetime) -> None:
        current = self.get(definition.instrument_id, effective_at)
        if definition.effective_from != effective_at:
            raise ValueError("replacement definition must start at effective_at")
        versions = self._definitions[definition.instrument_id]
        versions[versions.index(current)] = replace(current, effective_to=effective_at)
        self.add(definition)

    def end_listing(self, instrument_id: InstrumentId, effective_at: datetime) -> None:
        current = self.get(instrument_id, effective_at)
        versions = self._definitions[instrument_id]
        versions[versions.index(current)] = replace(
            current, effective_to=effective_at,
            listings=tuple(replace(item, delisted_at=effective_at) for item in current.listings),
        )


def _overlaps(start_a, end_a, start_b, end_b) -> bool:
    latest_start = max(start_a, start_b)
    if end_a is None and end_b is None:
        return True
    earliest_end = end_b if end_a is None else end_a if end_b is None else min(end_a, end_b)
    return latest_start < earliest_end
