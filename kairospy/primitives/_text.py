from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextValue:
    """Validated base for opaque text values.

    Boundary adapters may normalize external input before construction. The
    semantic value itself rejects surrounding whitespace so invalid input is
    never changed silently.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError(f"{type(self).__name__} must be constructed from text")
        if not self.value:
            raise ValueError(f"{type(self).__name__} cannot be empty")
        if self.value.strip() != self.value:
            raise ValueError(
                f"{type(self).__name__} cannot contain surrounding whitespace"
            )

    def __str__(self) -> str:
        return self.value
