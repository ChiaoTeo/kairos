"""Application-layer composition, configuration, and runtime ports."""

from .clock import Clock, FixedClock, SystemClock
from .config import ApplicationConfig, RuntimePaths
from .recovery import RuntimeRecovery, RuntimeRecoveryResult, RuntimeRecoveryService
from .runtime import (
    FunctionProbe, PersistenceProbe, ProbeResult, ReadinessProbe, RuntimeStatus, KairosApplication,
)
from .supervisor import (
    RecoveryBackgroundService, RuntimeBackgroundService, RuntimeSupervisor, SupervisorCycle,
    write_soak_artifact,
)
from .service_supervisor import (
    AsyncServiceSupervisor, ManagedServiceSnapshot, ManagedServiceSpec, ManagedServiceStatus,
    ServiceCriticality, ServiceFault,
)
from .async_runtime import AsyncKairosRuntime
from .modes import (
    ComponentBinding,ExecutableRunComposition,RunModeComposition, RuntimeExecutionPlan,
    RuntimeExecutionServicePlan, RuntimeFeedPlan, RuntimeFeedServiceBundle, RuntimeFeedServicePlan,
    RuntimeStrategyPlan, RuntimeStrategyServicePlan,
    backtest_composition, historical_simulation_composition,
    live_composition, paper_trading_composition, runtime_execution_plan,
    runtime_feed_plan, runtime_strategy_plan, study_composition,
)
from .strategy_run_loop import (
    CanonicalBarMarketProjection, GovernedStrategyRunLoop, StrategyRunResult,
)
from .strategy_runtime import (
    PaperIntentExecutionBridge, RuntimeStrategyBinding, RuntimeStrategyModelRegistry,
    RuntimeStrategyModelSpec, builtin_runtime_strategy_model_registry, strategy_runtime_runner_from_lock,
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
    "RuntimeStatus", "SystemClock", "KairosApplication",
    "RecoveryBackgroundService", "RuntimeBackgroundService", "RuntimeSupervisor", "SupervisorCycle",
    "write_soak_artifact",
    "AsyncServiceSupervisor",
    "ManagedServiceSnapshot", "ManagedServiceSpec", "ManagedServiceStatus", "ServiceCriticality", "ServiceFault",
    "AsyncKairosRuntime",
    "RunModeComposition", "RuntimeExecutionPlan", "RuntimeExecutionServicePlan",
    "RuntimeFeedPlan", "RuntimeFeedServiceBundle", "RuntimeFeedServicePlan",
    "RuntimeStrategyPlan", "RuntimeStrategyServicePlan",
    "backtest_composition", "historical_simulation_composition",
    "live_composition", "paper_trading_composition", "runtime_execution_plan",
    "runtime_feed_plan", "runtime_strategy_plan", "study_composition",
    "CanonicalBarMarketProjection", "GovernedStrategyRunLoop", "StrategyRunResult",
    "PaperIntentExecutionBridge", "RuntimeStrategyBinding", "RuntimeStrategyModelRegistry",
    "RuntimeStrategyModelSpec", "builtin_runtime_strategy_model_registry", "strategy_runtime_runner_from_lock",
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
