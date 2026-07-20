from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kairospy.__main__ import main
from kairospy.data import (
    DataCatalog, DatasetKey, DatasetLayer, DataProductDefinition, DatasetRelease, DatasetStatus, QualityLevel,
)
from kairospy.data.artifact_audit import audit_governed_artifact


class GovernedArtifactAuditTests(unittest.TestCase):
    def _catalog(self, root: str, *, quality=QualityLevel.BACKTEST, status=DatasetStatus.APPROVED_FOR_BACKTEST):
        catalog = DataCatalog(root)
        product = DataProductDefinition(DatasetKey("market.audit.input"), "Audit input", DatasetLayer.CURATED)
        catalog.register_product(product)
        catalog.register_release(DatasetRelease(
            "ds_audit_q3", product.key, "1", "audit.schema", "1", "audit", "1",
            "curated/audit", "parquet", "frozen-hash", status=status, quality_level=quality,
        ))
        catalog.save()

    def test_q3_frozen_input_passes_and_hash_or_quality_mismatch_fails(self) -> None:
        with TemporaryDirectory() as root:
            self._catalog(root)
            artifact = Path(root) / "artifact.json"
            artifact.write_text(json.dumps({
                "consumed_inputs": [{"release_id": "ds_audit_q3", "content_hash": "frozen-hash"}],
            }), encoding="utf-8")
            report = audit_governed_artifact(root, artifact)
            self.assertTrue(report.passed)
            artifact.write_text(json.dumps({
                "consumed_inputs": [{"release_id": "ds_audit_q3", "content_hash": "changed"}],
            }), encoding="utf-8")
            self.assertIn("content hash mismatch", audit_governed_artifact(root, artifact).violations[0])
        with TemporaryDirectory() as root:
            self._catalog(root, quality=QualityLevel.STUDY, status=DatasetStatus.APPROVED_FOR_STUDY)
            artifact = Path(root) / "artifact.json"
            artifact.write_text(json.dumps({
                "input": {"release_id": "ds_audit_q3", "content_hash": "frozen-hash"},
            }), encoding="utf-8")
            report = audit_governed_artifact(root, artifact)
            self.assertFalse(report.passed)
            self.assertTrue(any("below Q3" in item for item in report.violations))

    def test_cli_returns_nonzero_for_ungoverned_artifact(self) -> None:
        with TemporaryDirectory() as root:
            self._catalog(root)
            artifact = Path(root) / "artifact.json"
            artifact.write_text("{}", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--lake-root", root, "data", "audit-artifact", "--artifact", str(artifact)]), 2)


if __name__ == "__main__":
    unittest.main()
