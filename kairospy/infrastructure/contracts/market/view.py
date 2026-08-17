"""Market v2 current-view contract.

The payload is deliberately returned as the generated FlatBuffers object.  This
module owns transport framing and root selection; it does not mirror protocol
tables as Python dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import sys
from typing import Any

from kairospy.infrastructure.transport.generated import kairos as _generated_kairos
from kairospy.infrastructure.transport.shared_snapshot import SharedSnapshotReader

sys.modules.setdefault("kairos", _generated_kairos)


class MarketViewKind(str, Enum):
    QUOTE = "quote"
    BAR = "bar"
    GREEKS = "greeks"
    RATE = "rate"
    TICKER_24H = "ticker-24h"
    MARK_PRICE = "mark-price"
    FUNDING_RATE = "funding-rate"
    OPEN_INTEREST = "open-interest"
    INDEX_PRICE = "index-price"
    ORDER_BOOK = "order-book"
    FRESHNESS = "freshness"


@dataclass(frozen=True, slots=True)
class MarketViewKey:
    scope_key: str
    source_id: str
    kind: MarketViewKind
    qualifier: str | None = None

    def __post_init__(self) -> None:
        if not self.scope_key.strip() or not self.source_id.strip():
            raise ValueError("view identity is incomplete")

    def canonical_key(self) -> str:
        return (
            f"scope={self.scope_key};source={self.source_id};"
            f"view={self.kind.value};qualifier={self.qualifier or ''}"
        )

    def resource_path(self, root: str | Path) -> Path:
        return Path(root) / f"{self.resource_id()}.e1.mmap"

    def resource_id(self) -> str:
        qualifier = self.qualifier or "none"
        return "scope-{}-{}-{}-{}".format(
            _component(self.scope_key),
            _component(self.source_id),
            self.kind.value,
            _component(qualifier),
        )


@dataclass(frozen=True, slots=True)
class MarketViewFrame:
    """KSS1 frame plus its generated v2 protocol root."""

    key: MarketViewKey
    generation: int
    payload: bytes
    value: Any


_VIEW_ROOTS: dict[MarketViewKind, tuple[bytes, str, str]] = {
    MarketViewKind.QUOTE: (b"MLQ2", "QuoteLatestView", "QuoteLatestView"),
    MarketViewKind.BAR: (b"MBW2", "BarWindowView", "BarWindowView"),
    MarketViewKind.GREEKS: (b"MLG2", "GreeksLatestView", "GreeksLatestView"),
    MarketViewKind.RATE: (b"MLR2", "RateLatestView", "RateLatestView"),
    MarketViewKind.TICKER_24H: (
        b"MLT2",
        "Ticker24hLatestView",
        "Ticker24hLatestView",
    ),
    MarketViewKind.MARK_PRICE: (
        b"MLM2",
        "MarkPriceLatestView",
        "MarkPriceLatestView",
    ),
    MarketViewKind.FUNDING_RATE: (
        b"MFD2",
        "FundingRateLatestView",
        "FundingRateLatestView",
    ),
    MarketViewKind.OPEN_INTEREST: (
        b"MLI2",
        "OpenInterestLatestView",
        "OpenInterestLatestView",
    ),
    MarketViewKind.INDEX_PRICE: (
        b"MLP2",
        "IndexPriceLatestView",
        "IndexPriceLatestView",
    ),
    MarketViewKind.ORDER_BOOK: (
        b"MLO2",
        "OrderBookLatestView",
        "OrderBookLatestView",
    ),
    MarketViewKind.FRESHNESS: (
        b"MLF2",
        "MarketFreshnessLatestView",
        "MarketFreshnessLatestView",
    ),
}


class MarketViewReader:
    """Read one Market v2 view and return its generated protocol object."""

    def __init__(self, root: str | Path, key: MarketViewKey, *, retries: int = 8) -> None:
        self.root = Path(root)
        self.key = key
        self._reader = SharedSnapshotReader(key.resource_path(self.root), retries=retries)

    def read(self) -> MarketViewFrame:
        snapshot = self._reader.read()
        value = decode_view(snapshot.payload, self.key.kind)
        metadata = value.Metadata()
        if metadata is None:
            raise ValueError("Market v2 view metadata is missing")
        resource_id = _text(metadata.ResourceId())
        view_key = _text(metadata.ViewKey())
        if resource_id != self.key.resource_id():
            raise ValueError(
                f"Market view resource identity mismatch: {resource_id!r}"
            )
        if view_key != self.key.canonical_key():
            raise ValueError(f"Market view key mismatch: {view_key!r}")
        if metadata.ResourceEpoch() != 1:
            raise ValueError("unsupported Market view resource epoch")
        return MarketViewFrame(
            key=self.key,
            generation=snapshot.generation,
            payload=snapshot.payload,
            value=value,
        )


def decode_view(payload: bytes, kind: MarketViewKind) -> Any:
    """Decode a v2 view without copying its FlatBuffers tables."""

    identifier, module_name, root_name = _VIEW_ROOTS[kind]
    if len(payload) < 8 or payload[4:8] != identifier:
        raise ValueError(
            f"invalid Market {kind.value} view identifier: expected {identifier!r}"
        )
    module = __import__(
        f"kairospy.infrastructure.transport.generated.kairos.market.v2.{module_name}",
        fromlist=[root_name],
    )
    root_type = getattr(module, root_name)
    return root_type.GetRootAs(payload, 0)


def _component(value: str) -> str:
    return "".join(
        chr(byte)
        if (byte < 128 and chr(byte).isalnum()) or byte in b"-_."
        else f"%{byte:02X}"
        for byte in value.encode()
    )


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode()


__all__ = ["MarketViewFrame", "MarketViewKey", "MarketViewKind", "MarketViewReader", "decode_view"]
