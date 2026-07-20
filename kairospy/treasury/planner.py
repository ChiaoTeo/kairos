from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from uuid import NAMESPACE_URL, uuid5

from kairospy.reference import ReferenceCatalog
from kairospy.reference.identity import NetworkId

from .transfer_contracts import (
    AmountMode, AssetMovementIntent, BankAccountDestination,
    BankTransferInstruction, CryptoAddressDestination, CryptoTransferInstruction,
    InternalAccountDestination, InternalTransferInstruction,
)


class TreasuryPlanner:
    def __init__(self, catalog: ReferenceCatalog) -> None:
        self.catalog = catalog

    def plan(self, intent: AssetMovementIntent, at, *, available_amount: Decimal | None = None):
        amount = available_amount if intent.amount_mode is AmountMode.ALL else intent.requested_amount
        if amount is None or amount <= 0:
            raise ValueError("planned transfer amount must be positive")
        instruction_id = str(uuid5(NAMESPACE_URL, f"treasury-instruction:{intent.intent_id}"))
        idempotency_key = str(uuid5(NAMESPACE_URL, f"treasury-idempotency:{intent.intent_id}"))
        destination = intent.destination
        if isinstance(destination, InternalAccountDestination):
            return InternalTransferInstruction(instruction_id, intent.intent_id, intent.source_location_id, destination.location_id, intent.asset_id, amount, idempotency_key)
        if isinstance(destination, CryptoAddressDestination):
            network_asset = self.catalog.network_assets.get(destination.network_asset_id, at)
            if network_asset.asset_id != intent.asset_id or not network_asset.withdrawal_enabled:
                raise ValueError("network asset is not withdrawable for requested asset")
            if network_asset.minimum_withdrawal is not None and amount < network_asset.minimum_withdrawal:
                raise ValueError("transfer amount is below network minimum")
            quantum = Decimal(1).scaleb(-network_asset.decimals)
            normalized = amount.quantize(quantum, rounding=ROUND_DOWN)
            return CryptoTransferInstruction(instruction_id, intent.intent_id, intent.source_location_id, destination.network_asset_id, destination.address, normalized, intent.fee_policy, idempotency_key, destination.memo)
        if isinstance(destination, BankAccountDestination):
            rail = self.catalog.rails.get(destination.rail_id, at)
            if intent.asset_id not in rail.supported_assets:
                raise ValueError("settlement rail does not support requested asset")
            if rail.minimum_amount is not None and amount < rail.minimum_amount:
                raise ValueError("transfer amount is below rail minimum")
            if rail.maximum_amount is not None and amount > rail.maximum_amount:
                raise ValueError("transfer amount exceeds rail maximum")
            return BankTransferInstruction(instruction_id, intent.intent_id, intent.source_location_id, destination.beneficiary_id, destination.account_reference, destination.rail_id, intent.asset_id, amount, intent.fee_policy, idempotency_key)
        raise TypeError(f"unsupported transfer destination: {type(destination).__name__}")
