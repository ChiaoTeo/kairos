from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT

from .acquisition.historical_service import HistoricalDataService
from .live.services import LiveDataService
from .storage.reader import DatasetReader
from .storage.store import DatasetStore
from .storage.writer import DatasetWriter


class DataApi:
    """Small code-facing API for the simplified Dataset store."""

    def __init__(self, root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.root = Path(root)
        self.store = DatasetStore(self.root)
        self.reader = DatasetReader(self.store)
        self.writer = DatasetWriter(self.store)
        self.historical = HistoricalDataService(self.root)
        self.live_service = LiveDataService(self.root)

    def read(self, dataset: object, **query: object):
        return self.reader.read(dataset, **query)

    def live(self, dataset: object, *, view: str = "default") -> Path:
        return self.store.live_path(dataset) / view

    def use(self, product: str, *, instruments: Iterable[object] = (), **selector: object) -> dict[str, object]:
        args = _service_args(product, instruments=instruments, **selector)
        return self.historical.use_builtin(args)

    def connect(self, product: str, *, instruments: Iterable[object] = (), **selector: object) -> dict[str, object]:
        args = _service_args(product, instruments=instruments, **selector)
        return self.live_service.connect(args)

    def alias(self, dataset: object, alias: object) -> Path:
        return self.store.alias(dataset, alias)

    def append(self, dataset: object, frame: object, **kwargs: object) -> tuple[Path, ...]:
        return self.writer.append(dataset, frame, **kwargs)

    def upsert(self, dataset: object, frame: object, *, key: Iterable[str], **kwargs: object) -> tuple[Path, ...]:
        return self.writer.upsert(dataset, frame, key=key, **kwargs)


def _service_args(product: str, *, instruments: Iterable[object], **selector: object) -> SimpleNamespace:
    values = dict(selector)
    values.setdefault("dry_run", False)
    values.setdefault("for_use", None)
    values.setdefault("as_dataset", None)
    values["key"] = product
    values["source"] = product
    values["instrument"] = [str(item) for item in instruments]
    return SimpleNamespace(**values)
