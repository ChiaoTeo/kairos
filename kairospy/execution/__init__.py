from __future__ import annotations

from .events import TradeExecution, TradeSide
from .fills import Fill, LegFill, Settlement
from .orders import (
    ExecutionCapabilities,
    ExecutionInstructions,
    MarginMode,
    Order,
    OrderLeg,
    OrderStatus,
    OrderType,
    PositionMode,
    SelfTradePrevention,
    TimeInForce,
    TriggerPriceSource,
)
from .ports import (
    ComboExecutionPort,
    ComboLegRequest,
    ComboOrderRequest,
    Environment,
    ExecutionPort,
    OrderAck,
    OrderRecoveryPort,
    OrderRequest,
    RecoveredExecution,
    VenueOrderRecovery,
    VenueOrderStatus,
)

__all__ = [
    "ExecutionCalibrationRelease",
    "ExecutionCapabilities",
    "ExecutionInstructions",
    "ExecutionMode",
    "ExecutionPolicy",
    "ExecutionPort",
    "ExecutionRouter",
    "Fill",
    "IntentExecutionTracker",
    "IntentExecutionView",
    "IntentScope",
    "IntentStatus",
    "LegFill",
    "MarginMode",
    "Order",
    "OrderAck",
    "OrderCommand",
    "OrderRecoveryPort",
    "OrderRequest",
    "OrderLeg",
    "OrderStatus",
    "OrderType",
    "OutboxRecord",
    "OutboxStatus",
    "PartialFillPolicy",
    "PositionMode",
    "SelfTradePrevention",
    "Settlement",
    "TimeInForce",
    "TradeExecution",
    "TradeSide",
    "TriggerPriceSource",
    "ComboExecutionPort",
    "ComboLegRequest",
    "ComboOrderRequest",
    "Environment",
    "RecoveredExecution",
    "VenueOrderRecovery",
    "VenueOrderStatus",
    "build_execution_calibration_release",
    "intent_scope",
    "load_execution_calibration_release",
]

_LAZY_EXPORTS = {
    "ExecutionCalibrationRelease": ("kairospy.execution.calibration", "ExecutionCalibrationRelease"),
    "build_execution_calibration_release": ("kairospy.execution.calibration", "build_execution_calibration_release"),
    "load_execution_calibration_release": ("kairospy.execution.calibration", "load_execution_calibration_release"),
    "ExecutionMode": ("kairospy.execution.policy", "ExecutionMode"),
    "ExecutionPolicy": ("kairospy.execution.policy", "ExecutionPolicy"),
    "PartialFillPolicy": ("kairospy.execution.policy", "PartialFillPolicy"),
    "ExecutionRouter": ("kairospy.execution.router", "ExecutionRouter"),
    "OrderCommand": ("kairospy.execution.command", "OrderCommand"),
    "OutboxRecord": ("kairospy.execution.command", "OutboxRecord"),
    "OutboxStatus": ("kairospy.execution.command", "OutboxStatus"),
    "IntentExecutionTracker": ("kairospy.execution.intent_status", "IntentExecutionTracker"),
    "IntentExecutionView": ("kairospy.execution.intent_status", "IntentExecutionView"),
    "IntentScope": ("kairospy.execution.intent_status", "IntentScope"),
    "IntentStatus": ("kairospy.execution.intent_status", "IntentStatus"),
    "intent_scope": ("kairospy.execution.intent_status", "intent_scope"),
}


def __getattr__(name: str):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _LAZY_EXPORTS[name]
    from importlib import import_module

    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
