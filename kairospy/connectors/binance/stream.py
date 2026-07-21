from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

from kairospy.contracts import CanonicalEventEnvelope, canonical_from_trading_market_data
from kairospy.trading.identity import InstrumentId
from kairospy.market_data.stream import BoundedEventChannel
from kairospy.market_data.capture import CanonicalCaptureWriter

from .market_stream import BinanceStreamSession, parse_market_stream_event


class BinanceCanonicalStreamService:
    """Bridge the blocking websocket-client transport into the supervised async runtime."""

    def __init__(
        self,
        session: BinanceStreamSession,
        instrument_lookup: dict[str, InstrumentId],
        output: BoundedEventChannel[CanonicalEventEnvelope],
        *,
        source_instance: str,
        stream_id: str,
        canonical_capture: CanonicalCaptureWriter | None = None,
    ) -> None:
        if not source_instance.strip() or not stream_id.strip():
            raise ValueError("Binance canonical stream identity cannot be empty")
        self.session = session
        self.instrument_lookup = dict(instrument_lookup)
        self.output = output
        self.source_instance = source_instance
        self.stream_id = stream_id
        self.canonical_capture = canonical_capture
        self.raw_messages = 0
        self.canonical_events = 0
        self.ignored_messages = 0
        self.reconnects = 0

    async def run(self, *, message_limit: int | None = None) -> None:
        loop = asyncio.get_running_loop()
        reconnect_pending = False

        def reconnect(_: int) -> None:
            nonlocal reconnect_pending
            self.reconnects += 1
            reconnect_pending = True

        def consume(row: dict[str, object]) -> None:
            nonlocal reconnect_pending
            receive_sequence = self.raw_messages
            self.raw_messages += 1
            value = parse_market_stream_event(row, self.instrument_lookup)
            if value is None:
                self.ignored_messages += 1
                return
            now = datetime.now(timezone.utc)
            sequence = _source_sequence(row)
            events = canonical_from_trading_market_data(
                value,
                source="binance",
                source_instance=self.source_instance,
                stream_id=self.stream_id,
                receive_time=now,
                published_time=now,
                source_sequence=sequence,
                receive_sequence=receive_sequence,
            )
            for event in events:
                flags = event.flags + (("transport_reconnected",) if reconnect_pending else ())
                event = replace(
                    event, capture_offset=f"raw-line:{receive_sequence + 1}", flags=flags,
                )
                reconnect_pending = False
                if self.canonical_capture is not None:
                    self.canonical_capture.append(event)
                asyncio.run_coroutine_threadsafe(self.output.publish(event), loop).result()
                self.canonical_events += 1

        worker = asyncio.create_task(asyncio.to_thread(
            self.session.consume, consume, message_limit=message_limit, on_reconnect=reconnect,
        ))
        try:
            await worker
        finally:
            self.session.stop()
            if not worker.done():
                await asyncio.gather(worker, return_exceptions=True)
            await self.output.close()
            if self.canonical_capture is not None:
                self.canonical_capture.finalize()


def _source_sequence(row: dict[str, object]) -> int | None:
    payload = row.get("data") if isinstance(row.get("data"), dict) else row
    event_type = payload.get("e")  # type: ignore[union-attr]
    keys = {
        "depthUpdate": ("u", "E"),
        "trade": ("t", "E"),
        "aggTrade": ("a", "E"),
    }.get(str(event_type), ("E",))
    if event_type is None and all(key in payload for key in ("b", "a", "B", "A", "u")):  # type: ignore[operator]
        keys = ("u",)
    for key in keys:
        value = payload.get(key)  # type: ignore[union-attr]
        if value is not None:
            return int(value)
    return None
