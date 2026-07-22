from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from uuid import uuid4

from kairospy.identity import AccountRef, AccountType, AssetId, VenueId
from kairospy.reference import (
    NetworkAssetDefinition, NetworkDefinition, NetworkType, RailId, RailType,
    ReferenceCatalog, SettlementRail,
)
from kairospy.reference.identity import LocationId, NetworkAssetId, NetworkId
from kairospy.portfolio.treasury import (
    AmountMode, AssetMovementIntent, BankAccountDestination,
    CryptoAddressDestination, FeePolicy, InternalAccountDestination,
    TransferOperationStore, TreasuryCoordinator, TreasuryPlanner,
)
from kairospy.portfolio.treasury.transfer_gateway import SimulatedTransferGateway
from kairospy.portfolio.treasury.policy import TransferPolicy


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)
SOURCE = LocationId("location:exchange:spot")


class TreasuryPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = ReferenceCatalog()
        network = NetworkDefinition(NetworkId("ethereum"), NetworkType.BLOCKCHAIN, "Ethereum", NOW, native_asset=AssetId("ETH"), minimum_confirmations=12)
        token = NetworkAssetDefinition(NetworkAssetId("ethereum:usdt"), AssetId("USDT"), network.network_id, 6, NOW, contract_address="0xusdt", minimum_withdrawal=Decimal("10"), withdrawal_fee=Decimal("1"))
        rail = SettlementRail(RailId("fedwire:usd"), RailType.FEDWIRE, (AssetId("USD"),), NOW, minimum_amount=Decimal("100"), maximum_amount=Decimal("1000000"))
        self.catalog.networks.add(network)
        self.catalog.network_assets.add(token)
        self.catalog.rails.add(rail)
        self.planner = TreasuryPlanner(self.catalog)

    def test_crypto_instruction_uses_network_asset_precision(self) -> None:
        intent = AssetMovementIntent(uuid4(), "fund", SOURCE, CryptoAddressDestination(NetworkAssetId("ethereum:usdt"), "0xabc"), AssetId("USDT"), Decimal("10.1234567"), AmountMode.GROSS, FeePolicy.ADD_TO_AMOUNT, "rebalance")
        instruction = self.planner.plan(intent, NOW)
        self.assertEqual(instruction.amount, Decimal("10.123456"))
        self.assertEqual(instruction.network_asset_id, NetworkAssetId("ethereum:usdt"))

    def test_bank_instruction_enforces_rail_limits_and_asset(self) -> None:
        destination = BankAccountDestination("beneficiary-1", RailId("fedwire:usd"), "masked-account")
        intent = AssetMovementIntent(uuid4(), "fund", SOURCE, destination, AssetId("USD"), Decimal("500"), AmountMode.NET, FeePolicy.ADD_TO_AMOUNT, "cash sweep")
        instruction = self.planner.plan(intent, NOW)
        self.assertEqual(instruction.amount, Decimal("500"))
        invalid = AssetMovementIntent(uuid4(), "fund", SOURCE, destination, AssetId("EUR"), Decimal("500"), AmountMode.NET, FeePolicy.ADD_TO_AMOUNT, "cash sweep")
        with self.assertRaises(ValueError):
            self.planner.plan(invalid, NOW)

    def test_coordinator_is_idempotent_and_requires_approved_destination(self) -> None:
        intent = AssetMovementIntent(uuid4(), "fund", SOURCE, CryptoAddressDestination(NetworkAssetId("ethereum:usdt"), "0xabc"), AssetId("USDT"), Decimal("100"), AmountMode.GROSS, FeePolicy.ADD_TO_AMOUNT, "rebalance")
        instruction = self.planner.plan(intent, NOW)
        store = TransferOperationStore()
        coordinator = TreasuryCoordinator(store, SimulatedTransferGateway(), TransferPolicy({AssetId("USDT"): Decimal("1000")}, frozenset({"0xabc"})))
        first = coordinator.create(instruction, None, AssetId("USDT"), NOW)
        second = coordinator.create(instruction, None, AssetId("USDT"), NOW)
        self.assertEqual(first.transfer_id, second.transfer_id)
        with self.assertRaises(PermissionError):
            coordinator.validate_and_approve(first.transfer_id, instruction, NOW + timedelta(seconds=1))
        coordinator.approve(first.transfer_id, NOW + timedelta(seconds=1), actor="operator", reason="approved destination and amount")
        submitted = coordinator.submit(first.transfer_id, instruction, NOW + timedelta(seconds=2))
        self.assertEqual(submitted.provider_reference, f"sim:{instruction.instruction_id}")
        self.assertEqual(len(store.events(first.transfer_id)), 4)


if __name__ == "__main__":
    unittest.main()
