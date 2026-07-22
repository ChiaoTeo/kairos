from __future__ import annotations

import unittest
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from kairospy.data import DataCatalog, DatasetKey, DatasetLayer, DataProductDefinition, DatasetRelease
from kairospy.surface.data_features import SurfaceFeaturePublisher, load_surface_features
from kairospy.identity import InstrumentId
from kairospy.reference.contracts import OptionRight
from kairospy.analytics.volatility import CalibrationStatus, SviParameters, VolObservation, build_surface, surface_implied_volatility, total_variance


NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)
UNDERLYING = InstrumentId("index:spx")


def observations(expiry, maturity, parameters):
    result = []
    forward = Decimal("6000")
    from math import exp, sqrt
    for index, k in enumerate((Decimal("-0.20"), Decimal("-0.10"), Decimal("0"), Decimal("0.10"), Decimal("0.20"))):
        strike = forward * Decimal(str(exp(float(k))))
        variance = total_variance(k, parameters)
        iv = Decimal(str(sqrt(float(variance / maturity))))
        result.append(VolObservation(InstrumentId(f"option:{expiry.date()}:{index}"), UNDERLYING, NOW, expiry, strike, forward, maturity, OptionRight.CALL, Decimal("10"), iv))
    return result


class VolatilityTests(unittest.TestCase):
    def test_svi_calibration_is_deterministic_and_queryable(self) -> None:
        first_expiry, second_expiry = NOW + timedelta(days=30), NOW + timedelta(days=60)
        first = SviParameters(Decimal("0.005"), Decimal("0.08"), Decimal("-0.3"), Decimal("0"), Decimal("0.1"))
        second = SviParameters(Decimal("0.01"), Decimal("0.10"), Decimal("-0.2"), Decimal("0"), Decimal("0.12"))
        data = tuple(observations(first_expiry, Decimal("0.08219178"), first) + observations(second_expiry, Decimal("0.16438356"), second))
        available = NOW + timedelta(seconds=3)
        surface = build_surface(UNDERLYING, NOW, data, available_time=available)
        replay = build_surface(UNDERLYING, NOW, tuple(reversed(data)), available_time=available)
        self.assertEqual(surface.surface_id, replay.surface_id)
        self.assertEqual(surface.available_time, available)
        self.assertTrue(all(item.status is CalibrationStatus.CALIBRATED for item in surface.smiles))
        self.assertEqual(surface.calibration_status, CalibrationStatus.CALIBRATED)
        self.assertGreater(surface_implied_volatility(surface, first_expiry, Decimal("0")), 0)
        between = surface_implied_volatility(surface, NOW + timedelta(days=45), Decimal("0"))
        self.assertGreater(between, 0)
        with self.assertRaisesRegex(ValueError, "available_time"):
            build_surface(UNDERLYING, NOW, data, available_time=NOW - timedelta(seconds=1))

    def test_insufficient_smile_is_reported_not_silently_fitted(self) -> None:
        expiry = NOW + timedelta(days=30)
        params = SviParameters(Decimal("0.005"), Decimal("0.08"), Decimal("-0.3"), Decimal("0"), Decimal("0.1"))
        surface = build_surface(UNDERLYING, NOW, tuple(observations(expiry, Decimal("0.08"), params)[:4]))
        self.assertEqual(surface.smiles[0].status, CalibrationStatus.INSUFFICIENT_DATA)
        self.assertEqual(surface.calibration_status, CalibrationStatus.INSUFFICIENT_DATA)
        with self.assertRaises(LookupError):
            surface_implied_volatility(surface, expiry, Decimal("0"))

    def test_surface_feature_release_round_trip(self) -> None:
        expiry = NOW + timedelta(days=30)
        params = SviParameters(Decimal("0.005"), Decimal("0.08"), Decimal("-0.3"), Decimal("0"), Decimal("0.1"))
        surface = build_surface(UNDERLYING, NOW, tuple(observations(expiry, Decimal("0.08"), params)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = DataProductDefinition(
                DatasetKey("curated.market_snapshots.test.surface"), "Surface input", DatasetLayer.CURATED,
                "Frozen surface input", {"underlying": "SPX"}, owner="test",
            )
            catalog = DataCatalog(root)
            catalog.register_product(product)
            catalog.register_release(DatasetRelease(
                "surface-input", product.key, "1", "market_replay_dataset.v2", "2", "fixture", "1",
                "curated/input", "parquet", "input-hash",
            ))
            catalog.save()
            release = SurfaceFeaturePublisher(root).publish((surface,), input_release_id="surface-input")
            self.assertTrue((root / release.relative_path).exists())
            self.assertEqual(load_surface_features(root, release.release_id), (surface,))


if __name__ == "__main__":
    unittest.main()
