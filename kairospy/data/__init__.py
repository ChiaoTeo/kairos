from __future__ import annotations

from .ids import DataId, normalize_alias, normalize_data_id
from .ingest import DataSink
from .query import DataQuery, OutputFormat
from .store import DataStore
from .streams import InMemoryStreamFeed, StreamFeed, StreamSubscription

DatasetId = DataId

__all__ = [
    "DataSink",
    "DataId",
    "DataQuery",
    "DataStore",
    "DatasetId",
    "InMemoryStreamFeed",
    "OutputFormat",
    "StreamFeed",
    "StreamSubscription",
    "normalize_alias",
    "normalize_data_id",
]
