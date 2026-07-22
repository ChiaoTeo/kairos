from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest
from uuid import UUID

from kairospy.identity import AccountRef, AccountType, AssetId, InstrumentId, InstitutionId, VenueId
from kairospy.portfolio.ledger import Ledger, LedgerBook, LedgerEntry, LedgerEntryType, LedgerTransaction
from kairospy.reference import ReferenceCatalog
from kairospy.reference.contracts import (
    CryptoOptionSpec,
    EquitySpec,
    ExerciseStyle,
    ListedOptionSpec,
    OptionRight,
    ProductType,
    SettlementSession,
    SettlementType,
)
from kairospy.risk.extensions.covered_call import (
    CoveredCallCollateralRequest,
    covered_call_collateral_evidence,
    validate_covered_call_collateral,
)
from kairospy.risk.option_structure import maximum_expiry_loss
from tests.reference_support import publish_test_instrument


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


class OptionStructureRiskTest(unittest.TestCase):
    def test_crypto_iron_condor_has_finite_piecewise_maximum_loss(self) -> None:
        expiry = datetime(2026, 8, 1, tzinfo=timezone.utc)
        asset = AssetId("BTC")
        usd = AssetId("USD")

        def option(strike: int, right: OptionRight) -> CryptoOptionSpec:
            return CryptoOptionSpec(
                asset,
                usd,
                usd,
                asset,
                expiry,
                Decimal(str(strike)),
                right,
                ExerciseStyle.EUROPEAN,
                Decimal("1"),
                "index",
            )

        legs = (
            (option(80_000, OptionRight.PUT), 1),
            (option(90_000, OptionRight.PUT), -1),
            (option(110_000, OptionRight.CALL), -1),
            (option(120_000, OptionRight.CALL), 1),
        )
        self.assertEqual(maximum_expiry_loss(legs, Decimal("1000")), Decimal("9000"))

    def test_asymmetric_wing_uses_worst_side(self) -> None:
        expiry = datetime(2026, 8, 1, tzinfo=timezone.utc)
        asset = AssetId("BTC")
        usd = AssetId("USD")

        def option(strike: int, right: OptionRight) -> CryptoOptionSpec:
            return CryptoOptionSpec(
                asset,
                usd,
                usd,
                asset,
                expiry,
                Decimal(str(strike)),
                right,
                ExerciseStyle.EUROPEAN,
                Decimal("1"),
                "index",
            )

        legs = (
            (option(85_000, OptionRight.PUT), 1),
            (option(90_000, OptionRight.PUT), -1),
            (option(110_000, OptionRight.CALL), -1),
            (option(125_000, OptionRight.CALL), 1),
        )
        self.assertEqual(maximum_expiry_loss(legs, Decimal("1000")), Decimal("14000"))


class CoveredCallCollateralExtensionTest(unittest.TestCase):
    def test_covered_call_extension_returns_collateral_evidence(self) -> None:
        catalog, equity_id, option_id = _listed_call_catalog()
        account = AccountRef(InstitutionId("ibkr"), "U123", AccountType.SECURITIES_MARGIN)
        ledger = _ledger_with_position(account, equity_id, Decimal("100"))
        request = CoveredCallCollateralRequest(equity_id, option_id, Decimal("1"))

        evidence = validate_covered_call_collateral(request, account, ledger, catalog, NOW)

        self.assertTrue(evidence.passed)
        self.assertEqual(evidence.required_shares, Decimal("100"))
        self.assertEqual(evidence.held_shares, Decimal("100"))

        naked = CoveredCallCollateralRequest(equity_id, option_id, Decimal("2"))
        naked_evidence = covered_call_collateral_evidence(naked, account, ledger, catalog, NOW)
        self.assertFalse(naked_evidence.passed)
        self.assertEqual(naked_evidence.reason, "naked call prohibited")
        self.assertEqual(naked_evidence.required_shares, Decimal("200"))
        self.assertEqual(naked_evidence.held_shares, Decimal("100"))
        with self.assertRaisesRegex(ValueError, "naked call prohibited"):
            validate_covered_call_collateral(naked, account, ledger, catalog, NOW)

    def test_covered_call_extension_rejects_underlying_mismatch(self) -> None:
        catalog, _, option_id = _listed_call_catalog()
        account = AccountRef(InstitutionId("ibkr"), "U123", AccountType.SECURITIES_MARGIN)
        other_equity = InstrumentId("equity:msft")
        ledger = _ledger_with_position(account, other_equity, Decimal("1000"))
        request = CoveredCallCollateralRequest(other_equity, option_id, Decimal("1"))

        evidence = covered_call_collateral_evidence(request, account, ledger, catalog, NOW)

        self.assertFalse(evidence.passed)
        self.assertEqual(evidence.reason, "covered call option underlying mismatch")


def _listed_call_catalog() -> tuple[ReferenceCatalog, InstrumentId, InstrumentId]:
    catalog = ReferenceCatalog()
    equity_id = InstrumentId("equity:aapl")
    option_id = InstrumentId("option:aapl:20260821:150:c")
    expiry = datetime(2026, 8, 21, tzinfo=timezone.utc)
    publish_test_instrument(
        catalog,
        equity_id,
        ProductType.EQUITY,
        "AAPL",
        EquitySpec("XNAS", "US", AssetId("USD")),
        AssetId("USD"),
        VenueId("xnas"),
        "AAPL",
        NOW,
    )
    publish_test_instrument(
        catalog,
        option_id,
        ProductType.LISTED_OPTION,
        "AAPL 150C",
        ListedOptionSpec(
            equity_id,
            expiry,
            Decimal("150"),
            OptionRight.CALL,
            ExerciseStyle.AMERICAN,
            SettlementType.PHYSICAL,
            SettlementSession.PM,
            Decimal("100"),
            expiry,
        ),
        AssetId("USD"),
        VenueId("opra"),
        "AAPL260821C00150000",
        NOW,
    )
    return catalog, equity_id, option_id


def _ledger_with_position(account: AccountRef, instrument_id: InstrumentId, shares: Decimal) -> Ledger:
    ledger = Ledger()
    transaction_id = UUID(int=1)
    asset = AssetId(f"POSITION:{instrument_id.value}")
    ledger.post(
        LedgerTransaction(
            transaction_id,
            NOW,
            "seed-position",
            (
                LedgerEntry(
                    UUID(int=2),
                    transaction_id,
                    NOW,
                    account,
                    LedgerBook.POSITION,
                    asset,
                    shares,
                    LedgerEntryType.DEPOSIT,
                    "seed-position",
                    instrument_id,
                ),
                LedgerEntry(
                    UUID(int=3),
                    transaction_id,
                    NOW,
                    account,
                    LedgerBook.EXTERNAL,
                    asset,
                    -shares,
                    LedgerEntryType.DEPOSIT,
                    "seed-position",
                    instrument_id,
                ),
            ),
        )
    )
    return ledger


if __name__ == "__main__":
    unittest.main()
