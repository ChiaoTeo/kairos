"""Canonical Account owner-contract types exported by the native binding."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kairospy.contracts._native import load_owner_contract


def _native() -> Any:
    return load_owner_contract("Account")


if TYPE_CHECKING:
    from kairospy._native_account_contract import (
        AccountClient,
        AccountCommandStatus,
        AccountBalanceCurrent,
        AccountCollateralCurrent,
        AccountControlClient,
        AccountCurrentSnapshot,
        AccountControlRejectedError,
        AccountControlUnavailableError,
        AccountCurrentView,
        AccountHealth,
        AccountEarnHoldingCurrent,
        AccountInvalidCurrentViewError,
        AccountInvalidEventError,
        AccountInvalidInputError,
        AccountCurrentViewUnavailableError,
        AccountRefreshResponse,
        AccountObservedOrderCurrent,
        AccountPositionCurrent,
        AccountSegmentCurrent,
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
    AccountBalanceCurrent = _module.AccountBalanceCurrent
    AccountCollateralCurrent = _module.AccountCollateralCurrent
    AccountControlClient = _module.AccountControlClient
    AccountCurrentView = _module.AccountCurrentView
    AccountCurrentSnapshot = _module.AccountCurrentSnapshot
    AccountEarnHoldingCurrent = _module.AccountEarnHoldingCurrent
    AccountObservedOrderCurrent = _module.AccountObservedOrderCurrent
    AccountPositionCurrent = _module.AccountPositionCurrent
    AccountSegmentCurrent = _module.AccountSegmentCurrent
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
    "AccountBalanceCurrent",
    "AccountCollateralCurrent",
    "AccountClient",
    "AccountControlClient",
    "AccountControlRejectedError",
    "AccountControlUnavailableError",
    "AccountCurrentView",
    "AccountCurrentSnapshot",
    "AccountEarnHoldingCurrent",
    "AccountHealth",
    "AccountInvalidCurrentViewError",
    "AccountInvalidEventError",
    "AccountInvalidInputError",
    "AccountCurrentViewUnavailableError",
    "AccountRefreshResponse",
    "AccountObservedOrderCurrent",
    "AccountPositionCurrent",
    "AccountSegmentCurrent",
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
