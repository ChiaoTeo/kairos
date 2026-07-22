import unittest

from kairospy.research.validation import (
    DataCapabilities, ProductProtocol, ReturnDriver, ValidationLevel,
    approximate_required_samples, assess_sample_sufficiency, build_data_gap_plan,
    validate_product_protocol,
)


class ValidationProtocolsTest(unittest.TestCase):
    def test_option_executable_protocol_requires_synchronous_lifecycle_data(self):
        data = DataCapabilities(("trades",), point_in_time_universe=True,
            trade_events=True, supported_products=(ProductProtocol.OPTION,),
            maximum_validation_level=ValidationLevel.L3_MAPPING)
        decision = validate_product_protocol((ProductProtocol.OPTION,), data, ValidationLevel.L4_EXECUTABLE)
        self.assertFalse(decision.passed)
        self.assertIn("synchronous_multi_leg_quotes", decision.missing_capabilities)
        self.assertIn("option_lifecycle_events", decision.missing_capabilities)

    def test_perpetual_protocol_requires_funding_for_executable_carry(self):
        data = DataCapabilities(("perp",), point_in_time_universe=True, synchronous_quotes=True,
            top_of_book=True, quote_size=True, lifecycle_events=True,
            supported_products=(ProductProtocol.PERPETUAL,), maximum_validation_level=ValidationLevel.L4_EXECUTABLE)
        decision = validate_product_protocol((ProductProtocol.PERPETUAL,), data, ValidationLevel.L4_EXECUTABLE)
        self.assertIn("funding", decision.missing_capabilities)

    def test_overlap_and_power_are_explicit_not_row_count(self):
        sample = assess_sample_sufficiency(300, 30, .5)
        self.assertEqual(sample.effective_observations, 10)
        self.assertGreater(sample.required_effective_observations, sample.effective_observations)
        self.assertEqual(sample.additional_samples_required,
                         sample.required_effective_observations - 10)
        self.assertEqual(approximate_required_samples(.5), sample.required_effective_observations)

    def test_gap_planner_explains_remediation_and_recheck(self):
        plan = build_data_gap_plan(("synchronous_quotes", "quote_size"), target_samples=50,
                                   collection_frequency="1h")
        self.assertEqual(plan.blocked_capabilities, ("synchronous_quotes", "quote_size"))
        self.assertEqual(plan.target_samples, 50)
        self.assertIn("50", plan.reevaluation_condition)


if __name__ == "__main__": unittest.main()
