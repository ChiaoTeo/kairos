from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .institutions import InstitutionId


class AccountType(StrEnum):
    SECURITIES_CASH = "securities_cash"
    SECURITIES_MARGIN = "securities_margin"
    CRYPTO_SPOT = "crypto_spot"
    CROSS_MARGIN = "cross_margin"
    ISOLATED_MARGIN = "isolated_margin"
    DERIVATIVES = "derivatives"
    SUB_ACCOUNT = "sub_account"


@dataclass(frozen=True, slots=True, order=True)
class AccountRef:
    institution_id: InstitutionId
    account_id: str
    account_type: AccountType

    def __post_init__(self) -> None:
        if not isinstance(self.institution_id, InstitutionId):
            raise TypeError("account owner must be an InstitutionId")
        if not self.account_id.strip():
            raise ValueError("account id cannot be empty")

    @property
    def value(self) -> str:
        return f"{self.institution_id}:{self.account_type}:{self.account_id}"
