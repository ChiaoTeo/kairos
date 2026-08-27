"""Stable, typed public API for user-authored strategies."""

from kairospy.investment.apps.account.application import (
    COIN_M_FUTURES,
    CROSS_MARGIN,
    EQUITY,
    FUNDING,
    ISOLATED_MARGIN,
    OPTIONS,
    SPOT,
    USD_M_FUTURES,
    AccountApplication,
    AccountLookupError,
    AccountNotEnabledError,
    AccountSegmentNotFoundError,
    AccountSegmentSnapshot,
    AccountSnapshot,
    AccountsSnapshot,
    AccountStatusChange,
    Balance,
    BalanceNotFoundError,
    DataFreshness,
    EquityChange,
    Position,
    PositionNotFoundError,
)
from .account import AccountEvent, AccountEventChange, AccountEventMetadata
from kairospy.strategy.apps.agent.application import (
    AgentApplication,
    AgentContextDocument,
    AgentContextReceipt,
    AgentContextStatus,
    AgentDecisionNotice,
    AgentEvent,
    AgentEventStatus,
    AgentMode,
    AgentModeReceipt,
    AgentModeStatus,
)
from kairospy.investment.apps.capital.application import (
    CapitalApplication,
    CapitalAvailability,
    CapitalReadiness,
    FundingLocation,
    FundingForecastObservation,
    FundingForecastSource,
    FundingObjective,
    FundingObjectiveReceipt,
    FundingObjectiveStatus,
    FundingPriority,
)
from kairospy.investment.apps.execution.application import (
    AccountExecution,
    ArbitrageLegRequest,
    BulkOrderCommandReceipt,
    DeliveryCertainty,
    ExecutionApplication,
    ExecutionAccountNotEnabledError,
    ExecutionAlgorithmPolicy,
    ExecutionBenchmark,
    ExecutionIntent,
    ExecutionLookupError,
    Fill,
    IntentId,
    IntentReceipt,
    IntentNotFoundError,
    IntentStatus,
    HedgePolicy,
    ImmediateAlgorithm,
    LimitOrderRequest,
    MarketOrderRequest,
    MakerExecutionPolicy,
    MakerTakerHedgeAlgorithm,
    PassiveLimitAlgorithm,
    OptionSpreadLegRequest,
    OptionSpreadRequest,
    Order,
    OrderNotFoundError,
    OrderCommandReceipt,
    OrderId,
    OrderRequest,
    OrderSide,
    OrderStatus,
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
    TwapAlgorithm,
)
from .execution import ExecutionEvent, ExecutionEventMetadata
from kairospy.investment.apps.market.application import (
    ExpiryRange,
    MarketApplication,
    MarketData,
    ObservationRequirement,
    OptionFilter,
    OptionRight,
    Options,
    Provider,
    ProviderPreference,
    StrikeRange,
    Subscription,
    SubscriptionGroup,
    Timeframe,
)
from kairospy.strategy.apps.notification.application import (
    NotificationApplication,
    NotificationReceipt,
    NotificationRequest,
    NotificationSeverity,
)
from kairospy.investment.apps.portfolio.application import (
    AccountWatermark,
    PortfolioApplication,
    PortfolioCash,
    PortfolioEquity,
    PortfolioFreshness,
    PortfolioHistoryPoint,
    PortfolioHolding,
    PortfolioSnapshot,
    SegmentWatermark,
    ValuationWatermark,
)
from kairospy.investment.apps.risk.application import (
    RiskApplication,
    RiskStatus,
    RiskViolation,
)
from kairospy.investment.apps.reference.application import ReferenceApplication
from kairospy.investment.application.eventing import DataEvent, EventMetadata
from kairospy.primitives.account import AccountId, SegmentKey
from kairospy.primitives.execution import FillId
from kairospy.primitives.reference import (
    ExchangeId,
    InstrumentId,
    ListingId,
    MarketId,
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
from .market import (
    Bar,
    BarEvent,
    GreeksEvent,
    MarketEvent,
    ObservationScope,
    OptionGreeks,
    Quote,
    QuoteEvent,
    Trade,
    TradeEvent,
)
from .protocol import (
    Strategy,
    StrategyContext,
    StrategyProtocol,
)
from .results import CommandResult
from .risk import RiskEvent, RiskEventMetadata
from .reference import (
    Asset,
    Exchange,
    Instrument,
    InstrumentRef,
    Listing,
    Market,
    TradingRules,
)
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
    from kairospy.strategy.apps.decisions import application as decisions

    return getattr(decisions, name)


CommandHandle = CommandResult

__all__ = sorted(
    {name for name in globals() if not name.startswith("_")} | _DECISION_EXPORTS
)
