"""Application commands for local market dataset administration."""

from __future__ import annotations

from .resources import MarketCommandResources, StorageFormat


class MarketDatasetCommandService:
    def __init__(self, resources: MarketCommandResources) -> None:
        self._resources = resources

    def list(self, *, root: str | None, storage_format: StorageFormat | None) -> object:
        return self._resources.list_datasets(root, storage_format=storage_format or StorageFormat.parquet)

    def inspect(self, dataset: str, *, root: str | None, storage_format: StorageFormat | None, sample: int) -> object:
        return self._resources.inspect_dataset(dataset, root, storage_format=storage_format or StorageFormat.parquet, sample=sample)

    def alias(self, dataset: str, alias: str, *, root: str | None, storage_format: StorageFormat | None) -> object:
        return self._resources.alias_dataset(dataset, alias, root, storage_format=storage_format or StorageFormat.parquet)

    def prune(self, dataset: str, start: str, end: str, *, root: str | None, storage_format: StorageFormat | None) -> object:
        return self._resources.prune_dataset(dataset, start, end, root, storage_format=storage_format or StorageFormat.parquet)


__all__ = ["MarketDatasetCommandService"]
