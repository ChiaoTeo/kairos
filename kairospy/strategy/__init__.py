"""Stable, typed public API for user-authored strategies."""

from kairospy.application.account import (
    AccountApplication,
    AccountEvent,
    AccountSnapshot,
    AccountSnapshotEvent,
    Balance,
    BalanceEvent,
    DataFreshness,
    Position,
    PositionEvent,
)
from kairospy.application.execution import (
    ArbitrageLegRequest,
    BulkOrderCommandReceipt,
    DeliveryCertainty,
    ExecutionApplication,
    ExecutionEvent,
    ExecutionIntent,
    Fill,
    FillEvent,
    IntentId,
    IntentReceipt,
    IntentStatus,
    IntentUpdateEvent,
    HedgePolicy,
    LimitOrderRequest,
    MarketOrderRequest,
    MakerExecutionPolicy,
    OptionSpreadLegRequest,
    OptionSpreadRequest,
    Order,
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
    MarketApplication,
    MarketEvent,
    Quote,
    QuoteEvent,
    Subscription,
    Trade,
    TradeEvent,
)
from kairospy.application.reference import (
    AmbiguousReferenceError,
    InstrumentRef,
    Market,
    MarketStatus,
    ReferenceApplication,
    ReferenceNotFoundError,
    TradingRules,
)
from kairospy.application.risk import (
    RiskApplication,
    RiskEvent,
    RiskStatus,
    RiskStatusEvent,
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

CommandHandle = CommandResult
