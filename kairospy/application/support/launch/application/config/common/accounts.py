from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable
from typing import Mapping
from typing import Protocol
from typing import TypeVar

from kairospy.application.support.launch.application.config.launch import AccountConfig

from .config import account_selector, optional_int, optional_text

ConfigErrorT = TypeVar("ConfigErrorT", bound=Exception)


class AccountResolver(Protocol):
    def __call__(self, account_ref: str) -> "ConfiguredAccount":
        ...


@dataclass(frozen=True, slots=True)
class ConfiguredCredential:
    name: str
    ref: str | None = None
    kind: str | None = None
    role: str = "readonly"

    def can_read(self) -> bool:
        return self.role in {"readonly", "trade"}

    def can_trade(self) -> bool:
        return self.role == "trade"


@dataclass(frozen=True, slots=True)
class ConfiguredAccount:
    account_id: str
    index: int
    venue: str
    initial_balances: tuple[tuple[str, Decimal], ...]
    fee_rate: Decimal = Decimal("0")
    credential: str | None = None
    credential_role: str = "trade"
    environment: str = ""
    credentials: tuple[ConfiguredCredential, ...] = ()

    def read_credential_ref(self) -> str | None:
        for credential in self.credentials:
            if credential.role == "readonly" and credential.ref:
                return credential.ref
        for credential in self.credentials:
            if credential.can_read() and credential.ref:
                return credential.ref
        if self.credential:
            return self.credential
        return None

    def trade_credential_ref(self) -> str | None:
        for credential in self.credentials:
            if credential.can_trade() and credential.ref:
                return credential.ref
        if self.credential and self.credential_role == "trade":
            return self.credential
        return None

    def has_trade_credential(self) -> bool:
        if self.credentials:
            return any(credential.can_trade() for credential in self.credentials)
        return self.credential is None or self.credential_role == "trade"


class AccountConfigRegistry:
    def __init__(self, accounts: Iterable[ConfiguredAccount]) -> None:
        ordered = tuple(sorted(accounts, key=lambda account: account.index))
        self._accounts = {account.account_id: account for account in ordered}
        self._indexes = {account.index: account for account in ordered}
        if len(self._accounts) != len(ordered):
            raise ValueError("account ids must be unique")
        if len(self._indexes) != len(ordered):
            raise ValueError("account indexes must be unique")

    @classmethod
    def from_config(cls, accounts: Iterable[AccountConfig]) -> "AccountConfigRegistry":
        return cls(
            ConfiguredAccount(
                account.account_id,
                account.index,
                account.venue,
                account.initial_balances,
                environment="",
                fee_rate=account.fee_rate,
                credential=account.credential,
            )
            for account in accounts
        )

    @property
    def accounts(self) -> tuple[ConfiguredAccount, ...]:
        return tuple(self._accounts.values())

    def require(self, account_id: str) -> ConfiguredAccount:
        try:
            return self._accounts[account_id]
        except KeyError as error:
            raise ValueError(f"unknown account: {account_id}") from error

    def require_index(self, account_index: int) -> ConfiguredAccount:
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
    ) -> ConfiguredAccount:
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


def configured_account(
    accounts: object,
    *,
    venue: str,
    mode_config: Mapping[str, object],
    mode_label: str,
    error_type: type[ConfigErrorT],
    require_accounts_table: bool = True,
) -> ConfiguredAccount:
    registry = AccountConfigRegistry.from_config(accounts)  # type: ignore[arg-type]
    if require_accounts_table and not registry.accounts:
        raise error_type(f"[accounts] table is required for {mode_label} launches")
    try:
        return registry.resolve(
            venue=venue,
            account=account_selector(mode_config.get("account"), f"{mode_label}.account", error_type),
            account_id=optional_text(mode_config.get("account_id"), f"{mode_label}.account_id", error_type),
            account_index=optional_int(mode_config.get("account_index"), f"{mode_label}.account_index", error_type),
        )
    except ValueError as error:
        raise error_type(str(error)) from error


def configured_account_ref(
    account_ref: str | None,
    *,
    account_resolver: AccountResolver | None,
    venue: str | None,
    mode_label: str,
    error_type: type[ConfigErrorT],
) -> ConfiguredAccount:
    if account_ref is None:
        raise error_type(f"account.ref is required for {mode_label} launches")
    if account_resolver is None:
        raise error_type(f"account resolver is required for {mode_label} launches")
    try:
        account = account_resolver(account_ref)
    except Exception as error:
        raise error_type(str(error)) from error
    if venue is not None and account.venue != venue:
        raise error_type(f"account {account.account_id!r} is configured for venue {account.venue!r}, not {venue!r}")
    return account


__all__ = ["AccountConfigRegistry", "AccountResolver", "ConfiguredAccount", "ConfiguredCredential", "configured_account", "configured_account_ref"]
