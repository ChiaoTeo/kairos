from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading.domain.identity import InstrumentId
from trading.domain.market_data import Bar
from trading.history import BarRepository, BinanceHistoricalBarProvider


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
INSTRUMENT = InstrumentId("crypto:binance:spot:BTCUSDT")


class FakeTransport:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.requests = []

    def request(self, method, path, params=None, headers=None):
        self.requests.append((method, path, params))
        return next(self.pages)


def kline(open_ms: int, close_ms: int, close: str = "101"):
    return [open_ms, "100", "110", "90", close, "12.5", close_ms]


class HistoryTests(unittest.TestCase):
    def test_binance_provider_paginates_and_converts_bars(self) -> None:
        first_open = int(START.timestamp() * 1000)
        rows = [kline(first_open + index * 60_000, first_open + (index + 1) * 60_000 - 1) for index in range(1000)]
        final_open = first_open + 1000 * 60_000
        transport = FakeTransport((rows, [kline(final_open, final_open + 59_999, "102")]))
        provider = BinanceHistoricalBarProvider("spot", transport)

        bars = provider.fetch(INSTRUMENT, "BTCUSDT", "1m", START, START + timedelta(minutes=1001))

        self.assertEqual(len(bars), 1001)
        self.assertEqual(bars[-1].close, Decimal("102"))
        self.assertEqual(transport.requests[1][2]["startTime"], final_open)
        self.assertEqual(bars[0].end, START + timedelta(minutes=1))

    def test_repository_round_trip(self) -> None:
        bars = tuple(
            Bar(INSTRUMENT, START + timedelta(hours=index), START + timedelta(hours=index + 1),
                Decimal("100"), Decimal("110"), Decimal("90"), Decimal(str(101 + index)), Decimal("5"))
            for index in range(2)
        )

        class Provider:
            def fetch(self, *args):
                return bars

        with tempfile.TemporaryDirectory() as root:
            repository = BarRepository(root)
            saved = repository.download(
                Provider(), dataset_id="btc-1h", instrument_id=INSTRUMENT, symbol="BTCUSDT", interval="1h",
                start=START, end=START + timedelta(hours=2), source="fixture",
            )
            loaded = repository.load("btc-1h")

            self.assertEqual(loaded, saved)
            self.assertEqual(repository.datasets(), ("btc-1h",))
            self.assertEqual(list(loaded.frame().columns), ["open", "high", "low", "close", "volume"])

    def test_missing_dataset_error_lists_available_data(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repository = BarRepository(root)
            with self.assertRaisesRegex(FileNotFoundError, "available datasets: none"):
                repository.load("missing")

    def test_rejects_naive_time(self) -> None:
        provider = BinanceHistoricalBarProvider("spot", FakeTransport(()))
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            provider.fetch(INSTRUMENT, "BTCUSDT", "1h", START.replace(tzinfo=None), START)


if __name__ == "__main__":
    unittest.main()
