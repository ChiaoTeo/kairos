from datetime import datetime, timedelta, timezone
import json
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from kairos.data.bootstrap import register_default_products
from kairos.data.columnar_publishing import publish_intraday_staging_parquet
from kairos.data.contracts import QualityLevel
from kairos.data.products import BINANCE_USDM_PERPETUAL_HOURLY, capabilities_payload
from kairos.data.quality import DatasetQualityService


class ColumnarPublishingTests(unittest.TestCase):
    def test_intraday_staging_parquet_publishes_sorted_quality_release(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            register_default_products(root)
            staging = root / "tmp" / "staging"
            partition = staging / "event_year=2026" / "event_month=01"
            partition.mkdir(parents=True)
            rows = [
                _bar("ETHUSDT", datetime(2026, 1, 1, 1, tzinfo=timezone.utc), 101),
                _bar("BTCUSDT", datetime(2026, 1, 1, 0, tzinfo=timezone.utc), 100),
                _bar("ETHUSDT", datetime(2026, 1, 1, 0, tzinfo=timezone.utc), 102),
                _bar("BTCUSDT", datetime(2026, 1, 1, 0, tzinfo=timezone.utc), 100),
            ]
            pq.write_table(pa.Table.from_pylist(rows, schema=_arrow_schema()), partition / "part-raw.parquet")

            result = publish_intraday_staging_parquet(
                root, BINANCE_USDM_PERPETUAL_HOURLY, staging,
                schema=_schema(), lineage={"lineage_version": 2, "point_in_time_safe": True},
                interval=timedelta(hours=1),
                capabilities=capabilities_payload(BINANCE_USDM_PERPETUAL_HOURLY, "pending"),
                provider="binance", venue="binance",
                transform_id="test.columnar", transform_version="1",
                quality_level=QualityLevel.BACKTEST,
                primary_key=("venue", "instrument_id", "period_start", "interval"),
                order_by=("period_start", "instrument_id"),
            )

            assessment = DatasetQualityService(root).assess(result.release.release_id)
            checks = {item.name: item for item in assessment.checks}
            release_directory = root / result.release.relative_path
            release_manifest = (release_directory / "data_release_manifest.json")
            release_metadata = json.loads((release_directory / "release.json").read_text(encoding="utf-8"))
            release_manifest_payload = json.loads(release_manifest.read_text(encoding="utf-8"))
            self.assertTrue(checks["deterministic_order"].passed, checks["deterministic_order"])
            self.assertTrue(checks["streaming_execution"].passed)
            self.assertEqual(result.manifest["rows"], 3)
            self.assertTrue(release_manifest.exists())
            self.assertEqual(release_manifest_payload["kind"], "data_release_manifest")
            self.assertEqual(release_manifest_payload["content_hash"], result.release.content_hash)
            self.assertEqual(len(release_metadata["data_release_manifest_hash"]), 64)
            self.assertEqual(release_metadata["artifact_ref"], f"data://{result.release.product_key}/releases/{result.release.release_id}")


def _bar(symbol: str, start: datetime, price: int):
    end = start + timedelta(hours=1)
    return {
        "period_start": start,
        "period_end": end,
        "event_time": end,
        "available_time": end,
        "venue": "binance",
        "instrument_id": f"crypto:binance:perpetual:{symbol}",
        "symbol": symbol,
        "product": "usdm-perpetual",
        "interval": "PT1H",
        "open": float(price),
        "high": float(price + 2),
        "low": float(price - 2),
        "close": float(price + 1),
        "volume": 10.0,
        "quote_volume": 1000.0,
        "trade_count": 20,
        "taker_buy_base_volume": 4.0,
        "taker_buy_quote_volume": 400.0,
    }


def _schema():
    return {
        "schema_id": BINANCE_USDM_PERPETUAL_HOURLY.schema_id,
        "schema_version": 1,
        "primary_key": ["venue", "instrument_id", "period_start", "interval"],
        "columns": {name: {"type": "unknown"} for name in _bar("BTCUSDT", datetime(2026, 1, 1, tzinfo=timezone.utc), 100)},
    }


def _arrow_schema():
    return pa.schema([
        pa.field("period_start", pa.timestamp("us")),
        pa.field("period_end", pa.timestamp("us")),
        pa.field("event_time", pa.timestamp("us")),
        pa.field("available_time", pa.timestamp("us")),
        pa.field("venue", pa.string()),
        pa.field("instrument_id", pa.string()),
        pa.field("symbol", pa.string()),
        pa.field("product", pa.string()),
        pa.field("interval", pa.string()),
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.float64()),
        pa.field("quote_volume", pa.float64()),
        pa.field("trade_count", pa.int64()),
        pa.field("taker_buy_base_volume", pa.float64()),
        pa.field("taker_buy_quote_volume", pa.float64()),
    ])


if __name__ == "__main__":
    unittest.main()
