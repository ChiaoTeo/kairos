from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from examples.backtest.governed_sma import canonical_events, fixture_bars
from kairospy.features import (
    FactorQuality, FactorRegistry, SmaFactorConfig, SmaFactorRuntime, batch_sma_factors,
    snapshots_hash,
)


class FactorRuntimeTests(unittest.TestCase):
    def test_sma_batch_and_canonical_incremental_are_identical(self) -> None:
        bars = fixture_bars()
        config = SmaFactorConfig(5, 15)
        batch = batch_sma_factors(bars, config, input_identity="fixture:sma-bars-v1")
        runtime = SmaFactorRuntime(config, input_identity="fixture:sma-bars-v1")
        replay = tuple(
            snapshot for event in canonical_events(bars)
            if (snapshot := runtime.update(event)) is not None
        )

        self.assertEqual(batch, replay)
        self.assertEqual(snapshots_hash(batch), snapshots_hash(replay))
        self.assertEqual(batch[13].quality, FactorQuality.WARMING_UP)
        self.assertEqual(batch[14].quality, FactorQuality.READY)
        self.assertIsNotNone(batch[-1].get("spread"))

    def test_factor_state_can_be_restored_without_changing_future_output(self) -> None:
        bars = fixture_bars()
        config = SmaFactorConfig(5, 15)
        uninterrupted = SmaFactorRuntime(config, input_identity="fixture:sma-bars-v1")
        expected = [uninterrupted.update_bar(bar) for bar in bars]

        first = SmaFactorRuntime(config, input_identity="fixture:sma-bars-v1")
        for bar in bars[:30]:
            first.update_bar(bar)
        restored = SmaFactorRuntime(config, input_identity="fixture:sma-bars-v1")
        restored.restore(first.dump_state())
        actual = [restored.update_bar(bar) for bar in bars[30:]]

        self.assertEqual(expected[30:], actual)

    def test_factor_registry_is_immutable_by_identity_and_version(self) -> None:
        spec = SmaFactorRuntime(
            SmaFactorConfig(5, 15), input_identity="fixture:sma-bars-v1",
        ).spec
        with tempfile.TemporaryDirectory() as directory:
            target = FactorRegistry(directory).register(spec)
            payload = json.loads((target / "factor_spec.json").read_text(encoding="utf-8"))
            manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(payload["factor_spec_hash"], spec.spec_hash)
            self.assertEqual(manifest["factor_spec_hash"], spec.spec_hash)
            self.assertEqual(FactorRegistry(directory).register(spec), target)


if __name__ == "__main__":
    unittest.main()
