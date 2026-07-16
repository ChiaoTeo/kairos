from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

try:
    import pandas  # noqa: F401
except ImportError:
    pandas = None

if pandas is not None:
    from research.spxw_put_skew.study import ResearchConfig, analyze_hypothesis, build_panel, execute_research
    from tests.test_options_research_end_to_end import internally_priceable_spxw_dataset
    from trading.research.data_store import CollectionManifest, CollectionSession


@unittest.skipIf(pandas is None, "install the notebook optional dependencies")
class SpxwPutSkewStudyTests(unittest.TestCase):
    def test_panel_is_point_in_time_and_current_fixture_is_insufficient(self) -> None:
        dataset = internally_priceable_spxw_dataset()
        config = ResearchConfig(
            min_dte=0, max_dte=7, forward_horizon_days=1, minimum_rank_history=2,
            target_short_delta=Decimal("-0.35"), target_long_delta=Decimal("-0.20"),
            minimum_observations=252, bootstrap_samples=20,
        )
        panel = build_panel(dataset, config)
        self.assertEqual(len(panel), 1)
        self.assertTrue(panel["skew_rank"].isna().all())
        self.assertTrue(panel.iloc[-1:]["spread_pnl"].isna().all())
        conclusion = analyze_hypothesis(panel, config)
        self.assertEqual(conclusion.status, "INSUFFICIENT_DATA")
        _, readiness, guarded = execute_research(dataset, config, None)
        self.assertFalse(readiness.ready)
        self.assertEqual(guarded.status, "DATA_NOT_READY")
        self.assertIn("dataset_is_synthetic", readiness.reasons)
        self.assertIn("missing_real_collection_session", readiness.reasons)

    def test_invalid_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ResearchConfig(min_dte=45, max_dte=7)
        with self.assertRaises(ValueError):
            ResearchConfig(high_skew_percentile=Decimal("1"))

    def test_massive_canonical_collection_is_a_verified_real_source(self) -> None:
        dataset = internally_priceable_spxw_dataset()
        session = CollectionSession("hash", datetime.now(timezone.utc), dataset.manifest.start, dataset.manifest.end,
                                    dataset.manifest.slice_count, "massive.canonical:options.us.massive.spxw.v1", False)
        config = ResearchConfig(min_dte=0, max_dte=7, forward_horizon_days=1, minimum_rank_history=2, minimum_observations=252, bootstrap_samples=20)
        _, readiness, _ = execute_research(dataset, config, CollectionManifest(1, dataset.manifest.dataset_id, (session,)))
        self.assertNotIn("unverified_collection_source", readiness.reasons)


if __name__ == "__main__":
    unittest.main()
