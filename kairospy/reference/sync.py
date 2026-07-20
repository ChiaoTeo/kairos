from __future__ import annotations

from dataclasses import dataclass

from .catalog import ReferenceCatalog


@dataclass(frozen=True, slots=True)
class ReferenceSyncResult:
    definitions_received: int
    instruments_added: int
    listings_added: int
    mappings_added: int
    issues: tuple[str, ...] = ()


class ReferenceSyncService:
    """Merge normalized reference client facts into the authoritative catalog."""

    def __init__(self, catalog: ReferenceCatalog) -> None:
        self.catalog = catalog

    def sync(self, reference_client, request) -> ReferenceSyncResult:
        published = reference_client.sync(request)
        before = (len(self.catalog.instruments.values()), len(self.catalog.listings.values()), len(self.catalog.mappings()))
        self.catalog.merge(published)
        after = (len(self.catalog.instruments.values()), len(self.catalog.listings.values()), len(self.catalog.mappings()))
        return ReferenceSyncResult(len(published.instruments.values()), after[0] - before[0], after[1] - before[1], after[2] - before[2])
