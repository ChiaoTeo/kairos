from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import struct
import sys
from typing import Any, AsyncIterator, cast

from kairospy.infrastructure.contracts.market.records import MarketEventRecord
from kairospy.infrastructure.contracts.market import (
    MarketIndexedViewQueries,
    MarketViewKey,
    MarketViewKind,
)
from kairospy.infrastructure.transport.native_event import NativeEventSource
from kairospy.infrastructure.protocol.generated_spec import (
    DEFAULT_CHANNEL,
    MARKET_EVENTS,
)

# The generated FlatBuffers modules use their schema namespace (``kairos``)
# for sibling imports. Keep that generated namespace private to this adapter
# while making those imports resolvable; application code never sees it.
from kairospy.infrastructure.protocol.generated import kairos as _generated_kairos

sys.modules.setdefault("kairos", _generated_kairos)


class MarketViewAccess:
    """Access Market v2 entities from the owner-scoped LMDB view."""

    def __init__(
        self,
        path: str | Path,
        *,
        workspace_id: str,
        launch_id: str | None,
        instance_id: str | None,
    ) -> None:
        self.path = Path(path)
        self._queries = MarketIndexedViewQueries(
            path,
            workspace_id=workspace_id,
            launch_id=launch_id,
            instance_id=instance_id,
        )

    def read_view(
        self,
        key: MarketViewKey | str,
        provider: str | None = None,
        kind: MarketViewKind | None = None,
        qualifier: str | None = None,
    ):
        """Read one typed v2 current-view resource."""

        if isinstance(key, str):
            if provider is None or kind is None:
                raise ValueError("Market v2 view source and kind are required")
            key = MarketViewKey(key, provider, kind, qualifier)
        return self._queries.read(key)

    def read_quote(self, market_id: str, provider: str) -> object | None:
        frame = self.read_view(
            MarketViewKey(market_id, provider, MarketViewKind.QUOTE)
        )
        if frame is None:
            return None
        return frame.value

    def read_bar(
        self, market_id: str, provider: str, timeframe: str
    ) -> object | None:
        frame = self.read_view(
            MarketViewKey(market_id, provider, MarketViewKind.BAR, timeframe)
        )
        if frame is None:
            return None
        return frame.value

    def read_greeks(self, market_id: str, provider: str) -> object | None:
        frame = self.read_view(
            MarketViewKey(market_id, provider, MarketViewKind.GREEKS)
        )
        if frame is None:
            return None
        return frame.value

class UnixMarketEventStream:
    """Consume the replay Market frame stream.

    Production Market events use Aeron.  This adapter remains only for the
    launch-owned replay process and never reads or recovers through a current view.
    """

    def __init__(
        self,
        socket_path: str | Path,
        *,
        stream_id: str = "market.events",
        replayable: bool = False,
        reconnect_delay: float = 0.25,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.stream_id = stream_id
        self.replayable = replayable
        if reconnect_delay < 0:
            raise ValueError("reconnect_delay cannot be negative")
        self.reconnect_delay = reconnect_delay

    async def replay_from(
        self, after_sequence: int = 0
    ) -> AsyncIterator[MarketEventRecord]:
        cursor = max(0, after_sequence)
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(self.socket_path)
            except (FileNotFoundError, ConnectionError, OSError):
                await asyncio.sleep(self.reconnect_delay)
                continue
            try:
                while True:
                    prefix = await reader.readexactly(4)
                    (length,) = struct.unpack(">I", prefix)
                    if length == 0 or length > 4 * 1024 * 1024:
                        raise ValueError("invalid market event frame length")
                    payload = await reader.readexactly(length)
                    event = _decode_market_event(payload)
                    if event.sequence <= cursor:
                        continue
                    cursor = event.sequence
                    yield event
            except asyncio.IncompleteReadError:
                if self.replayable:
                    return
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
            await asyncio.sleep(self.reconnect_delay)


class AeronMarketEventSource(NativeEventSource[MarketEventRecord]):
    """Market-owned adapter over the native Aeron subscription bridge."""

    replayable = False

    def __init__(
        self,
        *,
        aeron_dir: str | Path | None = None,
        channel: str = DEFAULT_CHANNEL,
        stream_id: int = MARKET_EVENTS,
    ) -> None:
        super().__init__(
            decoder=decode_market_event,
            aeron_dir=aeron_dir,
            channel=channel,
            stream_id=stream_id,
        )


def decode_market_event(payload: bytes) -> MarketEventRecord:
    identifier = payload[4:8]
    v2 = {
        b"MQU2": ("QuoteUpdated", "quote", "Quote"),
        b"MTO2": ("TradeOccurred", "trade", "Trade"),
        b"MBV2": ("BarCompleted", "bar", "Bar"),
        b"MGU2": ("GreeksUpdated", "greeks", "Greeks"),
        b"MRU2": ("RateUpdated", "rate", "Rate"),
        b"MTU2": ("Ticker24hUpdated", "ticker_24h", "Ticker"),
        b"MMP2": ("MarkPriceUpdated", "mark_price", "MarkPrice"),
        b"MFD2": ("FundingRateUpdated", "funding_rate", "FundingRate"),
        b"MOI2": ("OpenInterestUpdated", "open_interest", "OpenInterest"),
        b"MIP2": ("IndexPriceUpdated", "index_price", "IndexPrice"),
        b"MOS2": ("OrderBookSnapshotReceived", "order_book_snapshot", "Snapshot"),
        b"MOD2": ("OrderBookDeltaReceived", "order_book_delta", "Delta"),
        b"MOR2": ("OrderBookResyncRequired", "order_book_resync", None),
    }.get(identifier)
    if v2 is not None:
        return _decode_v2_event(payload, *v2)
    raise ValueError(f"unsupported Market event identifier: {identifier!r}")


def _decode_v2_event(
    payload: bytes, root_name: str, kind: str, value_name: str | None
) -> MarketEventRecord:
    module = __import__(
        f"kairospy.infrastructure.protocol.generated.kairos.market.v2.{root_name}",
        fromlist=[root_name],
    )
    root_type = getattr(module, root_name)
    root = cast(Any, root_type.GetRootAs(payload, 0))
    metadata = cast(Any, root.Metadata())
    if metadata is None:
        raise ValueError(f"{root_name} metadata is missing")
    value = None if value_name is None else getattr(root, value_name)()
    if value_name is not None and value is None:
        raise ValueError(f"{root_name} payload is missing")
    event_time = int(metadata.OccurredAtUnixNanos())
    occurred_at = (
        None
        if not event_time
        else datetime.fromtimestamp(event_time / 1_000_000_000, tz=timezone.utc)
    )
    return MarketEventRecord(
        stream_id=_required_header_text(metadata.StreamId(), "stream_id"),
        sequence=int(metadata.Sequence()),
        kind=kind,
        payload=value,
        occurred_at=occurred_at,
        schema_version=2,
        producer=_required_header_text(metadata.ProducerId(), "producer_id"),
        causation_id=_header_text(metadata.CausationId()),
        launch_id=_header_text(metadata.LaunchId()),
        instance_id=_header_text(metadata.InstanceId()),
    )


_decode_market_event = decode_market_event


def _required_header_text(value: bytes | None, name: str) -> str:
    result = _header_text(value)
    if result is None:
        raise ValueError(f"Market event {name} is required")
    return result


def _header_text(value: bytes | None) -> str | None:
    if value is None:
        return None
    result = value.decode()
    return result if result.strip() else None
