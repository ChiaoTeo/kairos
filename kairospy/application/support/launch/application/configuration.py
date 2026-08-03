"""Public launch configuration API.

The mode-specific configuration objects are implemented in the launch domain,
but callers use this module as the stable application boundary.
"""

from __future__ import annotations

from kairospy.application.support.launch.domain.config.backtest import (
    BacktestConfigurationError,
    BacktestLaunchResult,
    ConfiguredBacktest,
    configured_backtest,
)
from kairospy.application.support.launch.domain.config.common import (
    ConfiguredAccount,
    ConfiguredCredential,
    FeedConfig,
    slippage_model,
)
from kairospy.application.support.launch.domain.config.live import (
    BrokerFactory,
    ConfiguredLive,
    LiveConfigurationError,
    LiveLaunchResult,
    MarketFeedFactory as LiveMarketFeedFactory,
    configured_live,
)
from kairospy.application.support.launch.domain.config.paper import (
    ConfiguredPaper,
    MarketFeedFactory as PaperMarketFeedFactory,
    PaperConfigurationError,
    PaperLaunchResult,
    configured_paper,
)
from kairospy.application.support.system.application.config import ConfigError, LaunchConfig, load_launch_config

__all__ = [
    "BacktestConfigurationError",
    "BacktestLaunchResult",
    "BrokerFactory",
    "ConfigError",
    "ConfiguredAccount",
    "ConfiguredBacktest",
    "ConfiguredCredential",
    "ConfiguredLive",
    "ConfiguredPaper",
    "FeedConfig",
    "LaunchConfig",
    "LiveConfigurationError",
    "LiveLaunchResult",
    "LiveMarketFeedFactory",
    "PaperConfigurationError",
    "PaperLaunchResult",
    "PaperMarketFeedFactory",
    "configured_backtest",
    "configured_live",
    "configured_paper",
    "load_launch_config",
    "slippage_model",
]
