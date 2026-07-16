from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from trading.research.validation import audit_governance
from trading.storage.data_lake import write_daily_dataset


class GovernanceAuditTest(unittest.TestCase):
    def test_audit_reports_missing_dataset_capabilities(self):
        with TemporaryDirectory() as directory:
            # Unknown catalog datasets are intentionally ignored; create a known prepared path.
            from trading.data import DataCatalog
            root=Path(directory);path=DataCatalog(root).path(DataCatalog.BTC_SPOT_DAILY.dataset_id)
            write_daily_dataset(path,[{"period_start":"2026-01-01T00:00:00Z","x":1}],dataset_id=DataCatalog.BTC_SPOT_DAILY.dataset_id,
                schema={"schema_id":"x","columns":{}},lineage={"source":"test"})
            (path/"capabilities.json").unlink()
            audit=audit_governance(root)
            self.assertFalse(audit.passed)
            self.assertTrue(any("capabilities.json" in value for value in audit.violations))


if __name__=="__main__":unittest.main()
