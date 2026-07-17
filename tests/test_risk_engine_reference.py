from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from uuid import UUID

from trading.backtest.feed import MarketSlice
from trading.backtest.portfolio import PortfolioSnapshot
from trading.domain.execution import TradeSide
from trading.domain.capability import TimeInForce
from trading.domain.identity import AssetId, InstrumentId
from trading.domain.intent import LegIntent, OpenStructureIntent
from trading.domain.market_data import Greeks, Quote
from trading.domain.product import ExerciseStyle, ListedOptionSpec, OptionRight, ProductType, SettlementSession, SettlementType
from trading.reference import EconomicProduct, InstrumentDefinition, InstrumentLifecycle, ProductId, ReferenceCatalog
from trading.research.snapshot import InstrumentSnapshot
from trading.risk.engine import RiskDecisionType, RiskEngine
from trading.risk.limits import RiskLimits


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class RiskEngineReferenceTests(unittest.TestCase):
    def test_option_risk_engine_reads_current_contract_specs(self) -> None:
        catalog = ReferenceCatalog(); underlying = InstrumentId("equity:us:AAPL")
        product = ProductId("product:options:AAPL")
        catalog.products.add(EconomicProduct(product, ProductType.LISTED_OPTION, "AAPL options", NOW, currency=AssetId("USD")))
        instruments = []
        for right, strike, suffix in ((OptionRight.PUT, Decimal("190"), "P190"), (OptionRight.PUT, Decimal("180"), "P180")):
            instrument = InstrumentId(f"option:AAPL:{suffix}")
            spec = ListedOptionSpec(underlying, NOW + timedelta(days=30), strike, right, ExerciseStyle.AMERICAN, SettlementType.PHYSICAL, SettlementSession.PM, Decimal("100"), NOW + timedelta(days=30))
            catalog.instruments.add(InstrumentDefinition(instrument, product, ProductType.LISTED_OPTION, spec, InstrumentLifecycle(), NOW))
            quote = Quote(instrument, Decimal("2"), Decimal("2.2"), Decimal("1"), Decimal("1"), NOW)
            greeks = Greeks(instrument, Decimal("0.2"), Decimal("-0.2"), Decimal("0.01"), Decimal("-0.1"), Decimal("0.2"), NOW)
            instruments.append(InstrumentSnapshot(instrument, quote, NOW, None, None, greeks, NOW))
        intent = OpenStructureIntent("strategy", tuple((LegIntent(item.instrument_id, TradeSide.SELL if index == 0 else TradeSide.BUY, 1)) for index, item in enumerate(instruments)), 1, Decimal("1"), TimeInForce.DAY, "test", UUID(int=1))
        portfolio = PortfolioSnapshot(
            NOW, Decimal("100000"), Decimal("100000"), Decimal("100000"), Decimal("100000"),
            Decimal("100000"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
            (), (), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("1"), (), (), Decimal("0"), 0,
        )
        market = MarketSlice(NOW, tuple(instruments))
        approved, decision = RiskEngine(RiskLimits(), catalog, id_factory=lambda: UUID(int=2)).evaluate(intent, portfolio, market)
        self.assertIsNotNone(approved)
        self.assertIn(decision.decision, {RiskDecisionType.APPROVED, RiskDecisionType.RESIZED})


if __name__ == "__main__":
    unittest.main()
