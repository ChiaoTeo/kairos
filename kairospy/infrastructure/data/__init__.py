from __future__ import annotations

from .ids import DataId, normalize_alias, normalize_data_id
from .ingest import DataSink
from .partitioning import PartitionSpec, TimePartitionGrain
from .query import DataQuery, OutputFormat
from .store import DataRow, DataRowInput, DataStore, DataValue, DataWriteResult
from .streams import InMemoryStreamFeed, StreamFeed, StreamSubscription

DatasetId = DataId

__all__ = [
    "DataSink",
    "DataId",
    "DataQuery",
    "DataRow",
    "DataRowInput",
    "DataStore",
    "DataValue",
    "DataWriteResult",
    "DatasetId",
    "InMemoryStreamFeed",
    "OutputFormat",
    "PartitionSpec",
    "StreamFeed",
    "StreamSubscription",
    "TimePartitionGrain",
    "normalize_alias",
    "normalize_data_id",
]
