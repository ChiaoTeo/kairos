"""Account contract facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .base import CommandEnvelope, MmapSnapshotReader, QueryEnvelope
from kairospy.infrastructure.transport.commands import UnixJsonCommandClient


class AccountContractClient:
    """Low-frequency Account query and command facade."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 5.0) -> None:
        self._client = UnixJsonCommandClient(socket_path, timeout=timeout)

    def health(self) -> Mapping[str, Any]:
        return self._get("/v1/health")

    def snapshot(self) -> Mapping[str, Any]:
        return self._get("/v1/snapshot")

    def capabilities(self) -> Mapping[str, Any]:
        return self._get("/v1/capabilities")

    def balances(self, *, symbol: str | None = None) -> Mapping[str, Any]:
        return self._get("/v1/balances", symbol=symbol)

    def positions(self, *, symbol: str | None = None) -> Mapping[str, Any]:
        return self._get("/v1/positions", symbol=symbol)

    def publish_order_event(self, event: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._post("/v1/order-event", event)

    def publish_fill(self, fill: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._post("/v1/fill", fill)

    def apply_simulated_fill(self, fill: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._post("/v1/simulated-fill", fill)

    def mark_to_market(self, update: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._post("/v1/mark-to-market", update)

    def _get(self, path: str, **params: object) -> Mapping[str, Any]:
        query = "&".join(
            f"{key}={value}" for key, value in params.items() if value is not None
        )
        status, value = self._client.request(
            "GET", f"{path}?{query}" if query else path
        )
        return _response(status, value)

    def _post(self, path: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        status, value = self._client.request("POST", path, body)
        return _response(status, value)


def _response(status: int, value: Mapping[str, Any]) -> Mapping[str, Any]:
    if status >= 400:
        raise RuntimeError(
            str(value.get("error", f"Account request failed: HTTP {status}"))
        )
    return value


def backtest_mark_to_market(
    path: str | Path,
    event,
    *,
    segment_key: str = "spot",
    quote_asset: str = "USDT",
) -> Mapping[str, Any] | None:
    """Apply the latest strategy-visible quote to Account during replay."""
    from kairospy.infrastructure.transport.market import QuoteView

    if event.kind != "quote" or not isinstance(event.payload, QuoteView):
        return None
    quote = event.payload
    prices = [
        value.value for value in (quote.bid_price, quote.ask_price) if value is not None
    ]
    if not prices:
        return None
    from decimal import Decimal

    mark = sum((Decimal(value) for value in prices), Decimal("0")) / len(prices)
    client = AccountContractClient(path)
    result = client.mark_to_market(
        {
            "segment_key": segment_key,
            "instrument_id": quote.instrument_id,
            "quote_asset": quote_asset,
            "mark_price": _decimal_wire(mark),
            "observed_at_unix_nanos": quote.event_time_unix_nanos,
        }
    )
    return {"result": result, "snapshot": client.snapshot()}


def _decimal_wire(value) -> dict[str, int]:
    normalized = value.normalize()
    sign, digits, exponent = normalized.as_tuple()
    mantissa = int("".join(str(digit) for digit in digits) or "0")
    if sign:
        mantissa = -mantissa
    if exponent >= 0:
        mantissa *= 10**exponent
        scale = 0
    else:
        scale = -exponent
    return {"mantissa": mantissa, "scale": scale}


def snapshot_reader(path: str | Path) -> MmapSnapshotReader:
    from kairospy.infrastructure.transport.generated.kairos.account.v1.AccountsSnapshot import (
        AccountsSnapshot,
    )

    return MmapSnapshotReader(path, file_identifier=b"AAC1", root_type=AccountsSnapshot)


__all__ = [
    "AccountContractClient",
    "CommandEnvelope",
    "QueryEnvelope",
    "backtest_mark_to_market",
    "snapshot_reader",
]
