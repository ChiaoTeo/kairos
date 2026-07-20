from pathlib import Path
import tempfile
import unittest

from kairospy.data import DataCatalog, DatasetRelease, DatasetStatus, DatasetStorageKind, QualityLevel
from kairospy.data.products import BTC_SPOT_DAILY
from kairospy.data.transfer import copy_dataset_release


class DatasetTransferTests(unittest.TestCase):
    def test_copy_dataset_release_merges_catalog_and_files(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir)
            target = Path(target_dir)
            release = DatasetRelease(
                "ds_copy_test",
                BTC_SPOT_DAILY.key,
                "content.abc",
                "market.ohlcv.v1",
                "1",
                "test-transform",
                "1",
                f"{BTC_SPOT_DAILY.relative_path}/release=ds_copy_test",
                "parquet",
                "abc",
                "binance",
                "binance",
                (f"{BTC_SPOT_DAILY.key}@latest-validated",),
                DatasetStatus.APPROVED_FOR_BACKTEST,
                QualityLevel.BACKTEST,
                "2026-07-20T00:00:00+00:00",
                DatasetStorageKind.TABULAR,
                "1",
            )
            release_root = source / release.relative_path
            (release_root / "event_year=2026" / "event_month=07").mkdir(parents=True)
            (release_root / "event_year=2026" / "event_month=07" / "part-00000.parquet").write_bytes(b"rows")
            (release_root / "release.json").write_text("{}", encoding="utf-8")
            source_cache = source / "source" / "provider=binance" / "dataset=spot_klines"
            source_cache.mkdir(parents=True)
            (source_cache / "receipt.json").write_text("{}", encoding="utf-8")

            catalog = DataCatalog(source)
            catalog.register_product_spec(BTC_SPOT_DAILY)
            catalog.register_release(release)
            catalog.save()

            result = copy_dataset_release(
                source,
                target,
                str(BTC_SPOT_DAILY.key),
                include_source_cache=True,
            )

            self.assertEqual(result.release_id, "ds_copy_test")
            self.assertEqual(result.files_copied, 2)
            self.assertEqual(result.source_cache_files_copied, 1)
            self.assertTrue((target / release.relative_path / "release.json").exists())
            self.assertTrue((target / "source" / "provider=binance" / "dataset=spot_klines" / "receipt.json").exists())
            loaded = DataCatalog(target).release(str(BTC_SPOT_DAILY.key))
            self.assertEqual(loaded.release_id, "ds_copy_test")

    def test_copy_dataset_release_discovers_source_without_catalog(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir)
            target = Path(target_dir)
            release_root = (
                source / "canonical" / "market" / "ohlcv" / "asset_class=crypto" / "venue=binance"
                / "instrument=BTC-USDT" / "interval=1d" / "release=ds_no_catalog"
            )
            release_root.mkdir(parents=True)
            (release_root / "release.json").write_text("""
{
  "release_id": "ds_no_catalog",
  "logical_key": "market.ohlcv.crypto.binance.btc-usdt.1d",
  "release_version": "content.abc",
  "schema_id": "market.ohlcv.v1",
  "schema_version": "1",
  "transform_id": "binance.spot_kline.ohlcv",
  "transform_version": "2",
  "content_hash": "abc",
  "provider": "binance",
  "venue": "binance",
  "status": "approved_for_backtest",
  "quality_level": "Q3",
  "published_at": "2026-07-20T00:00:00+00:00"
}
""", encoding="utf-8")
            (release_root / "usage.json").write_text("""
{
  "primary_time": "period_start",
  "dimensions": {"asset_class": "crypto", "venue": "binance", "frequency": "1d"}
}
""", encoding="utf-8")

            result = copy_dataset_release(
                source,
                target,
                "market.ohlcv.crypto.binance.btc-usdt.1d",
            )

            self.assertEqual(result.release_id, "ds_no_catalog")
            self.assertTrue((target / "catalog" / "datasets.json").exists())
            self.assertEqual(
                DataCatalog(target).release("market.ohlcv.crypto.binance.btc-usdt.1d").release_id,
                "ds_no_catalog",
            )


if __name__ == "__main__":
    unittest.main()
