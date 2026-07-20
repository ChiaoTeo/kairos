from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from kairos.domain.identity import InstrumentId
from kairos.domain.intent import TargetExposureIntent
from kairos.features import FactorQuality, FactorSnapshot, SmaFactorConfig, SmaFactorRuntime
from kairos.risk.portfolio_governance import PositionSizer
from kairos.strategies import SmaCrossStrategy, SmaCrossStrategyConfig, StrategyContext


INSTRUMENT = InstrumentId("crypto:binance:spot:BTCUSDT")
NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Market:
    timestamp: datetime


def factor(spread: str | None, quality: FactorQuality = FactorQuality.READY) -> FactorSnapshot:
    spec = SmaFactorRuntime(SmaFactorConfig(2, 3), input_identity="fixture").spec
    value = Decimal(spread) if spread is not None else None
    return FactorSnapshot(
        spec.factor_id, spec.version, spec.spec_hash, INSTRUMENT, NOW,
        (("fast_sma", Decimal("2")), ("slow_sma", Decimal("1")), ("spread", value)),
        3, quality, "fixture", "a" * 64,
    )


def context(snapshot: FactorSnapshot) -> StrategyContext:
    return StrategyContext(Market(NOW), object(), (), object(), factor_snapshots=(snapshot,))


class SmaStrategyTests(unittest.TestCase):
    def test_strategy_emits_exposure_not_account_quantity(self) -> None:
        strategy = SmaCrossStrategy(SmaCrossStrategyConfig(INSTRUMENT))
        intents = strategy.on_market(context(factor("0.5")))

        self.assertEqual(len(intents), 1)
        self.assertIsInstance(intents[0], TargetExposureIntent)
        self.assertEqual(intents[0].target_fraction, Decimal("1"))
        self.assertEqual(strategy.on_market(context(factor("0.6"))), ())

    def test_strategy_waits_for_governed_factor_warmup(self) -> None:
        strategy = SmaCrossStrategy(SmaCrossStrategyConfig(INSTRUMENT))
        self.assertEqual(strategy.on_market(context(factor(None, FactorQuality.WARMING_UP))), ())
        self.assertEqual(strategy.decisions[-1].action, "warmup")

    def test_portfolio_sizer_converts_exposure_after_capital_approval(self) -> None:
        exposure = SmaCrossStrategy(SmaCrossStrategyConfig(INSTRUMENT)).on_market(
            context(factor("0.5")),
        )[0]
        decision = PositionSizer().size(
            exposure, approved_capital=Decimal("1000"), reference_price=Decimal("250"),
            lot_size=Decimal("0.1"),
        )

        self.assertTrue(decision.approved)
        self.assertEqual(decision.intent.target_quantity, Decimal("4.0"))


if __name__ == "__main__":
    unittest.main()
