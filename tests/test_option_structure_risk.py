from datetime import datetime,timezone
from decimal import Decimal
import unittest

from trading.domain.identity import AssetId
from trading.domain.product import CryptoOptionSpec,ExerciseStyle,OptionRight
from trading.risk.option_structure import maximum_expiry_loss


class OptionStructureRiskTest(unittest.TestCase):
    def test_crypto_iron_condor_has_finite_piecewise_maximum_loss(self):
        expiry=datetime(2026,8,1,tzinfo=timezone.utc);asset=AssetId("BTC");usd=AssetId("USD")
        def option(strike,right):return CryptoOptionSpec(asset,usd,usd,asset,expiry,Decimal(str(strike)),right,ExerciseStyle.EUROPEAN,Decimal("1"),"index")
        legs=((option(80_000,OptionRight.PUT),1),(option(90_000,OptionRight.PUT),-1),
              (option(110_000,OptionRight.CALL),-1),(option(120_000,OptionRight.CALL),1))
        self.assertEqual(maximum_expiry_loss(legs,Decimal("1000")),Decimal("9000"))

    def test_asymmetric_wing_uses_worst_side(self):
        expiry=datetime(2026,8,1,tzinfo=timezone.utc);asset=AssetId("BTC");usd=AssetId("USD")
        def option(strike,right):return CryptoOptionSpec(asset,usd,usd,asset,expiry,Decimal(str(strike)),right,ExerciseStyle.EUROPEAN,Decimal("1"),"index")
        legs=((option(85_000,OptionRight.PUT),1),(option(90_000,OptionRight.PUT),-1),
              (option(110_000,OptionRight.CALL),-1),(option(125_000,OptionRight.CALL),1))
        self.assertEqual(maximum_expiry_loss(legs,Decimal("1000")),Decimal("14000"))


if __name__=="__main__":unittest.main()
