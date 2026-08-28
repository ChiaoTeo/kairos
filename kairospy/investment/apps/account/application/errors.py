from __future__ import annotations

from kairospy.primitives.account import AccountId, SegmentKey
from kairospy.primitives.reference import AssetId, InstrumentId


class AccountLookupError(LookupError):
    """Base class for typed Strategy-facing Account lookup failures."""


class AccountNotEnabledError(AccountLookupError):
    def __init__(self, account_id: AccountId) -> None:
        self.account_id = account_id
        super().__init__(f"account {account_id!s} is not enabled for this launch")


class AccountSegmentNotFoundError(AccountLookupError):
    def __init__(self, account_id: AccountId, segment_key: SegmentKey) -> None:
        self.account_id = account_id
        self.segment_key = segment_key
        super().__init__(
            f"account {account_id!s} has no segment {segment_key!s} in this snapshot"
        )


class BalanceNotFoundError(AccountLookupError):
    def __init__(
        self, account_id: AccountId, segment_key: SegmentKey, asset: AssetId
    ) -> None:
        self.account_id = account_id
        self.segment_key = segment_key
        self.asset = asset
        super().__init__(
            f"account {account_id!s} segment {segment_key!s} has no balance for {asset}"
        )


class PositionNotFoundError(AccountLookupError):
    def __init__(
        self,
        account_id: AccountId,
        segment_key: SegmentKey,
        instrument_id: InstrumentId,
    ) -> None:
        self.account_id = account_id
        self.segment_key = segment_key
        self.instrument_id = instrument_id
        super().__init__(
            f"account {account_id!s} segment {segment_key!s} has no position for "
            f"{instrument_id!s}"
        )
