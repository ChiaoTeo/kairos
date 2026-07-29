from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from kairospy.config import AccountConfig


@dataclass(frozen=True, slots=True)
class RuntimeAccount:
    account_id: str
    index: int
    venue: str
    cash: Decimal
    currency: str
    fee_rate: Decimal = Decimal("0")
    credential: str | None = None


class AccountRegistry:
    def __init__(self, accounts: Iterable[RuntimeAccount]) -> None:
        ordered = tuple(sorted(accounts, key=lambda account: account.index))
        self._accounts = {account.account_id: account for account in ordered}
        self._indexes = {account.index: account for account in ordered}
        if len(self._accounts) != len(ordered):
            raise ValueError("account ids must be unique")
        if len(self._indexes) != len(ordered):
            raise ValueError("account indexes must be unique")

    @classmethod
    def from_config(cls, accounts: Iterable[AccountConfig]) -> "AccountRegistry":
        return cls(
            RuntimeAccount(
                account.account_id,
                account.index,
                account.venue,
                account.cash,
                account.currency,
                fee_rate=account.fee_rate,
                credential=account.credential,
            )
            for account in accounts
        )

    @property
    def accounts(self) -> tuple[RuntimeAccount, ...]:
        return tuple(self._accounts.values())

    def require(self, account_id: str) -> RuntimeAccount:
        try:
            return self._accounts[account_id]
        except KeyError as error:
            raise ValueError(f"unknown account: {account_id}") from error

    def require_index(self, account_index: int) -> RuntimeAccount:
        try:
            return self._indexes[account_index]
        except KeyError as error:
            raise ValueError(f"unknown account index: {account_index}") from error

    def resolve(
        self,
        *,
        venue: str,
        account: int | str | None = None,
        account_id: str | None = None,
        account_index: int | None = None,
    ) -> RuntimeAccount:
        venue = venue.strip()
        if account is not None:
            if isinstance(account, bool):
                raise ValueError("account must be an account id or integer account index")
            if isinstance(account, int):
                account_index = account
            else:
                account_id = str(account)
        if account_index is not None:
            selected = self.require_index(account_index)
            if selected.venue != venue:
                raise ValueError(f"account index {account_index} is configured for venue {selected.venue!r}, not {venue!r}")
            return selected
        if account_id is not None:
            selected = self.require(account_id)
            if selected.venue != venue:
                raise ValueError(f"account {account_id!r} is configured for venue {selected.venue!r}, not {venue!r}")
            return selected
        matches = tuple(account for account in self._accounts.values() if account.venue == venue)
        if not matches:
            raise ValueError(f"no configured account for venue: {venue}")
        if len(matches) > 1:
            indexes = ", ".join(str(account.index) for account in matches)
            raise ValueError(f"multiple accounts configured for venue {venue!r}; specify account index: {indexes}")
        return matches[0]


__all__ = ["AccountRegistry", "RuntimeAccount"]
