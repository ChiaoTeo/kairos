from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Iterable

from kairos.contracts import CanonicalEventEnvelope, canonicalize_market_event
from kairos.market_data.stream import BoundedEventChannel
from kairos.market_data.capture import CanonicalCaptureWriter

from .config import MassiveConfig


class MassiveWebSocketClient:
    def __init__(self, config: MassiveConfig) -> None:
        self.config = config

    async def messages(self, market: str, subscriptions: Iterable[str]) -> AsyncIterator[list[dict[str, object]]]:
        if market not in {"stocks", "options", "indices", "forex", "crypto", "futures"}:
            raise ValueError(f"unsupported Massive WebSocket market: {market}")
        try:
            import websockets
        except ImportError as error:
            raise RuntimeError("Massive WebSocket requires the 'massive' optional dependency") from error
        url = f"{self.config.socket_base.rstrip('/')}/{market}"
        async with websockets.connect(url) as socket:
            await socket.send(json.dumps({"action": "auth", "params": self.config.api_key}))
            auth = json.loads(await socket.recv())
            if not _authenticated(auth):
                raise RuntimeError("Massive WebSocket authentication failed")
            values = tuple(subscriptions)
            if values:
                await socket.send(json.dumps({"action": "subscribe", "params": ",".join(values)}))
            async for message in socket:
                payload = json.loads(message)
                yield payload if isinstance(payload, list) else [payload]


def _authenticated(payload: object) -> bool:
    values = payload if isinstance(payload, list) else [payload]
    return any(isinstance(item, dict) and (
        item.get("status") == "auth_success" or "authenticated" in str(item.get("message", "")).lower()
    ) for item in values)


class MassiveLiveStream:
    """Reconnectable raw journal with sequence-gap and REST-backfill hooks."""

    def __init__(self, client: MassiveWebSocketClient, journal: str | Path, *,
                 wait: Callable[[float], Awaitable[None]],
                 on_gap: Callable[[str, int, int], Awaitable[None]] | None = None,
                 on_reconnect_backfill: Callable[[], Awaitable[None]] | None = None,
                 on_fault: Callable[["MassiveStreamFault"], Awaitable[None]] | None = None) -> None:
        self.client, self.journal, self.wait = client, Path(journal), wait
        self.on_gap, self.on_reconnect_backfill = on_gap, on_reconnect_backfill
        self.on_fault = on_fault
        self._sequences: dict[str, int] = {}

    async def run(self, market: str, subscriptions: Iterable[str], consume: Callable[[dict[str, object]], Awaitable[None]], *,
                  stop_after_messages: int | None = None) -> None:
        attempts, received = 0, 0
        while stop_after_messages is None or received < stop_after_messages:
            try:
                if attempts and self.on_reconnect_backfill is not None:
                    await self.on_reconnect_backfill()
                async for batch in self.client.messages(market, subscriptions):
                    attempts = 0
                    for message in batch:
                        self._journal(message)
                        await self._check_sequence(message)
                        await consume(message)
                        received += 1
                        if stop_after_messages is not None and received >= stop_after_messages:
                            return
            except Exception as error:
                attempts += 1
                if self.on_fault is not None:
                    await self.on_fault(MassiveStreamFault(
                        market, attempts, type(error).__name__, str(error), datetime.now(timezone.utc),
                    ))
                await self.wait(min(30.0, 1.5 * attempts))

    async def _check_sequence(self, message: dict[str, object]) -> None:
        raw = message.get("q") if message.get("q") is not None else message.get("sequence_number")
        if raw is None:
            return
        sequence = int(raw)
        key = f"{message.get('ev', message.get('event_type', 'unknown'))}:{message.get('sym', message.get('ticker', 'unknown'))}"
        previous = self._sequences.get(key)
        if previous is not None and sequence > previous + 1 and self.on_gap is not None:
            await self.on_gap(key, previous + 1, sequence)
        if previous is None or sequence > previous:
            self._sequences[key] = sequence

    def _journal(self, message: dict[str, object]) -> None:
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with self.journal.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


@dataclass(frozen=True, slots=True)
class MassiveStreamFault:
    market: str
    attempt: int
    error_type: str
    message: str
    occurred_at: datetime


class MassiveCanonicalStreamService:
    """Normalize a supervised Massive stream into the shared canonical channel."""

    def __init__(
        self,
        stream: MassiveLiveStream,
        market: str,
        subscriptions: Iterable[str],
        decode: Callable[[dict[str, object], int], Iterable[object]],
        output: BoundedEventChannel[CanonicalEventEnvelope],
        *,
        source_instance: str,
        canonical_capture: CanonicalCaptureWriter | None = None,
    ) -> None:
        if not source_instance.strip():
            raise ValueError("Massive canonical stream source instance cannot be empty")
        self.stream = stream
        self.market = market
        self.subscriptions = tuple(subscriptions)
        self.decode = decode
        self.output = output
        self.source_instance = source_instance
        self.canonical_capture = canonical_capture
        self.raw_messages = 0
        self.canonical_events = 0

    async def run(self, *, stop_after_messages: int | None = None) -> None:
        async def consume(message: dict[str, object]) -> None:
            source_order = self.raw_messages
            self.raw_messages += 1
            for event in self.decode(message, source_order):
                canonical = replace(
                    canonicalize_market_event(event, source_instance=self.source_instance),
                    capture_offset=f"raw-line:{source_order + 1}",
                )
                if self.canonical_capture is not None:
                    self.canonical_capture.append(canonical)
                await self.output.publish(canonical)
                self.canonical_events += 1

        try:
            await self.stream.run(
                self.market, self.subscriptions, consume,
                stop_after_messages=stop_after_messages,
            )
        finally:
            await self.output.close()
            if self.canonical_capture is not None:
                self.canonical_capture.finalize()
