from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kairospy.study_platform.validation import audit_governance
from kairospy.storage.data_lake import write_daily_dataset
from kairospy.data import DatasetRelease
from kairospy.data.products import BTC_SPOT_DAILY


class GovernanceAuditTest(unittest.TestCase):
    def test_audit_reports_missing_dataset_capabilities(self):
        with TemporaryDirectory() as directory:
            # Unknown catalog datasets are intentionally ignored; create a known prepared path.
            from kairospy.data import DataCatalog
            release=DatasetRelease(str(BTC_SPOT_DAILY.key),BTC_SPOT_DAILY.key,"1",BTC_SPOT_DAILY.schema_id,"1","test","1",BTC_SPOT_DAILY.relative_path,"parquet","test-hash")
            root=Path(directory);catalog=DataCatalog(root);catalog.register_product(BTC_SPOT_DAILY.product);catalog.register_release(release);catalog.save();path=catalog.path(str(BTC_SPOT_DAILY.key))
            write_daily_dataset(path,[{"period_start":"2026-01-01T00:00:00Z","x":1}],dataset_id=str(BTC_SPOT_DAILY.key),
                schema={"schema_id":"x","columns":{}},lineage={"source":"test"})
            (path/"capabilities.json").unlink()
            audit=audit_governance(root)
            self.assertFalse(audit.passed)
            self.assertTrue(any("capabilities.json" in value for value in audit.violations))


if __name__=="__main__":unittest.main()
