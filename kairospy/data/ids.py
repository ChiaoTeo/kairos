from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DatasetId:
    """Canonical logical Dataset identity.

    A Dataset ID identifies the data asset, not a run mode, provider credential,
    acquisition request, or physical partition layout.
    """

    value: str

    def __post_init__(self) -> None:
        text = self.value.strip()
        if text != self.value or not text:
            raise ValueError(f"invalid Dataset ID: {self.value!r}")
        parts = text.split(".")
        if len(parts) < 2:
            raise ValueError(f"invalid Dataset ID: {self.value!r}")
        for part in parts:
            if not _valid_segment(part):
                raise ValueError(f"invalid Dataset ID segment {part!r} in {self.value!r}")

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(self.value.split("."))

    def __str__(self) -> str:
        return self.value


def normalize_dataset_id(value: object) -> DatasetId:
    if isinstance(value, DatasetId):
        return value
    key = getattr(value, "key", None)
    if key is not None:
        return DatasetId(str(key))
    return DatasetId(str(value))


def normalize_alias(value: object) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("dataset alias cannot be empty")
    if "/" in text or "\\" in text or text in {".", ".."}:
        raise ValueError(f"invalid dataset alias: {text!r}")
    if any(part in {"", ".", ".."} for part in text.split(".")):
        raise ValueError(f"invalid dataset alias: {text!r}")
    return text


def _valid_segment(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    return all(character.isalnum() or character in {"_", "-"} for character in value)

