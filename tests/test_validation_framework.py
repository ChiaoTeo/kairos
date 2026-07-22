from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kairospy.research.validation import (
    CapitalSpec, DataCapabilities, DataGap, DataGapPlan, EvidenceStatus,
    ExecutionArchetype, GateRequirement, OutOfSampleEvidence, ProductProtocol,
    ExperimentValidationResult, ReturnDriver, SampleSufficiency, ExperimentRegistration,
    ValidationArtifactWriter, ValidationGate, ValidationLevel, ValidationState,
)


def registration(capital=True):
    return ExperimentRegistration(
        "btc_condor", "1.0.0", "fear premium predicts condor returns",
        (ProductProtocol.OPTION,), ("short_volatility",), (ReturnDriver.VOLATILITY,),
        ("gamma", "jump"), ExecutionArchetype.TAKER,
        ("2021-01-01", "2024-01-01"), None, ("2024-01-01", "2026-01-01"),
        ("skew", "atm_iv"), ("net_pnl",), (14,), "mean_net_return", 50,
        "bootstrap lower bound > 0", "bootstrap upper bound <= 0",
        ("synchronous_quotes", "quote_size"),
        CapitalSpec(Decimal("100000"), "USD", Decimal(".02"), Decimal(".10"),
                    "venue_portfolio_margin_v1", True, False, "usd_cash_rate_v1", "maintenance_margin") if capital else None,
        created_at="2026-07-15T00:00:00+00:00",
    )


class ValidationFrameworkTest(unittest.TestCase):
    def test_spec_hash_is_stable_and_ignores_registration_time(self):
        first = registration(); second = registration()
        self.assertEqual(first.spec_hash, second.spec_hash)

    def test_queue_capability_requires_event_data(self):
        with self.assertRaises(ValueError):
            DataCapabilities(("quotes",), queue_reconstructable=True)

    def test_executable_gate_rejects_trade_only_data(self):
        result = ExperimentValidationResult(
            registration(),
            ValidationState(EvidenceStatus.READY, EvidenceStatus.SUPPORTED,
                            EvidenceStatus.DATA_NOT_READY, EvidenceStatus.TRADE_PROXY_ONLY,
                            ValidationLevel.L3_MAPPING, "trade proxy only"),
            DataCapabilities(("deribit_trades",), trade_events=True, trade_direction=True,
                             point_in_time_universe=True, maximum_validation_level=ValidationLevel.L3_MAPPING,
                             supported_products=(ProductProtocol.OPTION,)),
            SampleSufficiency(100, 30, 30, 30), OutOfSampleEvidence.TIME, {},
            data_gap_plan=DataGapPlan((DataGap("synchronization", "synchronous_quotes", ValidationLevel.L4_EXECUTABLE, "capture quotes"),)),
        )
        decision = ValidationGate().evaluate(result, GateRequirement(
            ValidationLevel.L4_EXECUTABLE, 30, OutOfSampleEvidence.TIME,
            ExecutionArchetype.TAKER, multi_leg=True, require_capital_spec=True,
        ))
        self.assertFalse(decision.passed)
        self.assertTrue(any("synchronous" in reason for reason in decision.reasons))

    def test_artifact_writer_emits_governed_files_and_audit_hashes(self):
        result = ExperimentValidationResult(
            registration(), ValidationState(EvidenceStatus.READY, EvidenceStatus.SUPPORTED,
                EvidenceStatus.SUPPORTED, EvidenceStatus.SUPPORTED, ValidationLevel.L4_EXECUTABLE, "executable backtest"),
            DataCapabilities(("quotes",), synchronous_quotes=True, top_of_book=True, quote_size=True,
                point_in_time_universe=True, supported_products=(ProductProtocol.OPTION,), maximum_validation_level=ValidationLevel.L4_EXECUTABLE),
            SampleSufficiency(80, 60, 55, 50), OutOfSampleEvidence.DECISION, {"mean_return": .01},
        )
        with TemporaryDirectory() as directory:
            output = ValidationArtifactWriter(directory).write(result, report="# Result")
            expected = {"experiment_spec.json", "data_capabilities.json", "data_quality.json", "sample_sufficiency.json",
                        "data_gap_plan.json", "capital_spec.json", "results.json", "REPORT.md", "audit.json"}
            self.assertEqual({path.name for path in output.iterdir()}, expected)
            audit = json.loads((output / "audit.json").read_text())
            self.assertEqual(audit["spec_hash"], result.spec_hash)
            self.assertIn("results.json", audit["artifact_hashes"])

    def test_artifact_writer_accepts_only_safe_unique_json_extras(self):
        result = ExperimentValidationResult(
            registration(), ValidationState(EvidenceStatus.READY, EvidenceStatus.SUPPORTED,
                EvidenceStatus.SUPPORTED, EvidenceStatus.SUPPORTED, ValidationLevel.L4_EXECUTABLE, "ok"),
            DataCapabilities(("quotes",), synchronous_quotes=True, top_of_book=True, quote_size=True,
                point_in_time_universe=True, supported_products=(ProductProtocol.OPTION,), maximum_validation_level=ValidationLevel.L4_EXECUTABLE),
            SampleSufficiency(80, 60, 55, 50), OutOfSampleEvidence.DECISION, {})
        with TemporaryDirectory() as directory:
            output = ValidationArtifactWriter(directory).write(result, report="# R",
                extra_artifacts={"execution_spec.json": {"mode": "taker"}})
            self.assertTrue((output / "execution_spec.json").exists())
            with self.assertRaises(ValueError):
                ValidationArtifactWriter(directory).write(result, report="# R",
                    extra_artifacts={"../escape.json": {}})

    def test_robustness_and_live_gates_require_specific_evidence(self):
        result = ExperimentValidationResult(
            registration(), ValidationState(EvidenceStatus.READY, EvidenceStatus.SUPPORTED,
                EvidenceStatus.SUPPORTED, EvidenceStatus.SUPPORTED, ValidationLevel.L6_LIVE, "live"),
            DataCapabilities(("quotes",), synchronous_quotes=True, top_of_book=True, quote_size=True,
                point_in_time_universe=True, settlement_price=True, lifecycle_events=True,
                supported_products=(ProductProtocol.OPTION,), maximum_validation_level=ValidationLevel.L6_LIVE),
            SampleSufficiency(100,100,100,50), OutOfSampleEvidence.DECISION,
            {"robustness":{"parameter_stable":True,"regime_stable":True,"stress_cost_passed":True},
             "deployment":{"paper_or_live_evidence":True,"reconciliation_passed":True,"risk_controls_passed":True}})
        decision=ValidationGate().evaluate(result,GateRequirement(ValidationLevel.L6_LIVE,50,OutOfSampleEvidence.DECISION,
            ExecutionArchetype.TAKER,multi_leg=True,require_capital_spec=True))
        self.assertTrue(decision.passed,decision.reasons)


if __name__ == "__main__":
    unittest.main()
