from __future__ import annotations

import gzip
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from trading.adapters.massive import SpxwDayAggPipeline
from trading.adapters.massive.source import MassiveFlatFileBatchDownloader, request_fingerprint


HEADER = "ticker,volume,open,close,high,low,window_start,transactions\n"


class MassiveDayAggTests(unittest.TestCase):
    def test_inventory_conversion_and_rolling_representatives_are_deterministic(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _flat(root, "2026-01-02", [
                "O:SPXW260102C06000000,100,11,12,13,10,1767330000000000000,20",
                "O:SPXW260102P06000000,90,8,9,10,7,1767330000000000000,18",
                "O:SPY260102C00600000,200,1,2,2,1,1767330000000000000,30",
            ])
            _flat(root, "2026-01-05", [
                "O:SPXW260105C06100000,120,10,11,12,9,1767589200000000000,24",
                "O:SPXW260105P06100000,110,7,8,9,6,1767589200000000000,22",
            ])
            pipeline = SpxwDayAggPipeline(root)
            first = pipeline.prepare("spxw.day.test.v1", _date("2026-01-02"), _date("2026-01-06"))
            second = pipeline.prepare("spxw.day.test.v1", _date("2026-01-02"), _date("2026-01-06"))
            self.assertEqual(first["dataset_sha256"], second["dataset_sha256"])
            self.assertEqual(first["rows"], 4)
            inventory = json.loads((root / first["inventory_path"]).read_text())
            self.assertEqual([item["trading_date"] for item in inventory["entries"]], ["2026-01-02", "2026-01-05"])
            quality = json.loads((root / "curated/provider=massive/dataset=spxw.day.test.v1/quality.json").read_text())
            self.assertTrue(quality["publishable"]); self.assertEqual(quality["observed_trading_days"], 2)
            import pyarrow.parquet as pq
            monthly = pq.read_table(root / "curated/provider=massive/dataset=spxw.day.test.v1/year=2026/month=01" / first["files"][0]["path"].split("/")[-1]).to_pylist()
            self.assertEqual(monthly[0]["available_time"].isoformat(), "2026-01-03T16:00:00+00:00")
            representatives = pq.read_table(root / "curated/provider=massive/dataset=spxw.day.test.v1/daily_representatives.parquet").to_pylist()
            self.assertEqual(len(representatives), 2)
            self.assertEqual(representatives[0]["synthetic_forward_0dte"], 6003)
            self.assertEqual(representatives[0]["top_call_ticker"], "O:SPXW260102C06000000")

    def test_inventory_fails_closed_on_a_missing_trading_day(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _flat(root, "2026-01-02", ["O:SPXW260102C06000000,1,1,1,1,1,1767330000000000000,1"])
            with self.assertRaisesRegex(FileNotFoundError, "missing 1 trading days"):
                SpxwDayAggPipeline(root).build_inventory(_date("2026-01-02"), _date("2026-01-06"))


def _date(value: str):
    from datetime import date
    return date.fromisoformat(value)


def _flat(root: Path, value: str, rows: list[str]) -> None:
    trading_day = _date(value)
    key = MassiveFlatFileBatchDownloader.file_key(trading_day)
    directory = root / "source/provider=massive/resource=flat-files" / f"request_id={request_fingerprint(key, {})}"
    directory.mkdir(parents=True)
    path = directory / f"{value}.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        handle.write(HEADER)
        handle.write("\n".join(rows) + "\n")
    digest = sha256(path.read_bytes()).hexdigest()
    (directory / "receipt.json").write_text(json.dumps({
        "status": "complete", "file_key": key, "bytes": path.stat().st_size, "sha256": digest,
    }), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
