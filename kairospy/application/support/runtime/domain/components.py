from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeComponents:
    """Already-composed roles consumed by the runtime kernel."""

    market: object | None = None
    account: object | None = None
    account_catalog: object | None = None
    execution: object | None = None
    reference: object | None = None


__all__ = [
    "RuntimeComponents",
]
