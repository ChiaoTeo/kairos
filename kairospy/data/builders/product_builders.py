from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol


@dataclass(frozen=True, slots=True)
class ProductSourceBinding:
    product_key: str
    provider: str
    service: str
    resource: str
    venue: str | None = None
    params: Mapping[str, object] = field(default_factory=dict)
    universe_policy: str = "explicit"
    codec: str | None = None

    def __post_init__(self) -> None:
        for name in ("product_key", "provider", "service", "resource", "universe_policy"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"product source binding {name} cannot be empty")


@dataclass(frozen=True, slots=True)
class DatasetBuildResult:
    dataset_id: str
    status: str
    build_id: str | None = None
    snapshot_id: str | None = None
    coverage: Mapping[str, object] = field(default_factory=dict)
    quality: Mapping[str, object] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.dataset_id.strip() or not self.status.strip():
            raise ValueError("dataset build result requires dataset_id and status")


class DataProductBuilder(Protocol):
    provider: str

    def supports(self, product_key: str) -> bool:
        ...

    def acquire(self, request: object) -> object:
        ...

    def estimate(self, request: object) -> object:
        ...


class DataProductBuilderRegistry:
    def __init__(self) -> None:
        self._builders: dict[str, list[DataProductBuilder]] = {}

    def register(self, builder: DataProductBuilder) -> None:
        provider = builder.provider.strip()
        if not provider:
            raise ValueError("data product builder must declare a provider")
        values = self._builders.setdefault(provider, [])
        if builder not in values:
            values.append(builder)

    def builders(self, provider: str | None = None) -> tuple[DataProductBuilder, ...]:
        if provider is not None:
            return tuple(self._builders.get(provider, ()))
        values = []
        for provider_builders in self._builders.values():
            values.extend(provider_builders)
        return tuple(values)
