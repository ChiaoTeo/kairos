"""Market v2 owner-scoped LMDB current-view contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import sys
from typing import Any, cast

from kairospy.infrastructure.protocol.generated import kairos as _generated_kairos
from kairospy.infrastructure.transport.indexed_view import (
    IndexedViewMetadata,
    IndexedViewReader,
    IndexedViewSchema,
)

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


_DATABASES: dict[MarketViewKind, str] = {
    MarketViewKind.QUOTE: "quotes",
    MarketViewKind.BAR: "bars",
    MarketViewKind.GREEKS: "greeks",
    MarketViewKind.RATE: "rates",
    MarketViewKind.TICKER_24H: "tickers_24h",
    MarketViewKind.MARK_PRICE: "mark_prices",
    MarketViewKind.FUNDING_RATE: "funding_rates",
    MarketViewKind.OPEN_INTEREST: "open_interest",
    MarketViewKind.INDEX_PRICE: "index_prices",
    MarketViewKind.ORDER_BOOK: "order_books",
    MarketViewKind.FRESHNESS: "freshness",
}
_ROOTS: dict[MarketViewKind, tuple[str, str]] = {
    MarketViewKind.QUOTE: ("MQC3", "MarketQuoteCurrent"),
    MarketViewKind.BAR: ("MBC3", "MarketBarCurrent"),
    MarketViewKind.GREEKS: ("MGC3", "MarketGreeksCurrent"),
    MarketViewKind.RATE: ("MRC3", "MarketRateCurrent"),
    MarketViewKind.TICKER_24H: ("MTC3", "MarketTicker24hCurrent"),
    MarketViewKind.MARK_PRICE: ("MMP3", "MarketMarkPriceCurrent"),
    MarketViewKind.FUNDING_RATE: ("MFR3", "MarketFundingRateCurrent"),
    MarketViewKind.OPEN_INTEREST: ("MOI3", "MarketOpenInterestCurrent"),
    MarketViewKind.INDEX_PRICE: ("MIP3", "MarketIndexPriceCurrent"),
    MarketViewKind.ORDER_BOOK: ("MOB3", "MarketOrderBookCurrent"),
    MarketViewKind.FRESHNESS: ("MFS3", "MarketFreshnessCurrent"),
}
_SCHEMAS = tuple(
    IndexedViewSchema(_DATABASES[kind], 1, identifier, 1)
    for kind, (identifier, _) in _ROOTS.items()
)


@dataclass(frozen=True, slots=True)
class MarketViewKey:
    scope_key: str
    provider: str
    kind: MarketViewKind
    qualifier: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.scope_key
            or self.scope_key.strip() != self.scope_key
            or not self.provider
            or self.provider.strip() != self.provider
            or (self.qualifier is not None and self.qualifier.strip() != self.qualifier)
        ):
            raise ValueError("Market indexed view identity is invalid")

    def canonical_key(self) -> str:
        return (
            f"scope={self.scope_key};provider={self.provider};"
            f"view={self.kind.value};qualifier={self.qualifier or ''}"
        )

    def encoded(self) -> bytes:
        encoded = bytearray((1,))
        for part in (self.scope_key, self.provider, self.qualifier or ""):
            raw = part.encode()
            if b"\x00" in raw or len(raw) > 65535:
                raise ValueError("Market indexed key component is invalid")
            encoded.extend(len(raw).to_bytes(2, "big"))
            encoded.extend(raw)
        return bytes(encoded)


@dataclass(frozen=True, slots=True)
class MarketIndexedFrame:
    key: MarketViewKey
    metadata: IndexedViewMetadata
    payload: bytes
    value: Any


def market_indexed_environment_path(root: str | Path) -> Path:
    return Path(root) / "views" / "v3" / "Market" / "market-main" / "epoch-1" / "current.lmdb"


class MarketIndexedViewQueries:
    """Read exact Market entities with metadata from one LMDB transaction."""

    def __init__(
        self,
        root: str | Path,
        *,
        workspace_id: str,
        launch_id: str | None,
        instance_id: str | None,
    ) -> None:
        self._path = market_indexed_environment_path(root)
        self._workspace_id = workspace_id
        self._launch_id = launch_id
        self._instance_id = instance_id
        self._reader: IndexedViewReader | None = None

    def _open_reader(self) -> IndexedViewReader:
        if self._reader is None:
            self._reader = IndexedViewReader(
                self._path,
                map_size=512 * 1024 * 1024,
                workspace_id=self._workspace_id,
                launch_id=self._launch_id,
                instance_id=self._instance_id,
                owner="Market",
                publisher_resource_id="market-main",
                resource_epoch=1,
                schemas=_SCHEMAS,
            )
        return self._reader

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    @property
    def path(self) -> Path:
        return self._path

    def read(self, key: MarketViewKey) -> MarketIndexedFrame | None:
        metadata, payload = self._open_reader().value_snapshot(
            _DATABASES[key.kind], key.encoded()
        )
        if metadata.rebuild_state != "ready":
            raise ValueError("Market indexed current view is not ready")
        if payload is None:
            return None
        value = _decode_entity(payload, key)
        return MarketIndexedFrame(key, metadata, payload, value)

def _decode_entity(payload: bytes, key: MarketViewKey) -> Any:
    identifier, root_name = _ROOTS[key.kind]
    if len(payload) < 8 or payload[4:8] != identifier.encode():
        raise ValueError(f"invalid {identifier} {root_name} value")
    module = __import__(
        f"kairospy.infrastructure.protocol.generated.kairos.market.v2.{root_name}",
        fromlist=[root_name],
    )
    root_type = cast(Any, getattr(module, root_name))
    root = cast(Any, root_type.GetRootAs(payload, 0))
    identity = cast(Any, root.Identity())
    if (
        _text(identity.ScopeKey()) != key.scope_key
        or _text(identity.Provider()) != key.provider
        or (_text(identity.Qualifier()) or "") != (key.qualifier or "")
    ):
        raise ValueError("Market indexed key/value identity mismatch")
    value = root.Value()
    if value is None:
        raise ValueError(f"{root_name} is missing its required value")
    return value


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode()


__all__ = [
    "MarketIndexedFrame",
    "MarketIndexedViewQueries",
    "MarketViewKey",
    "MarketViewKind",
    "market_indexed_environment_path",
]
