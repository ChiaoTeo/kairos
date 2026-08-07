"""Public application-facing contract for strategy authors.

The top-level :mod:`kairospy.strategy` package re-exports this stable surface
for concise strategy imports. Runtime implementations live elsewhere.
"""

from ..events import EventEnvelope
from ..protocol import StrategyBase, StrategyContextProtocol, StrategyProtocol
from ..requests import SubscriptionRequest, TargetPositionRequest
from ..results import CommandResult
from ..validation import StrategyContractError, validate_strategy

__all__ = [
    "CommandResult",
    "EventEnvelope",
    "StrategyBase",
    "StrategyContextProtocol",
    "StrategyContractError",
    "StrategyProtocol",
    "SubscriptionRequest",
    "TargetPositionRequest",
    "validate_strategy",
]
