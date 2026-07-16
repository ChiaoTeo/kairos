from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Iterable

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
                 on_reconnect_backfill: Callable[[], Awaitable[None]] | None = None) -> None:
        self.client, self.journal, self.wait = client, Path(journal), wait
        self.on_gap, self.on_reconnect_backfill = on_gap, on_reconnect_backfill
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
            except Exception:
                attempts += 1
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
