"""Typed account capability adapters owned by the account application."""

from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.usecases.account.protocol import (
    AccountAssetReader,
    AccountPositionReader,
    AccountReadPort,
    AccountReadRequest,
    AccountTransferRequest,
    AccountTransferResult,
    AccountTransferService,
)
from kairospy.domain.account import AccountBalance, PositionSnapshot


@dataclass(frozen=True, slots=True)
class SnapshotAccountCapabilities(AccountAssetReader, AccountPositionReader):
    """Split one normalized snapshot reader into minimal consumer capabilities."""

    reader: AccountReadPort

    def read_assets(self, request: AccountReadRequest) -> tuple[AccountBalance, ...]:
        return self.reader.read_account(request).balances

    def read_positions(self, request: AccountReadRequest) -> tuple[PositionSnapshot, ...]:
        return self.reader.read_account(request).positions


class UnavailableAccountTransferService(AccountTransferService):
    """Explicit result for venues without a configured transfer adapter."""

    def transfer(self, request: AccountTransferRequest) -> AccountTransferResult:
        return AccountTransferResult(
            request,
            accepted=False,
            reason="account transfer adapter is not configured for this venue",
        )


__all__ = ["SnapshotAccountCapabilities", "UnavailableAccountTransferService"]
