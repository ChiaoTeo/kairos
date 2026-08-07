"""Public Reference control and query application boundary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..system import UnixRestClient


@dataclass(frozen=True, slots=True)
class ReferenceControlApplication:
    socket_path: Path

    def request(self, method: str, path: str) -> dict[str, Any]:
        return asyncio.run(UnixRestClient(self.socket_path).request(method, path))

    def status(self) -> dict[str, Any]:
        return self.request("GET", "/v1/health")

    def snapshot(self) -> dict[str, Any]:
        return self.request("GET", "/v1/snapshot")

    def refresh(self) -> dict[str, Any]:
        return self.request("POST", "/v1/refresh")

    def publish(self) -> dict[str, Any]:
        return self.request("POST", "/v1/publish")

    def add_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        import json
        return self.request("POST", "/v1/assets", json.dumps(asset, separators=(",", ":")).encode())

    def stop(self) -> dict[str, Any]:
        return self.request("POST", "/v1/stop")

    def markets(self, *, symbol: str | None = None, market_id: str | None = None,
                venue_id: str | None = None, active_only: bool = False) -> dict[str, Any] | list[Any]:
        query = []
        if symbol is not None:
            query.append(f"symbol={symbol}")
        if market_id is not None:
            query.append(f"market_id={market_id}")
        if venue_id is not None:
            query.append(f"venue_id={venue_id}")
        if active_only:
            query.append("active_only=true")
        suffix = "?" + "&".join(query) if query else ""
        return self.request("GET", f"/v1/markets{suffix}")

    def resolve_market(self, *, symbol: str | None = None, market_id: str | None = None,
                       venue_id: str | None = None) -> dict[str, Any]:
        query = []
        if symbol is not None:
            query.append(f"symbol={symbol}")
        if market_id is not None:
            query.append(f"market_id={market_id}")
        if venue_id is not None:
            query.append(f"venue_id={venue_id}")
        suffix = "?" + "&".join(query) if query else ""
        return self.request("GET", f"/v1/markets/resolve{suffix}")


from .cli import ReferenceCliApplication


__all__ = ["ReferenceCliApplication", "ReferenceControlApplication"]
