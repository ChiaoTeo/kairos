from __future__ import annotations

from dataclasses import dataclass
from kairos.domain.identity import InstitutionId


class _NormalizedId:
    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()
        if not value:
            raise ValueError(f"{type(self).__name__} cannot be empty")
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class EntityId(_NormalizedId):
    value: str


@dataclass(frozen=True, slots=True, order=True)
class BenchmarkId(_NormalizedId):
    value: str


@dataclass(frozen=True, slots=True, order=True)
class ProductId(_NormalizedId):
    value: str


@dataclass(frozen=True, slots=True, order=True)
class SeriesId(_NormalizedId):
    value: str


@dataclass(frozen=True, slots=True, order=True)
class ListingId(_NormalizedId):
    value: str


@dataclass(frozen=True, slots=True, order=True)
class ProviderId(_NormalizedId):
    value: str


@dataclass(frozen=True, slots=True, order=True)
class BrokerId(_NormalizedId):
    value: str


@dataclass(frozen=True, slots=True, order=True)
class RouteId(_NormalizedId):
    value: str


@dataclass(frozen=True, slots=True, order=True)
class CalendarId(_NormalizedId):
    value: str


@dataclass(frozen=True, slots=True, order=True)
class NetworkId(_NormalizedId):
    value: str


@dataclass(frozen=True, slots=True, order=True)
class NetworkAssetId(_NormalizedId):
    value: str


@dataclass(frozen=True, slots=True, order=True)
class RailId(_NormalizedId):
    value: str


@dataclass(frozen=True, slots=True, order=True)
class LocationId(_NormalizedId):
    value: str
