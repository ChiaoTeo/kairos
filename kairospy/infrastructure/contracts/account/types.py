"""Canonical Account owner-contract types exported by the native binding."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kairospy.infrastructure.contracts._native import load_owner_contract


def _native() -> Any:
    return load_owner_contract("Account")


if TYPE_CHECKING:
    from kairospy._native_account_contract import (
        AccountClient,
        AccountCommandStatus,
        AccountControlClient,
        AccountControlRejectedError,
        AccountControlUnavailableError,
        AccountCurrentView,
        AccountHealth,
        AccountInvalidCurrentViewError,
        AccountInvalidEventError,
        AccountInvalidInputError,
        AccountCurrentViewUnavailableError,
        AccountRefreshResponse,
        AccountSegmentsRequest,
        AdvanceAccountTimeRequest,
        AdvanceAccountTimeResponse,
        MarkToMarketRequest,
        SimulatedCapitalMutation,
        SimulatedCapitalMutationQuery,
        SimulatedCapitalMutationStatusResponse,
        SimulatedSettlement,
    )
else:
    _module = _native()
    AccountClient = _module.AccountClient
    AccountControlClient = _module.AccountControlClient
    AccountCurrentView = _module.AccountCurrentView
    AccountSegmentsRequest = _module.AccountSegmentsRequest
    MarkToMarketRequest = _module.MarkToMarketRequest
    AdvanceAccountTimeRequest = _module.AdvanceAccountTimeRequest
    AccountHealth = _module.AccountHealth
    AccountInvalidCurrentViewError = _module.AccountInvalidCurrentViewError
    AccountInvalidEventError = _module.AccountInvalidEventError
    AccountInvalidInputError = _module.AccountInvalidInputError
    AccountCurrentViewUnavailableError = _module.AccountCurrentViewUnavailableError
    AccountCommandStatus = _module.AccountCommandStatus
    AccountRefreshResponse = _module.AccountRefreshResponse
    AdvanceAccountTimeResponse = _module.AdvanceAccountTimeResponse
    SimulatedSettlement = _module.SimulatedSettlement
    SimulatedCapitalMutation = _module.SimulatedCapitalMutation
    SimulatedCapitalMutationQuery = _module.SimulatedCapitalMutationQuery
    SimulatedCapitalMutationStatusResponse = _module.SimulatedCapitalMutationStatusResponse
    AccountControlUnavailableError = _module.AccountControlUnavailableError
    AccountControlRejectedError = _module.AccountControlRejectedError
ACCOUNT_EVENT_STREAM_ID = _native().ACCOUNT_EVENT_STREAM_ID
DEFAULT_AERON_CHANNEL = _native().DEFAULT_AERON_CHANNEL


__all__ = [
    "AccountCommandStatus",
    "AccountClient",
    "AccountControlClient",
    "AccountControlRejectedError",
    "AccountControlUnavailableError",
    "AccountCurrentView",
    "AccountHealth",
    "AccountInvalidCurrentViewError",
    "AccountInvalidEventError",
    "AccountInvalidInputError",
    "AccountCurrentViewUnavailableError",
    "AccountRefreshResponse",
    "AccountSegmentsRequest",
    "AdvanceAccountTimeRequest",
    "AdvanceAccountTimeResponse",
    "MarkToMarketRequest",
    "SimulatedCapitalMutation",
    "SimulatedCapitalMutationQuery",
    "SimulatedCapitalMutationStatusResponse",
    "SimulatedSettlement",
    "ACCOUNT_EVENT_STREAM_ID",
    "DEFAULT_AERON_CHANNEL",
]
