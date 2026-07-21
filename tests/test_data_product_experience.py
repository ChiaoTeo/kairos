from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from kairospy.__main__ import main
from kairospy.data import (
    DataCatalog, DataDiagnosticsService, DatasetKey, DatasetLayer, DataProductDefinition, DatasetRelease,
    DatasetStatus, QualityLevel,
)
from kairospy.storage.data_lake import write_daily_dataset


class DataProductExperienceTests(unittest.TestCase):
    def _lake(self, directory: str) -> tuple[DataProductDefinition, DatasetRelease]:
        root = Path(directory)
        product = DataProductDefinition(
            DatasetKey("market.ohlcv.crypto.test.btc-usdt.1d"), "BTC daily test data", DatasetLayer.CANONICAL,
            "Daily BTC/USDT bars for governed study and backtesting",
            {"asset_class": "crypto", "instrument": "BTC-USDT", "frequency": "1d"},
            "period_start", owner="data-platform",
        )
        relative_path = "canonical/test/release-test-1"
        target = root / relative_path
        manifest = write_daily_dataset(
            target, [{"period_start": "2026-01-01T00:00:00Z", "value": 1}],
            dataset_id="release-test-1", schema={"schema_id": "market.ohlcv.v1", "primary_key": ["period_start"]},
            lineage={"source": {"provider": "test"}},
        )
        for name in ("usage", "release"):
            (target / f"{name}.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        release = DatasetRelease(
            "release-test-1", product.key, "1", "market.ohlcv.v1", "1", "fixture", "1",
            relative_path, "parquet", str(manifest["dataset_sha256"]), "test", "test", (),
            DatasetStatus.APPROVED_FOR_BACKTEST, QualityLevel.BACKTEST,
        )
        catalog = DataCatalog(root)
        catalog.register_product(product)
        catalog.register_release(release)
        catalog.save()
        return product, release

    def test_search_describe_doctor_and_strict_health_form_a_product_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            product, release = self._lake(directory)
            report = DataDiagnosticsService(directory).audit()
            self.assertTrue(report["healthy"])
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json", "data", "search",
                    "--dimension", "instrument=BTC-USDT", "--dimension", "frequency=1d",
                ]), 0)
                search = json.loads(output.getvalue())
                self.assertEqual(search["products"][0]["dataset"], str(product.key))
                self.assertNotIn("release_id", json.dumps(search))
                self.assertNotIn("logical_key", json.dumps(search))
                self.assertNotIn("selected_release", json.dumps(search))
                self.assertNotIn("layer", json.dumps(search))
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main(["--lake-root", directory, "data", "describe", "--dataset", str(product.key)]), 0)
                rendered = output.getvalue()
                self.assertIn(str(product.key), rendered)
                self.assertIn("ready_for_backtest", rendered)
                self.assertNotIn(release.release_id, rendered)
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main(["--lake-root", directory, "--format", "json", "data", "doctor", "--dataset", str(product.key)]), 0)
                self.assertIn('"healthy": true', output.getvalue())
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main(["--lake-root", directory, "--format", "json", "data", "diagnostics", "--strict"]), 0)
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "data", "query", "--dataset", release.release_id,
                    "--limit", "1",
                ]), 0)
                rendered = output.getvalue()
                self.assertIn(str(product.key), rendered)
                self.assertNotIn("Release", rendered)
            snapshot = Path(directory) / "studies" / "input_snapshot.json"
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "data", "freeze", "--study-id", "test-study",
                    "--dataset", release.release_id, "--output", str(snapshot),
                ]), 0)
                self.assertTrue(snapshot.exists())

    def test_strict_health_fails_with_actionable_missing_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            product, release = self._lake(directory)
            (Path(directory) / release.relative_path / "quality.json").unlink()
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main(["--lake-root", directory, "--format", "json", "data", "diagnostics", "--strict"]), 2)
                self.assertIn("missing_quality", output.getvalue())
            doctor = DataDiagnosticsService(directory).doctor(str(product.key))
            self.assertIn("missing_quality", doctor["issues"])


if __name__ == "__main__":
    unittest.main()
