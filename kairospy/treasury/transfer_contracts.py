from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TypeAlias
from uuid import UUID

from kairospy.trading.identity import AccountKey, AssetId
from kairospy.reference.identity import InstitutionId, LocationId, NetworkAssetId, NetworkId, RailId


class LocationType(StrEnum):
    BROKER_ACCOUNT = "broker_account"
    EXCHANGE_SPOT = "exchange_spot"
    EXCHANGE_MARGIN = "exchange_margin"
    EXCHANGE_DERIVATIVES = "exchange_derivatives"
    BANK_ACCOUNT = "bank_account"
    CUSTODIAL_WALLET = "custodial_wallet"
    ONCHAIN_WALLET = "onchain_wallet"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class AssetLocation:
    location_id: LocationId
    location_type: LocationType
    institution_id: InstitutionId | None = None
    account_key: AccountKey | None = None
    network_id: NetworkId | None = None
    address: str | None = None

    def __post_init__(self) -> None:
        if self.location_type is LocationType.ONCHAIN_WALLET:
            if self.network_id is None or not self.address or self.account_key is not None:
                raise ValueError("on-chain location requires network/address and no account")
        elif self.location_type is not LocationType.EXTERNAL and self.account_key is None:
            raise ValueError("controlled non-chain location requires an account")


@dataclass(frozen=True, slots=True)
class InternalAccountDestination:
    location_id: LocationId


@dataclass(frozen=True, slots=True)
class CryptoAddressDestination:
    network_asset_id: NetworkAssetId
    address: str
    memo: str | None = None

    def __post_init__(self) -> None:
        if not self.address.strip():
            raise ValueError("crypto destination address cannot be empty")


@dataclass(frozen=True, slots=True)
class BankAccountDestination:
    beneficiary_id: str
    rail_id: RailId
    account_reference: str

    def __post_init__(self) -> None:
        if not self.beneficiary_id.strip() or not self.account_reference.strip():
            raise ValueError("bank destination fields cannot be empty")


TransferDestination: TypeAlias = InternalAccountDestination | CryptoAddressDestination | BankAccountDestination


class AmountMode(StrEnum):
    GROSS = "gross"
    NET = "net"
    ALL = "all"


class FeePolicy(StrEnum):
    DEDUCT_FROM_AMOUNT = "deduct_from_amount"
    ADD_TO_AMOUNT = "add_to_amount"
    SEPARATE_ASSET = "separate_asset"


@dataclass(frozen=True, slots=True)
class AssetMovementIntent:
    intent_id: UUID
    owner_id: str
    source_location_id: LocationId
    destination: TransferDestination
    asset_id: AssetId
    requested_amount: Decimal
    amount_mode: AmountMode
    fee_policy: FeePolicy
    reason: str
    preferred_rail_id: RailId | None = None

    def __post_init__(self) -> None:
        if not self.owner_id.strip() or not self.reason.strip():
            raise ValueError("movement owner and reason cannot be empty")
        if self.amount_mode is not AmountMode.ALL and self.requested_amount <= 0:
            raise ValueError("movement amount must be positive")
        if self.amount_mode is AmountMode.ALL and self.requested_amount != 0:
            raise ValueError("ALL movement amount must be zero until planning")


@dataclass(frozen=True, slots=True)
class InternalTransferInstruction:
    instruction_id: str
    intent_id: UUID
    source_location_id: LocationId
    destination_location_id: LocationId
    asset_id: AssetId
    amount: Decimal
    idempotency_key: str

    def __post_init__(self) -> None:
        if self.source_location_id == self.destination_location_id or self.amount <= 0:
            raise ValueError("internal transfer requires distinct locations and positive amount")
        if not self.instruction_id.strip() or not self.idempotency_key.strip():
            raise ValueError("instruction identity cannot be empty")


@dataclass(frozen=True, slots=True)
class CryptoTransferInstruction:
    instruction_id: str
    intent_id: UUID
    source_location_id: LocationId
    network_asset_id: NetworkAssetId
    destination_address: str
    amount: Decimal
    fee_policy: FeePolicy
    idempotency_key: str
    memo: str | None = None

    def __post_init__(self) -> None:
        if not self.instruction_id.strip() or not self.idempotency_key.strip() or not self.destination_address.strip():
            raise ValueError("crypto instruction identity/address cannot be empty")
        if self.amount <= 0:
            raise ValueError("crypto transfer amount must be positive")


@dataclass(frozen=True, slots=True)
class BankTransferInstruction:
    instruction_id: str
    intent_id: UUID
    source_location_id: LocationId
    beneficiary_id: str
    account_reference: str
    rail_id: RailId
    asset_id: AssetId
    amount: Decimal
    fee_policy: FeePolicy
    idempotency_key: str

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.instruction_id, self.beneficiary_id, self.account_reference, self.idempotency_key)):
            raise ValueError("bank instruction identity fields cannot be empty")
        if self.amount <= 0:
            raise ValueError("bank transfer amount must be positive")


TransferInstruction: TypeAlias = InternalTransferInstruction | CryptoTransferInstruction | BankTransferInstruction


class TransferStatus(StrEnum):
    CREATED = "created"
    VALIDATED = "validated"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    SOURCE_DEBITED = "source_debited"
    IN_TRANSIT = "in_transit"
    BROADCAST = "broadcast"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    DESTINATION_CREDITED = "destination_credited"
    SETTLED = "settled"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    RETURNED = "returned"
    REVERSED = "reversed"
    MANUAL_REVIEW = "manual_review"


TERMINAL_STATUSES = frozenset({
    TransferStatus.COMPLETED, TransferStatus.REJECTED, TransferStatus.FAILED,
    TransferStatus.CANCELLED, TransferStatus.EXPIRED, TransferStatus.REVERSED,
})


@dataclass(frozen=True, slots=True)
class TransferOperation:
    transfer_id: str
    intent_id: UUID
    instruction_id: str
    source_location_id: LocationId
    destination_location_id: LocationId | None
    asset_id: AssetId
    requested_amount: Decimal
    status: TransferStatus
    created_at: datetime
    updated_at: datetime
    fee_policy: FeePolicy = FeePolicy.ADD_TO_AMOUNT
    debited_amount: Decimal | None = None
    credited_amount: Decimal | None = None
    fee_amount: Decimal | None = None
    fee_asset: AssetId | None = None
    provider_reference: str | None = None
    transaction_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.transfer_id.strip() or not self.instruction_id.strip():
            raise ValueError("operation identity cannot be empty")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("operation timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("operation update cannot precede creation")
        if self.requested_amount <= 0:
            raise ValueError("requested amount must be positive")
        if any(value is not None and value < 0 for value in (self.debited_amount, self.credited_amount, self.fee_amount)):
            raise ValueError("operation amounts cannot be negative")
        if self.fee_amount is not None and self.fee_amount > 0 and self.fee_asset is None:
            raise ValueError("positive fee requires fee asset")

    def evolve(self, status: TransferStatus, at: datetime, **changes) -> TransferOperation:
        return replace(self, status=status, updated_at=at, **changes)


@dataclass(frozen=True, slots=True)
class TransferOperationEvent:
    event_id: str
    transfer_id: str
    previous_status: TransferStatus | None
    status: TransferStatus
    occurred_at: datetime
    provider_event_id: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("operation event timestamp must be timezone-aware")
