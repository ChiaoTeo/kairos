from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairos.domain.identity import AssetId
from kairos.reference.identity import LocationId

from .transfer_contracts import TransferInstruction


@dataclass(frozen=True, slots=True)
class TransferPolicy:
    maximum_single_amount: dict[AssetId, Decimal]
    approved_destinations: frozenset[str] = frozenset()
    require_approval: bool = True

    def validate(self, instruction: TransferInstruction) -> None:
        asset = getattr(instruction, "asset_id", None)
        if asset is not None:
            maximum = self.maximum_single_amount.get(asset)
            if maximum is not None and instruction.amount > maximum:
                raise ValueError(f"transfer amount exceeds policy limit: {instruction.amount} > {maximum}")
        destination = getattr(instruction, "destination_address", None) or getattr(instruction, "account_reference", None)
        if destination is not None and destination not in self.approved_destinations:
            raise PermissionError("transfer destination is not approved")
