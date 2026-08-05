from __future__ import annotations

from enum import StrEnum


class IntegrationCapability(StrEnum):
    MARKET_DATA = "market.data"
    MARKET_STREAM = "market.stream"
    REFERENCE_DATA = "reference.data"
    ACCOUNT_READ = "account.read"
    ACCOUNT_MARKET_PROFILE_READ = "account.market_profile.read"
    ACCOUNT_STREAM = "account.stream"
    ORDER_ENTRY = "order.entry"
    ORDER_CANCEL = "order.cancel"
    EXECUTION_STREAM = "execution.stream"
    EARN = "earn.operations"


__all__ = ["IntegrationCapability"]
