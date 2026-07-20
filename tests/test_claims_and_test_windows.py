from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from kairos.research.validation import TestWindowRegistry,TestWindowUse
from tests.test_research_validation_framework import registration
from kairos.research.validation import (
    DataCapabilities,EvidenceStatus,OutOfSampleEvidence,ProductProtocol,ResearchValidationResult,
    SampleSufficiency,ValidationLevel,ValidationState,authorize_claim,
)


class ClaimsAndTestWindowsTest(unittest.TestCase):
    def test_trade_proxy_cannot_claim_cagr_or_capacity(self):
        result=ResearchValidationResult(registration(),ValidationState(EvidenceStatus.READY,EvidenceStatus.SUPPORTED,
            EvidenceStatus.DATA_NOT_READY,EvidenceStatus.TRADE_PROXY_ONLY,ValidationLevel.L3_MAPPING,"proxy"),
            DataCapabilities(("trades",),point_in_time_universe=True,supported_products=(ProductProtocol.OPTION,),maximum_validation_level=ValidationLevel.L3_MAPPING),
            SampleSufficiency(30,30,30,30),OutOfSampleEvidence.TIME,{})
        decision=authorize_claim(result,ValidationLevel.L4_EXECUTABLE,mentions_cagr=True,mentions_capacity=True)
        self.assertFalse(decision.allowed);self.assertEqual(len(decision.reasons),3)

    def test_decision_oos_window_cannot_reuse_consumed_period(self):
        with TemporaryDirectory() as directory:
            registry=TestWindowRegistry(Path(directory)/"windows.jsonl")
            registry.register(TestWindowUse("a","1","2025-01-01","2025-06-01","test",False))
            with self.assertRaises(ValueError):registry.register(TestWindowUse("b","1","2025-05-01","2025-08-01","confirm",True))


if __name__=="__main__":unittest.main()
