"""Canonical Execution owner-contract types from the native companion."""

from typing import TYPE_CHECKING, Any

from kairospy.infrastructure.contracts._native import load_owner_contract

if TYPE_CHECKING:
    from kairospy._native_execution_contract import (
        AdvanceExecutionTimeRequest,
        AdvanceExecutionTimeResponse,
        CancelOrderRequest,
        ExecutionAlgorithmPolicyRequest,
        ExecutionBenchmarkRequest,
        ExecutionBacktestFill,
        ExecutionBacktestEquityPoint,
        ExecutionBacktestInputFill,
        ExecutionBacktestMarketRequest,
        ExecutionBacktestMarketResponse,
        ExecutionBacktestMetrics,
        ExecutionBacktestOrder,
        ExecutionBacktestOrderRequest,
        ExecutionBacktestRequest,
        ExecutionBacktestRunResponse,
        ExecutionBacktestSimulationConfig,
        ExecutionCommandStatus,
        ExecutionControlClient,
        ExecutionControlRejectedError,
        ExecutionControlUnavailableError,
        ExecutionCurrentView,
        ExecutionHealth,
        ExecutionInvalidCurrentViewError,
        ExecutionInvalidEventError,
        ExecutionInvalidInputError,
        ExecutionCurrentViewUnavailableError,
        ExecutionIntentRequest,
        ExecutionOrderAuditEvent,
        ExecutionOrderAuditQuery,
        ExecutionOrderAuditResponse,
        ExecutionOrderOptionsRequest,
        ExecutionReconcileResponse,
        ExecutionRouteCandidate,
        ExecutionRoutesQuery,
        ExecutionRoutesResponse,
        IntentAdmissionEvidenceRequest,
        IntentLegRequest,
        MakerExecutionPolicyRequest,
        ReconcileExecutionRequest,
        ReplaceOrderRequest,
        SplitOrderPolicyRequest,
        SubmitIntentRequest,
    )


def _native() -> Any:
    return load_owner_contract("Execution")


_NAMES = (
    "AdvanceExecutionTimeRequest",
    "AdvanceExecutionTimeResponse",
    "CancelOrderRequest",
    "ExecutionAlgorithmPolicyRequest",
    "ExecutionBenchmarkRequest",
    "ExecutionBacktestFill",
    "ExecutionBacktestEquityPoint",
    "ExecutionBacktestInputFill",
    "ExecutionBacktestMarketRequest",
    "ExecutionBacktestMarketResponse",
    "ExecutionBacktestMetrics",
    "ExecutionBacktestOrder",
    "ExecutionBacktestOrderRequest",
    "ExecutionBacktestRequest",
    "ExecutionBacktestRunResponse",
    "ExecutionBacktestSimulationConfig",
    "ExecutionCommandStatus",
    "ExecutionControlClient",
    "ExecutionControlRejectedError",
    "ExecutionControlUnavailableError",
    "ExecutionCurrentView",
    "ExecutionClient",
    "ExecutionHealth",
    "ExecutionInvalidCurrentViewError",
    "ExecutionInvalidEventError",
    "ExecutionInvalidInputError",
    "ExecutionCurrentViewUnavailableError",
    "ExecutionIntentRequest",
    "ExecutionOrderAuditEvent",
    "ExecutionOrderAuditQuery",
    "ExecutionOrderAuditResponse",
    "ExecutionOrderOptionsRequest",
    "ExecutionReconcileResponse",
    "ExecutionRouteCandidate",
    "ExecutionRoutesQuery",
    "ExecutionRoutesResponse",
    "IntentAdmissionEvidenceRequest",
    "IntentLegRequest",
    "MakerExecutionPolicyRequest",
    "ReconcileExecutionRequest",
    "ReplaceOrderRequest",
    "SplitOrderPolicyRequest",
    "SubmitIntentRequest",
)
if not TYPE_CHECKING:
    _module = _native()
    globals().update({name: getattr(_module, name) for name in _NAMES})

__all__ = list(_NAMES)
