"""Stable, typed public API for user-authored strategies."""

from kairospy.application.account import (
    COIN_M_FUTURES,
    CROSS_MARGIN,
    EQUITY,
    FUNDING,
    ISOLATED_MARGIN,
    OPTIONS,
    SPOT,
    USD_M_FUTURES,
    AccountApplication,
    AccountEvent,
    AccountLookupError,
    AccountNotEnabledError,
    AccountSegmentNotFoundError,
    AccountSegmentSnapshot,
    AccountSnapshot,
    AccountsSnapshot,
    AccountStatusChange,
    AccountStatusChangedEvent,
    Balance,
    BalanceNotFoundError,
    BalanceChangedEvent,
    DataFreshness,
    EquityChange,
    EquityChangedEvent,
    Position,
    PositionNotFoundError,
    PositionChangedEvent,
)
from kairospy.application.execution import (
    AccountExecution,
    ArbitrageLegRequest,
    BulkOrderCommandReceipt,
    DeliveryCertainty,
    ExecutionApplication,
    ExecutionAccountNotEnabledError,
    ExecutionEvent,
    ExecutionIntent,
    ExecutionLookupError,
    Fill,
    FillEvent,
    IntentId,
    IntentReceipt,
    IntentNotFoundError,
    IntentStatus,
    IntentUpdateEvent,
    HedgePolicy,
    LimitOrderRequest,
    MarketOrderRequest,
    MakerExecutionPolicy,
    OptionSpreadLegRequest,
    OptionSpreadRequest,
    Order,
    OrderNotFoundError,
    OrderCommandReceipt,
    OrderId,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderUpdateEvent,
    PairArbitrageRequest,
    PortfolioRebalanceRequest,
    PortfolioRebalanceTarget,
    QuoteProvisioningRequest,
    QuoteRefreshRequest,
    ReplaceOrderRequest,
    SubmissionStatus,
    SplitOrderPolicy,
    TargetPositionRequest,
    TimeInForce,
)
from kairospy.application.market import (
    AggressorSide,
    Bar,
    BarEvent,
    GreeksEvent,
    MarketApplication,
    MarketEvent,
    ObservationScope,
    ObservationScopeKind,
    OptionGreeks,
    Quote,
    QuoteEvent,
    Subscription,
    Trade,
    TradeEvent,
)
from kairospy.application.notification import (
    NotificationApplication,
    NotificationReceipt,
    NotificationRequest,
    NotificationSeverity,
)
from kairospy.application.reference import (
    AmbiguousReferenceError,
    Asset,
    Entity,
    Instrument,
    InstrumentRef,
    Listing,
    Market,
    MarketStatus,
    ReferenceApplication,
    ReferenceNotFoundError,
    ReferenceStatus,
    TradingRules,
)
from kairospy.application.risk import (
    ReservationChange,
    ReservationChangedEvent,
    RiskApplication,
    RiskEvent,
    RiskStatus,
    RiskViolation,
)
from kairospy.domain_types import (
    AccountId,
    DataEvent,
    EventMetadata,
    ExchangeId,
    FillId,
    InstrumentId,
    ListingId,
    MarketId,
    SegmentKey,
)

from .clock import DeterministicTimerQueue, StrategyClock, TimerEvent
from .commands import CommandEnvelope, CommandSource, StrategyCommand
from .errors import *
from .events import (
    ClockAdvance,
    ClockAdvancedEvent,
    ClockEvent,
    SystemEvent,
    SystemNotice,
    TimerFiredEvent,
)
from .identity import StrategyIdentity
from .logging import StrategyLogger, StrategyOutput
from .protocol import (
    Strategy,
    StrategyContext,
    StrategyProtocol,
)
from .results import CommandResult
from .selection import (
    OptionSelectionAudit,
    OptionSelectionCandidate,
    OptionSpreadSelectionApplication,
    OptionSpreadSelectionRequest,
    OptionSpreadSelectionResult,
)
from .state import StrategyState
from .validation import StrategyContractError, validate_strategy

# Decision lifecycle values live in the Strategy application because that
# application owns their persistence and progress.  Resolve them lazily here
# so user strategies have a stable public import without creating an import
# cycle while the Strategy runtime itself is being assembled.
_DECISION_EXPORTS = frozenset(
    {
        "DecisionEffectEvaluation",
        "DecisionHorizon",
        "DecisionLifecycle",
        "EffectEvidence",
        "EffectOutcome",
        "StrategyDecision",
    }
)


def __getattr__(name: str):
    if name not in _DECISION_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from kairospy.application.strategy.application import decisions

    return getattr(decisions, name)


CommandHandle = CommandResult
