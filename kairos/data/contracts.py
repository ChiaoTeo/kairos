from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
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
    STUDY = "study"
    RESEARCH = "study"
    BACKTEST = "backtest"
    HISTORICAL_SIMULATION = "historical-simulation"
    PAPER_TRADING = "paper-trading"
    LIVE = "live"

    @classmethod
    def _missing_(cls, value: object):
        if str(value) == "research":
            return cls.STUDY
        if str(value) == "paper":
            return cls.PAPER_TRADING
        return None


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


@dataclass(frozen=True, slots=True)
class DataSetContractArtifact:
    """Stable upper-layer contract evidence for a Data Product.

    This is intentionally narrower than DataProductContract: it keeps logical
    identity, schema, time, storage kind, and quality semantics, but excludes
    physical lake paths and connector details that Study/Strategy must not
    depend on.
    """

    dataset_id: str
    title: str
    layer: DatasetLayer
    primary_time: str
    schema_id: str
    storage_kind: DatasetStorageKind = DatasetStorageKind.TABULAR
    layout_version: str = "1"
    quality_profile: str = "generic"
    minimum_publication_level: QualityLevel = QualityLevel.RESEARCH
    capabilities: Mapping[str, object] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self) -> None:
        for name in ("dataset_id", "title", "primary_time", "schema_id", "layout_version", "quality_profile"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"DataSet Contract artifact {name} cannot be empty")
        DatasetKey(self.dataset_id)

    @classmethod
    def from_product_contract(cls, contract: DataProductContract) -> "DataSetContractArtifact":
        product = contract.product
        return cls(
            dataset_id=str(product.key),
            title=product.title,
            layer=product.layer,
            primary_time=product.primary_time,
            schema_id=contract.schema_id,
            storage_kind=contract.storage_kind,
            layout_version=contract.layout_version,
            quality_profile=contract.quality_profile,
            minimum_publication_level=contract.minimum_publication_level,
            capabilities=dict(contract.capabilities),
        )

    @property
    def contract_hash(self) -> str:
        return stable_artifact_hash(self.to_primitive())

    def to_primitive(self) -> dict[str, object]:
        return {
            "kind": "data_set_contract",
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "title": self.title,
            "layer": self.layer.value,
            "primary_time": self.primary_time,
            "schema_id": self.schema_id,
            "storage": {
                "kind": self.storage_kind.value,
                "layout_version": self.layout_version,
            },
            "quality": {
                "profile": self.quality_profile,
                "minimum_publication_level": self.minimum_publication_level.value,
            },
            "capabilities": dict(sorted(self.capabilities.items())),
        }


@dataclass(frozen=True, slots=True)
class DataReleaseManifest:
    dataset_id: str
    release_id: str
    contract_hash: str
    content_hash: str
    primary_time: str
    fields: tuple[str, ...] = ()
    quality_level: QualityLevel | None = None
    source: Mapping[str, object] = field(default_factory=dict)
    published_at: str = ""
    schema_version: int = 1

    def __post_init__(self) -> None:
        _validate_dataset_manifest_identity(self.dataset_id, self.release_id, self.contract_hash, self.content_hash, self.primary_time)

    @property
    def artifact_ref(self) -> str:
        return data_release_ref(self.dataset_id, self.release_id)

    @property
    def manifest_hash(self) -> str:
        return stable_artifact_hash(self.to_primitive())

    def to_primitive(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "product": "data",
            "kind": "data_release_manifest",
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "release_id": self.release_id,
            "contract_hash": self.contract_hash,
            "content_hash": self.content_hash,
            "primary_time": self.primary_time,
            "fields": list(self.fields),
            "source": dict(self.source),
            "published_at": self.published_at,
        }
        if self.quality_level is not None:
            payload["quality_level"] = self.quality_level.value
        return payload


@dataclass(frozen=True, slots=True)
class LiveViewManifest:
    dataset_id: str
    live_view_id: str
    contract_hash: str
    connector_hash: str
    primary_time: str
    fields: tuple[str, ...] = ()
    live_data_plane: Mapping[str, object] = field(default_factory=dict)
    source: Mapping[str, object] = field(default_factory=dict)
    freshness_status: str = "configured"
    published_at: str = ""
    schema_version: int = 1

    def __post_init__(self) -> None:
        _validate_dataset_manifest_identity(self.dataset_id, self.live_view_id, self.contract_hash, self.connector_hash, self.primary_time)
        if not self.freshness_status.strip():
            raise ValueError("Live View Manifest freshness_status cannot be empty")

    @property
    def artifact_ref(self) -> str:
        return f"data://{self.dataset_id}/live-views/{self.live_view_id}"

    @property
    def manifest_hash(self) -> str:
        return stable_artifact_hash(self.to_primitive())

    def to_primitive(self) -> dict[str, object]:
        return {
            "product": "data",
            "kind": "live_view_manifest",
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "live_view_id": self.live_view_id,
            "contract_hash": self.contract_hash,
            "connector_hash": self.connector_hash,
            "primary_time": self.primary_time,
            "fields": list(self.fields),
            "live_data_plane": dict(self.live_data_plane),
            "source": dict(self.source),
            "freshness_status": self.freshness_status,
            "published_at": self.published_at,
        }


def data_release_ref(dataset_id: str, release_id: str) -> str:
    DatasetKey(dataset_id)
    if not release_id.strip():
        raise ValueError("Data Release artifact ref requires release_id")
    return f"data://{dataset_id}/releases/{release_id}"


def stable_artifact_hash(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validate_dataset_manifest_identity(dataset_id: str, artifact_id: str, contract_hash: str, content_hash: str, primary_time: str) -> None:
    DatasetKey(dataset_id)
    for name, value in (
        ("artifact_id", artifact_id),
        ("contract_hash", contract_hash),
        ("content_hash", content_hash),
        ("primary_time", primary_time),
    ):
        if not value.strip():
            raise ValueError(f"Data manifest {name} cannot be empty")


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
DatasetLike = DataProductDefinition | DatasetKey | str
FieldLike = FieldRef | str
