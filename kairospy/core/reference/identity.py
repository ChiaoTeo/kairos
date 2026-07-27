from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True, order=True)
class ReferenceId:
    value: str

    def __post_init__(self) -> None:
        value = str(self.value).strip()
        if not value:
            raise ValueError(f"{type(self).__name__} cannot be empty")
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value


class EntityId(ReferenceId):
    pass


class AssetId(ReferenceId):
    pass


class InstrumentId(ReferenceId):
    pass


class ListingId(ReferenceId):
    pass


class MarketId(ReferenceId):
    pass


def reference_slug(value: object) -> str:
    text = str(value).strip().lower()
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text)).strip("_")


__all__ = [
    "AssetId",
    "EntityId",
    "InstrumentId",
    "ListingId",
    "MarketId",
    "ReferenceId",
    "reference_slug",
]
