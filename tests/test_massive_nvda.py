from __future__ import annotations

from datetime import date
from decimal import Decimal
import gzip
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from trading.adapters.massive import (
    MassiveClient, MassiveConfig, MassiveEquityDayAggPipeline, MassiveResponse,
    OptionDayAggPipeline, OptionDayIvPipeline,
)
from trading.adapters.massive.source import MassiveFlatFileBatchDownloader, request_fingerprint


class StubTransport:
    def __init__(self, payload): self.payload = payload
    def request(self, url, headers, timeout):
        return MassiveResponse(200, {}, json.dumps(self.payload).encode())


class MassiveNvdaTests(unittest.TestCase):
    def test_nvda_equity_options_and_internal_iv_pipeline(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _flat(root, "2026-01-02", [
                "O:NVDA260116C00100000,100,4.8,5,5.2,4.7,1767330000000000000,20",
                "O:NVDA260116P00100000,90,4.3,4.5,4.7,4.2,1767330000000000000,18",
                "O:SPXW260102C06000000,1,1,1,1,1,1767330000000000000,1",
            ])
            options = OptionDayAggPipeline(root, "NVDA").prepare(
                "options.nvda.test.v1", date(2026, 1, 2), date(2026, 1, 3),
            )
            self.assertEqual(options["option_root"], "NVDA"); self.assertEqual(options["rows"], 2)
            payload = {"request_id": "nvda-bars", "results": [{
                "t": 1767330000000, "o": 99, "h": 102, "l": 98, "c": 100, "v": 1_000_000, "n": 10_000, "vw": 100,
            }]}
            equity = MassiveEquityDayAggPipeline(
                root, MassiveClient(MassiveConfig("secret"), StubTransport(payload)),
            ).prepare("equity.nvda.test.v1", "NVDA", date(2026, 1, 2), date(2026, 1, 3))
            self.assertEqual(equity["rows"], 1)
            iv = OptionDayIvPipeline(root).prepare(
                "features.nvda.iv.test.v1", "options.nvda.test.v1", "equity.nvda.test.v1",
                risk_free_rate=Decimal("0.04"), dividend_yield=Decimal("0"),
            )
            self.assertEqual(iv["rows"], 2); self.assertEqual(iv["converged_rows"], 2)
            quality = json.loads((root / "features/provider=massive/dataset=features.nvda.iv.test.v1/quality.json").read_text())
            self.assertEqual(quality["status_counts"], {"converged": 2})
            import pyarrow.parquet as pq
            rows = pq.read_table(root / "features/provider=massive/dataset=features.nvda.iv.test.v1" / iv["files"][0]["path"]).to_pylist()
            self.assertTrue(all(item["implied_volatility"] > 0 for item in rows))


def _flat(root: Path, value: str, rows: list[str]) -> None:
    trading_day = date.fromisoformat(value)
    key = MassiveFlatFileBatchDownloader.file_key(trading_day)
    directory = root / "source/provider=massive/resource=flat-files" / f"request_id={request_fingerprint(key, {})}"
    directory.mkdir(parents=True)
    path = directory / f"{value}.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        handle.write("ticker,volume,open,close,high,low,window_start,transactions\n")
        handle.write("\n".join(rows) + "\n")
    digest = sha256(path.read_bytes()).hexdigest()
    (directory / "receipt.json").write_text(json.dumps({
        "status": "complete", "file_key": key, "bytes": path.stat().st_size, "sha256": digest,
    }), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
