"""Application-layer composition, configuration, and runtime ports."""

from .clock import Clock, FixedClock, SystemClock
from .config import ApplicationConfig, RuntimePaths
from .recovery import RuntimeRecovery, RuntimeRecoveryResult, RuntimeRecoveryService
from .runtime import (
    FunctionProbe, PersistenceProbe, ProbeResult, ReadinessProbe, RuntimeStatus, TradingApplication,
)
from .supervisor import (
    RecoveryBackgroundService, RuntimeBackgroundService, RuntimeSupervisor, SupervisorCycle,
    write_soak_artifact,
)
from .task_supervisor import (
    AsyncTaskSupervisor, ManagedTaskSnapshot, ManagedTaskSpec, ManagedTaskStatus, TaskCriticality, TaskFault,
)
from .async_runtime import AsyncTradingRuntime
from .modes import (
    ComponentBinding,ExecutableRunComposition,RunModeComposition, backtest_composition, historical_simulation_composition,
    live_composition, live_paper_composition, research_composition,
)
from .strategy_run_loop import (
    CanonicalBarMarketProjection, GovernedStrategyRunLoop, StrategyRunResult,
)
from .historical_simulation import (
    HistoricalSimulationResult, build_simulated_spot_catalog, run_sma_historical_simulation,
)
from .run_artifact import RunArtifact, RunArtifactRepository
from .attribution import (
    ExecutionAttribution,PortfolioAttribution,RunAttribution,SignalAttribution,build_run_attribution,
)

__all__ = [
    "ApplicationConfig", "Clock", "FixedClock", "FunctionProbe",
    "PersistenceProbe", "ProbeResult",
    "ReadinessProbe", "RuntimePaths", "RuntimeRecovery", "RuntimeRecoveryResult", "RuntimeRecoveryService",
    "RuntimeStatus", "SystemClock", "TradingApplication",
    "RecoveryBackgroundService", "RuntimeBackgroundService", "RuntimeSupervisor", "SupervisorCycle",
    "write_soak_artifact",
    "AsyncTaskSupervisor", "ManagedTaskSnapshot", "ManagedTaskSpec", "ManagedTaskStatus",
    "TaskCriticality", "TaskFault",
    "AsyncTradingRuntime",
    "RunModeComposition", "backtest_composition", "historical_simulation_composition",
    "live_composition", "live_paper_composition", "research_composition",
    "CanonicalBarMarketProjection", "GovernedStrategyRunLoop", "StrategyRunResult",
    "HistoricalSimulationResult", "run_sma_historical_simulation",
    "build_simulated_spot_catalog",
    "RunArtifact", "RunArtifactRepository",
    "ExecutionAttribution","PortfolioAttribution","RunAttribution","SignalAttribution","build_run_attribution",
    "ComponentBinding","ExecutableRunComposition",
]
from .immediate_backtest import (
    ImmediateBacktestPortfolio, ImmediateBacktestTrade, ImmediateIntentBacktestResult,
    run_immediate_target_backtest, run_target_backtest,
)

__all__ += [
    "ImmediateBacktestPortfolio", "ImmediateBacktestTrade", "ImmediateIntentBacktestResult",
    "run_immediate_target_backtest", "run_target_backtest",
]
