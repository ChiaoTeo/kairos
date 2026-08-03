from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MarketBasis = str
MarketDerivation = str


@dataclass(frozen=True, slots=True)
class MarketSelector:
    model: type
    attributes: tuple[str, ...] = ()
    subject_type: str = "market"
    interval: str | None = None
    depth: int | str | None = None
    basis: MarketBasis | None = None
    derivation: MarketDerivation = "direct"

    def __post_init__(self) -> None:
        if not isinstance(self.model, type):
            raise TypeError("market selector model must be a type")
        object.__setattr__(self, "attributes", tuple(_required_text(value, "market selector attribute") for value in self.attributes))
        object.__setattr__(self, "subject_type", _required_text(self.subject_type, "market selector subject_type"))
        object.__setattr__(self, "interval", _optional_text(self.interval, "market selector interval"))
        object.__setattr__(self, "basis", _optional_text(self.basis, "market selector basis"))
        object.__setattr__(self, "derivation", _required_text(self.derivation, "market selector derivation"))
        object.__setattr__(self, "depth", _depth(self.depth))

    @property
    def model_name(self) -> str:
        return self.model.__name__

    @property
    def key(self) -> str:
        parts = [self.model_name]
        if self.attributes:
            parts.append(".".join(self.attributes))
        if self.interval is not None:
            parts.append(f"interval={self.interval}")
        if self.depth is not None:
            parts.append(f"depth={self.depth}")
        if self.basis is not None:
            parts.append(f"basis={self.basis}")
        if self.derivation != "direct":
            parts.append(f"derivation={self.derivation}")
        return "|".join(parts)


class MarketSelectable:
    @classmethod
    def select(
        cls,
        *attributes: str,
        subject_type: str = "market",
        interval: str | None = None,
        depth: int | str | None = None,
        basis: str | None = None,
        derivation: str = "direct",
    ) -> MarketSelector:
        return MarketSelector(
            cls,
            attributes=tuple(attributes),
            subject_type=subject_type,
            interval=interval,
            depth=depth,
            basis=basis,
            derivation=derivation,
        )


def market_selector(
    value: type | MarketSelector,
    *,
    attributes: tuple[str, ...] = (),
    subject_type: str = "market",
    interval: str | None = None,
    depth: int | str | None = None,
    basis: str | None = None,
    derivation: str = "direct",
) -> MarketSelector:
    if isinstance(value, MarketSelector):
        if attributes or interval is not None or depth is not None or basis is not None or derivation != "direct":
            raise ValueError("cannot override an existing market selector")
        return value
    return MarketSelector(
        value,
        attributes=attributes,
        subject_type=subject_type,
        interval=interval,
        depth=depth,
        basis=basis,
        derivation=derivation,
    )


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _optional_text(value: object | None, label: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, label)


def _depth(value: object | None) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "full":
            return "full"
        value = int(text)
    if int(value) <= 0:
        raise ValueError("market selector depth must be positive")
    return int(value)


__all__ = ["MarketBasis", "MarketDerivation", "MarketSelectable", "MarketSelector", "market_selector"]
