from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
from typing import Protocol

from kairospy.market.canonical import CanonicalEventEnvelope, MarketEventKind
from kairospy.identity import InstrumentId
from kairospy.market.types import Bar
from kairospy.market.projections import CanonicalBarSeriesProjection


class FactorQuality(StrEnum):
    WARMING_UP = "warming_up"
    READY = "ready"
    STALE = "stale"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class FactorSpec:
    factor_id: str
    version: str
    required_inputs: tuple[str, ...]
    parameters: tuple[tuple[str, str], ...]
    warmup_observations: int
    output_fields: tuple[str, ...]
    implementation: str
    implementation_hash: str

    def __post_init__(self) -> None:
        if not self.factor_id.strip() or not self.version.strip():
            raise ValueError("factor identity and version are required")
        if not self.required_inputs or not self.output_fields:
            raise ValueError("factor inputs and outputs are required")
        if self.warmup_observations < 1:
            raise ValueError("factor warmup must be positive")
        if not self.implementation.strip() or len(self.implementation_hash) != 64:
            raise ValueError("factor implementation and SHA-256 hash are required")

    @property
    def spec_hash(self) -> str:
        return _hash(asdict(self))


@dataclass(frozen=True, slots=True)
class FactorSnapshot:
    factor_id: str
    factor_version: str
    factor_spec_hash: str
    instrument_id: InstrumentId
    as_of: datetime
    values: tuple[tuple[str, Decimal | None], ...]
    observations: int
    quality: FactorQuality
    input_identity: str
    state_hash: str
    available_time: datetime | None = None

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("factor snapshot as_of must be timezone-aware")
        if self.available_time is None:
            object.__setattr__(self, "available_time", self.as_of)
        if self.available_time is not None and self.available_time.tzinfo is None:
            raise ValueError("factor snapshot available_time must be timezone-aware")
        if self.available_time is not None and self.available_time < self.as_of:
            raise ValueError("factor snapshot available_time cannot precede as_of")
        if self.observations < 0:
            raise ValueError("factor observation count cannot be negative")

    def get(self, name: str) -> Decimal | None:
        try:
            return dict(self.values)[name]
        except KeyError as error:
            raise LookupError(f"factor value is not available: {name}") from error


class FactorRuntime(Protocol):
    @property
    def spec(self) -> FactorSpec: ...

    def update(self, event: CanonicalEventEnvelope) -> FactorSnapshot | None: ...

    def snapshot(self) -> FactorSnapshot | None: ...

    def dump_state(self) -> dict[str, object]: ...

    def restore(self, state: dict[str, object]) -> None: ...


class FactorRegistry:
    """Immutable local registry for governed, runnable factor definitions."""

    def __init__(self, root: str | Path = "data/factors") -> None:
        self.root = Path(root)

    def register(self, spec: FactorSpec) -> Path:
        directory = self.root / spec.factor_id / spec.version
        directory.mkdir(parents=True, exist_ok=True)
        payload = {**_primitive(asdict(spec)), "factor_spec_hash": spec.spec_hash}
        target = directory / "factor_spec.json"
        if target.exists():
            current = json.loads(target.read_text(encoding="utf-8"))
            if current != payload:
                raise ValueError("registered factor version has different semantics")
        else:
            target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "factor_id": spec.factor_id,
            "version": spec.version,
            "factor_spec_hash": spec.spec_hash,
            "files": {"factor_spec.json": sha256(target.read_bytes()).hexdigest()},
        }
        (directory / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        return directory


def implementation_hash(path: str | Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def snapshots_hash(snapshots: tuple[FactorSnapshot, ...]) -> str:
    return _hash(snapshots)


def _hash(value: object) -> str:
    encoded = json.dumps(
        _primitive(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()
    return sha256(encoded).hexdigest()


def _primitive(value):
    if hasattr(value, "__dataclass_fields__"):
        return _primitive(asdict(value))
    if isinstance(value, dict):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_primitive(item) for item in value]
    if isinstance(value, (Decimal, datetime, InstrumentId, StrEnum)):
        return value.value if isinstance(value, (InstrumentId, StrEnum)) else str(value)
    return value


class CanonicalBarFactorRuntime:
    """Base helper that accepts only completed canonical Bar events."""

    def __init__(self) -> None:
        self._projection = CanonicalBarSeriesProjection()

    def _bar(self, event: CanonicalEventEnvelope) -> Bar | None:
        if event.kind is not MarketEventKind.BAR:
            return None
        return self._projection.apply(event)
