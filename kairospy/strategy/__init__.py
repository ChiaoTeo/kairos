"""Stable public contract for user-authored strategies.

Strategy code may depend on this package. Runtime services, transports, and
component implementations are intentionally outside this public surface.
"""

from .application import (
    CommandResult,
    EventEnvelope,
    StrategyBase,
    StrategyContextProtocol,
    StrategyContractError,
    StrategyProtocol,
    SubscriptionRequest,
    TargetPositionRequest,
    validate_strategy,
)
from .logging import StrategyLogger, StrategyOutput
from .commands import CommandEnvelope, CommandSource

__all__ = [
    "CommandResult",
    "CommandHandle",
    "CommandEnvelope",
    "CommandSource",
    "EventEnvelope",
    "StrategyBase",
    "StrategyContextProtocol",
    "StrategyLogger",
    "StrategyOutput",
    "StrategyContractError",
    "StrategyProtocol",
    "SubscriptionRequest",
    "MarketSubscriptionRequest",
    "TargetPositionRequest",
    "validate_strategy",
]

# Names used by the application adapter are aliases of the stable SDK types.
CommandHandle = CommandResult
MarketSubscriptionRequest = SubscriptionRequest
