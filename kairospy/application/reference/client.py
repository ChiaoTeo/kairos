"""Public client for the running Reference process."""

from __future__ import annotations

import json
import mmap
import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode
from typing import Any


@dataclass(frozen=True, slots=True)
class ReferenceSnapshotClient:
    """Read Reference snapshots from mmap and use its socket for queries."""

    socket_path: Path | None = None
    snapshot_path: Path | None = None
    markets_snapshot_path: Path | None = None
    timeout: float = 5.0

    _MAGIC = b"KSS1"
    _HEADER_SIZE = 64
    _SLOT_COUNT = 2

    def request(self, path: str, *, method: str = "GET", **params: object) -> Any:
        if self.socket_path is None:
            raise RuntimeError("Reference control socket is not configured")
        query = urlencode({key: str(value).lower() if isinstance(value, bool) else str(value)
                           for key, value in params.items() if value is not None})
        target = f"{path}?{query}" if query else path
        request = (
            f"{method} {target} HTTP/1.1\r\n"
            "Host: reference\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(self.timeout)
            try:
                client.connect(str(self.socket_path))
            except OSError as error:
                raise RuntimeError(
                    f"cannot connect to Reference at {self.socket_path}; "
                    "is the reference process running?"
                ) from error
            client.sendall(request)
            response = bytearray()
            while True:
                chunk = client.recv(64 * 1024)
                if not chunk:
                    break
                response.extend(chunk)

        header, separator, body = bytes(response).partition(b"\r\n\r\n")
        if not separator:
            raise RuntimeError("Reference returned an invalid HTTP response")
        status_line = header.splitlines()[0].decode("ascii", errors="replace")
        try:
            status = int(status_line.split()[1])
            value = json.loads(body)
        except (IndexError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("Reference returned an invalid JSON response") from error
        if status >= 400:
            message = value.get("error", status_line) if isinstance(value, dict) else status_line
            raise RuntimeError(str(message))
        return value

    def health(self) -> dict[str, Any]:
        return self.request("/v1/health")

    def providers(self) -> dict[str, Any]:
        return self.request("/v1/providers")

    def refresh(self) -> dict[str, Any]:
        return self.request("/v1/refresh", method="POST")

    def snapshot(self) -> dict[str, Any]:
        if self.snapshot_path is not None:
            return self._read_catalog_snapshot()
        raise RuntimeError("Reference catalog snapshot path is not configured")

    def _read_payload(self, path: Path) -> tuple[bytes, int]:
        with path.open("rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
            if len(mapped) < self._HEADER_SIZE or mapped[:4] != self._MAGIC:
                raise RuntimeError(f"invalid Reference shared snapshot: {path}")
            version, slots, slot_size = struct.unpack_from("<HHI", mapped, 4)
            if version != 1 or slots != self._SLOT_COUNT or slot_size <= 0:
                raise RuntimeError(f"unsupported Reference shared snapshot: {path}")
            if len(mapped) < self._HEADER_SIZE + slots * slot_size:
                raise RuntimeError(f"truncated Reference shared snapshot: {path}")
            for _ in range(8):
                active = mapped[12]
                generation = struct.unpack_from("<Q", mapped, 32 + active * 8)[0]
                length = struct.unpack_from("<I", mapped, 24 + active * 4)[0]
                start = self._HEADER_SIZE + active * slot_size
                payload = bytes(mapped[start:start + length])
                if active == mapped[12] and generation == struct.unpack_from("<Q", mapped, 32 + active * 8)[0]:
                    return payload, generation
        raise RuntimeError("Reference shared snapshot changed while being read")

    @staticmethod
    def _text(value: bytes | None) -> str | None:
        return None if value is None else value.decode("utf-8")

    def _read_catalog_snapshot(self) -> dict[str, Any]:
        payload, generation = self._read_payload(Path(self.snapshot_path))
        from kairospy.infrastructure.transport.generated.kairos.reference.v1.CatalogSnapshot import CatalogSnapshot

        if payload[4:8] != b"PRC1":
            raise RuntimeError("invalid Reference catalog snapshot identifier")
        root = CatalogSnapshot.GetRootAs(payload, 0)
        header = root.Header()
        catalog = root.Payload()
        if header is None or catalog is None:
            raise RuntimeError("Reference catalog snapshot is missing header or payload")
        return {
            "actor_id": self._text(header.OwnerActorId()),
            "generation": header.Generation() or generation,
            "event_sequence": header.EventSequence(),
            "catalog": {
                "entity_count": catalog.EntityCount(),
                "asset_count": catalog.AssetCount(),
                "instrument_count": catalog.InstrumentCount(),
                "listing_count": catalog.ListingCount(),
                "market_count": catalog.MarketCount(),
                "financial_product_count": catalog.FinancialProductCount(),
                "active_market_count": catalog.ActiveMarketCount(),
                "lifecycle_event_count": catalog.LifecycleEventCount(),
            },
        }

    def markets(
        self,
        *,
        symbol: str | None = None,
        venue_id: str | None = None,
        market_type: str | None = None,
        asset_type: str | None = None,
        active_only: bool = False,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if self.markets_snapshot_path is not None:
            return self._read_markets_snapshot(
                symbol=symbol,
                venue_id=venue_id,
                market_type=market_type,
                asset_type=asset_type,
                active_only=active_only,
                status=status,
            )
        value = self.request(
            "/v1/markets",
            symbol=symbol,
            venue_id=venue_id,
            market_type=market_type,
            asset_type=asset_type,
            active_only=active_only,
            status=status,
        )
        return list(value.get("markets", ()))

    @staticmethod
    def _decimal(value: Any) -> str | None:
        if value is None:
            return None
        mantissa = value.Mantissa()
        scale = value.Scale()
        sign = "-" if mantissa < 0 else ""
        digits = str(abs(mantissa)).rjust(scale + 1, "0")
        return f"{sign}{digits}" if scale == 0 else f"{sign}{digits[:-scale]}.{digits[-scale:]}"

    def _read_markets_snapshot(self, **filters: object) -> list[dict[str, Any]]:
        payload, _ = self._read_payload(Path(self.markets_snapshot_path))
        from kairospy.infrastructure.transport.generated.kairos.reference.v1.MarketsSnapshot import MarketsSnapshot

        if payload[4:8] != b"PRD1":
            raise RuntimeError("invalid Reference markets snapshot identifier")
        root = MarketsSnapshot.GetRootAs(payload, 0)
        data = root.Payload()
        if data is None:
            raise RuntimeError("Reference markets snapshot is missing payload")
        result: list[dict[str, Any]] = []
        for index in range(data.MarketsLength()):
            market = data.Markets(index)
            if market is None:
                continue
            value = {
                "market_id": self._text(market.MarketId()),
                "market_key": self._text(market.MarketKey()),
                "instrument_id": self._text(market.InstrumentId()),
                "listing_id": self._text(market.ListingId()),
                "venue_id": self._text(market.VenueId()),
                "market_type": self._text(market.MarketType()),
                "symbol": self._text(market.SourceSymbol()),
                "base_asset_id": self._text(market.BaseAssetId()),
                "quote_asset_id": self._text(market.QuoteAssetId()),
                "status": self._text(market.Status()),
                "price_tick": self._decimal(market.PriceTick()),
                "quantity_tick": self._decimal(market.QuantityTick()),
                "minimum_quantity": self._decimal(market.MinimumQuantity()),
                "minimum_notional": self._decimal(market.MinimumNotional()),
                "contract_size": self._decimal(market.ContractSize()),
                "price_precision": market.PricePrecision(),
                "quantity_precision": market.QuantityPrecision(),
                "effective_from_unix_nanos": market.EffectiveFromUnixNanos(),
                "effective_to_unix_nanos": market.EffectiveToUnixNanos(),
            }
            if filters["symbol"] is not None and value["symbol"] != filters["symbol"]:
                continue
            if filters["venue_id"] is not None and value["venue_id"] != filters["venue_id"]:
                continue
            if filters["market_type"] is not None and value["market_type"] != filters["market_type"]:
                continue
            if filters["status"] is not None and value["status"] != filters["status"]:
                continue
            if filters["active_only"] and value["status"] != "active":
                continue
            result.append(value)
        return result

    def resolve_market(self, **filters: object) -> dict[str, Any]:
        return self.request("/v1/markets/resolve", **filters)



__all__ = ["ReferenceSnapshotClient"]
