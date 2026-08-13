from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from kairospy.application.reference import InstrumentRef
from kairospy.domain_types import AccountId, InstrumentId

from .models import AccountSnapshot, Balance, Position

if TYPE_CHECKING:
    from kairospy.infrastructure.contracts.account import AccountMmapProjection


class AccountApplication:
    """Concrete read-only Account projections scoped to one strategy launch."""

    def __init__(
        self,
        projections: Mapping[AccountId, AccountMmapProjection],
    ) -> None:
        self._projections = dict(projections)

    @property
    def account_ids(self) -> tuple[AccountId, ...]:
        return tuple(self._projections)

    def snapshot(self, account: AccountId | str) -> AccountSnapshot:
        account_id = _account_id(account)
        try:
            projection = self._projections[account_id]
        except KeyError as error:
            raise ValueError(
                f"account {account_id!s} is not enabled for this launch"
            ) from error
        return projection.snapshot(account_id)

    def balance(self, asset: str, *, account: AccountId | str) -> Balance | None:
        return next(
            (
                value
                for value in self.snapshot(account).balances
                if value.asset == asset
            ),
            None,
        )

    def position(
        self,
        instrument: InstrumentRef | InstrumentId,
        *,
        account: AccountId | str,
    ) -> Position | None:
        instrument_id = (
            instrument.id if isinstance(instrument, InstrumentRef) else instrument
        )
        return next(
            (
                value
                for value in self.snapshot(account).positions
                if value.instrument.id == instrument_id
            ),
            None,
        )


def _account_id(value: AccountId | str) -> AccountId:
    return value if isinstance(value, AccountId) else AccountId(value)
