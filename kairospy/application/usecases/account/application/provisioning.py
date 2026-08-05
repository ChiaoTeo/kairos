"""Public account capability provisioning use case."""

from __future__ import annotations

from decimal import Decimal

from kairospy.application.usecases.account.services.provisioning import AccountProvisioningService as _AccountProvisioningService
from kairospy.domain.account import AccountSegment, AccountCapability, AccountFeeSchedule


class AccountProvisioningService:
    def __init__(self) -> None:
        self._service = _AccountProvisioningService()

    def capability(self, segment: AccountSegment, *, trade_enabled: bool = True, has_trade_credential: bool = True) -> AccountCapability:
        return self._service.capability(segment, trade_enabled=trade_enabled, has_trade_credential=has_trade_credential)

    def fee_schedule(self, segment: AccountSegment, *, fee_rate: Decimal) -> AccountFeeSchedule:
        return self._service.fee_schedule(segment, fee_rate=fee_rate)


__all__ = ["AccountProvisioningService"]
