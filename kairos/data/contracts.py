from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class DatasetLayer(StrEnum):
    SOURCE = "source"
    CANONICAL = "canonical"
    CURATED = "curated"
    REFERENCE = "reference"
    FEATURES = "features"
    STUDIES = "studies"


class DatasetStorageKind(StrEnum):
    TABULAR = "tabular"
    MARKET_EVENTS = "market_events"
    MARKET_SNAPSHOTS = "market_snapshots"
    MARKET_SLICES = "market_slices"
    REFERENCE = "reference"


class DatasetStatus(StrEnum):
    DRAFT = "draft"
    REGISTERED = "registered"
    VALIDATING = "validating"
    VALIDATED = "validated"
    APPROVED_FOR_RESEARCH = "approved_for_research"
    APPROVED_FOR_BACKTEST = "approved_for_backtest"
    APPROVED_FOR_PRODUCTION = "approved_for_production"
    DEPRECATED = "deprecated"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class QualityLevel(StrEnum):
    ARCHIVED = "Q0"
    INTEGRITY = "Q1"
    RESEARCH = "Q2"
    BACKTEST = "Q3"
    PRODUCTION = "Q4"


class AcquirePolicy(StrEnum):
    NEVER = "never"
    PLAN = "plan"
    IF_MISSING = "if-missing"
    REFRESH = "refresh"


class DataView(StrEnum):
    RAW_AS_RECEIVED = "raw-as-received"
    CORRECTED_FINAL = "corrected-final"


class OutputFormat(StrEnum):
    ARROW = "arrow"
    POLARS = "polars"
    PANDAS = "pandas"
    ROWS = "rows"


class RunMode(StrEnum):
    RESEARCH = "research"
    BACKTEST = "backtest"
    HISTORICAL_SIMULATION = "historical-simulation"
    PAPER_TRADING = "paper-trading"
    LIVE_PAPER = "live-paper"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class DatasetKey:
    value: str

    def __post_init__(self) -> None:
        parts = self.value.split(".")
        if len(parts) < 2 or any(not part or not part.replace("_", "").replace("-", "").isalnum() for part in parts):
            raise ValueError(f"invalid logical dataset key: {self.value!r}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class FieldRef:
    name: str

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError(f"invalid field name: {self.name!r}")

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class SourceBinding:
    provider: str
    venue: str | None = None
    priority: int = 0
    quality_level: QualityLevel = QualityLevel.RESEARCH
    acquisition_modes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("source provider cannot be empty")


@dataclass(frozen=True, slots=True)
class DataProductDefinition:
    key: DatasetKey
    title: str
    layer: DatasetLayer
    description: str = ""
    dimensions: Mapping[str, str] = field(default_factory=dict)
    primary_time: str = "available_time"
    default_view: DataView = DataView.RAW_AS_RECEIVED
    sources: tuple[SourceBinding, ...] = ()
    owner: str | None = None
    source_policy_version: str = "priority-v1"

    def __post_init__(self) -> None:
        if not self.source_policy_version.strip():
            raise ValueError("dataset product source policy version cannot be empty")

    def __str__(self) -> str:
        return str(self.key)


@dataclass(frozen=True, slots=True)
class DataProductContract:
    """Complete logical, storage, quality, and usage contract for one data product."""

    product: DataProductDefinition
    relative_path: str
    schema_id: str
    capabilities: Mapping[str, object] = field(default_factory=dict)
    storage_kind: DatasetStorageKind = DatasetStorageKind.TABULAR
    layout_version: str = "1"
    quality_profile: str = "generic"
    minimum_publication_level: QualityLevel = QualityLevel.RESEARCH

    def __post_init__(self) -> None:
        path = self.relative_path.replace("\\", "/")
        if not path.strip() or path.startswith("/") or ".." in path.split("/"):
            raise ValueError("data product contract requires a safe lake-relative path")
        if not self.schema_id.strip() or not self.layout_version.strip() or not self.quality_profile.strip():
            raise ValueError("data product contract schema, layout, and quality profile cannot be empty")
        if self.product.primary_time.strip() == "":
            raise ValueError("data product contract requires a primary time field")

    @property
    def key(self) -> DatasetKey:
        return self.product.key


DatasetProductSpec = DataProductContract


@dataclass(frozen=True, slots=True)
class DatasetRelease:
    release_id: str
    product_key: DatasetKey
    release_version: str
    schema_id: str
    schema_version: str
    transform_id: str
    transform_version: str
    relative_path: str
    format: str
    content_hash: str | None = None
    provider: str | None = None
    venue: str | None = None
    aliases: tuple[str, ...] = ()
    status: DatasetStatus = DatasetStatus.APPROVED_FOR_RESEARCH
    quality_level: QualityLevel = QualityLevel.RESEARCH
    published_at: str | None = None
    storage_kind: DatasetStorageKind = DatasetStorageKind.TABULAR
    layout_version: str = "1"

    def __post_init__(self) -> None:
        for name in ("release_id", "release_version", "schema_id", "schema_version", "transform_id",
                     "transform_version", "relative_path", "format", "layout_version"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"dataset release {name} cannot be empty")


class CommonFields:
    AVAILABLE_TIME = FieldRef("available_time")
    EVENT_TIME = FieldRef("event_time")
    INSTRUMENT_ID = FieldRef("instrument_id")


class OptionQuoteFields:
    AVAILABLE_TIME = CommonFields.AVAILABLE_TIME
    EVENT_TIME = CommonFields.EVENT_TIME
    INSTRUMENT_ID = CommonFields.INSTRUMENT_ID
    BID = FieldRef("bid")
    ASK = FieldRef("ask")
    BID_SIZE = FieldRef("bid_size")
    ASK_SIZE = FieldRef("ask_size")
    TOP_OF_BOOK = (AVAILABLE_TIME, INSTRUMENT_ID, BID, ASK, BID_SIZE, ASK_SIZE)


DataProduct = DataProductDefinition
DatasetProduct = DataProductDefinition
DatasetLike = DataProductDefinition | DatasetKey | str
FieldLike = FieldRef | str
