"""Reference application resource ports."""

from __future__ import annotations

from typing import Protocol


class ReferenceCommandResources(Protocol):
    def reference_store(self, root: str | None) -> object: ...
    def public_market_access(self, exchange_name: object, driver_name: object, *, product: object = ...) -> object: ...
    def provider(self, provider_name: object, driver_name: object) -> object: ...
    def reference_access(self, source_kind: str, source_name: str, *, market: str | None, driver_name: object) -> object: ...


__all__ = ["ReferenceCommandResources"]
