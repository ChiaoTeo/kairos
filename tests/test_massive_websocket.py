from __future__ import annotations

import asyncio
import json
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from trading.adapters.massive.websocket import MassiveLiveStream


class FakeWebSocketClient:
    def __init__(self):
        self.calls = 0

    async def messages(self, market, subscriptions):
        self.calls += 1
        if self.calls == 1:
            yield [{"ev": "Q", "sym": "AAPL", "q": 1}]
            raise ConnectionError("disconnect")
        yield [{"ev": "Q", "sym": "AAPL", "q": 3}]


class MassiveWebSocketTests(unittest.TestCase):
    def test_reconnect_journal_gap_and_backfill_hooks(self):
        async def scenario(directory):
            gaps, backfills, consumed = [], [], []
            async def no_wait(_): pass
            async def on_gap(key, expected, actual): gaps.append((key, expected, actual))
            async def backfill(): backfills.append(True)
            async def consume(message): consumed.append(message)
            journal = Path(directory) / "raw.jsonl"
            stream = MassiveLiveStream(FakeWebSocketClient(), journal, wait=no_wait, on_gap=on_gap, on_reconnect_backfill=backfill)
            await stream.run("stocks", ("Q.AAPL",), consume, stop_after_messages=2)
            self.assertEqual(len(consumed), 2)
            self.assertEqual(gaps, [("Q:AAPL", 2, 3)])
            self.assertEqual(len(backfills), 1)
            self.assertEqual(len(journal.read_text().splitlines()), 2)
        with TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))


if __name__ == "__main__":
    unittest.main()
