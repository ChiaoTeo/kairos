from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping


TIME_FIELD_CANDIDATES = (
    "available_time",
    "event_time",
    "timestamp",
    "time",
    "date",
    "period_start",
)


class DataNeedsTimeError(ValueError):
    def __init__(self, *, source_format: str, fields: tuple[str, ...] | list[str]) -> None:
        self.source_format = source_format
        self.fields = tuple(str(field) for field in fields)
        super().__init__(
            f"no time field detected in {source_format}; pass --time with one of: "
            + ", ".join(self.fields)
        )

    def to_payload(self, *, dataset_id: str | None = None, source: str | Path | None = None) -> dict[str, object]:
        return {
            "product": "data",
            "operation": "add",
            "status": "needs_time",
            **({"dataset": dataset_id} if dataset_id else {}),
            **({"source": str(source)} if source is not None else {}),
            "detected_fields": list(self.fields),
            "issues": [{
                "code": "time_field_not_detected",
                "message": "No default time field was detected.",
                "why": "Data needs one primary time field before it can become a Dataset.",
            }],
            "required": ["--time"],
            "example": "--time " + (self.fields[0] if self.fields else "<field>"),
        }


@dataclass(frozen=True, slots=True)
class FieldMetadata:
    name: str
    inferred_type: str = "string"


@dataclass(frozen=True, slots=True)
class DatasetMetadata:
    dataset_id: str
    primary_time: str
    fields: tuple[FieldMetadata, ...]
    grain: Literal["time_series", "panel", "event"] = "time_series"
    source_kind: Literal["built_in", "user_defined"] = "user_defined"
    source_summary: Mapping[str, object] | None = None
    quality_profile: str = "generic"
    freshness_policy: str | None = None

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields)

    def to_contract(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "primary_time": self.primary_time,
            "fields": list(self.field_names),
            "metadata": {
                "grain": self.grain,
                "source_kind": self.source_kind,
                "source_summary": dict(self.source_summary or {}),
                "quality_profile": self.quality_profile,
                "freshness_policy": self.freshness_policy,
            },
        }


class DatasetMetadataInference:
    def infer_file(
        self,
        path: str | Path,
        *,
        dataset_id: str,
        time_field: str | None = None,
        source_kind: Literal["built_in", "user_defined"] = "user_defined",
    ) -> DatasetMetadata:
        source = Path(path)
        if source.suffix.lower() == ".parquet":
            return self.infer_parquet(source, dataset_id=dataset_id, time_field=time_field, source_kind=source_kind)
        return self.infer_csv(source, dataset_id=dataset_id, time_field=time_field, source_kind=source_kind)

    def infer_csv(
        self,
        path: str | Path,
        *,
        dataset_id: str,
        time_field: str | None = None,
        source_kind: Literal["built_in", "user_defined"] = "user_defined",
    ) -> DatasetMetadata:
        source = Path(path)
        with source.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            try:
                header = [str(item).strip() for item in next(reader)]
            except StopIteration as error:
                raise ValueError("CSV file is empty") from error
        if not header or any(not item for item in header):
            raise ValueError("CSV header fields must be non-empty")
        primary_time = time_field or self.detect_time_field(header)
        if primary_time is None:
            raise DataNeedsTimeError(source_format="CSV", fields=tuple(header))
        if primary_time not in header:
            raise ValueError(f"time field {primary_time!r} is not present in CSV header")
        grain = "panel" if any(name in header for name in ("instrument_id", "symbol", "asset_id")) else "time_series"
        return DatasetMetadata(
            dataset_id=dataset_id,
            primary_time=primary_time,
            fields=tuple(FieldMetadata(name) for name in header),
            grain=grain,
            source_kind=source_kind,
            source_summary={"kind": "file", "name": source.name},
        )

    def infer_parquet(
        self,
        path: str | Path,
        *,
        dataset_id: str,
        time_field: str | None = None,
        source_kind: Literal["built_in", "user_defined"] = "user_defined",
    ) -> DatasetMetadata:
        source = Path(path)
        try:
            import pyarrow.parquet as pq
        except ImportError as error:
            raise RuntimeError("Parquet data add requires pyarrow") from error
        fields = [str(name).strip() for name in pq.read_schema(source).names]
        if not fields or any(not item for item in fields):
            raise ValueError("Parquet schema fields must be non-empty")
        primary_time = time_field or self.detect_time_field(fields)
        if primary_time is None:
            raise DataNeedsTimeError(source_format="Parquet", fields=tuple(fields))
        if primary_time not in fields:
            raise ValueError(f"time field {primary_time!r} is not present in Parquet schema")
        grain = "panel" if any(name in fields for name in ("instrument_id", "symbol", "asset_id")) else "time_series"
        return DatasetMetadata(
            dataset_id=dataset_id,
            primary_time=primary_time,
            fields=tuple(FieldMetadata(name) for name in fields),
            grain=grain,
            source_kind=source_kind,
            source_summary={"kind": "file", "name": source.name, "format": "parquet"},
        )

    @staticmethod
    def detect_time_field(fields: tuple[str, ...] | list[str]) -> str | None:
        by_lower = {field.lower(): field for field in fields}
        for candidate in TIME_FIELD_CANDIDATES:
            if candidate in by_lower:
                return by_lower[candidate]
        return None
