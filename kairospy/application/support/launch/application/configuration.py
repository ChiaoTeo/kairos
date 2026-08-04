"""Public launch configuration API.

The mode-specific configuration objects are implemented in the launch domain,
but callers use this module as the stable application boundary.
"""

from __future__ import annotations

from kairospy.application.support.launch.application.config.backtest import (
    BacktestConfigurationError,
    BacktestLaunchResult,
    ConfiguredBacktest,
    configured_backtest,
)
from kairospy.application.support.launch.application.config.common import (
    ConfiguredAccount,
    ConfiguredCredential,
    FeedConfig,
    slippage_model,
)
from kairospy.application.support.launch.application.config.live import (
    BrokerFactory,
    ConfiguredLive,
    LiveConfigurationError,
    LiveLaunchResult,
    MarketFeedFactory as LiveMarketFeedFactory,
    configured_live,
)
from kairospy.application.support.launch.application.config.paper import (
    ConfiguredPaper,
    MarketFeedFactory as PaperMarketFeedFactory,
    PaperConfigurationError,
    PaperLaunchResult,
    configured_paper,
)
from kairospy.application.support.launch.application.config.launch import (
    AccountConfig,
    ConfigError,
    LaunchAccountConfig,
    LaunchConfig,
    RESERVED_LAUNCH_IDS,
    SYSTEM_LAUNCH_ID,
    load_launch_config,
)
from kairospy.application.usecases.workspace.domain.config import CONFIG_FILENAME, find_manifest_path

__all__ = [
    "BacktestConfigurationError",
    "BacktestLaunchResult",
    "BrokerFactory",
    "AccountConfig",
    "CONFIG_FILENAME",
    "ConfigError",
    "ConfiguredAccount",
    "ConfiguredBacktest",
    "ConfiguredCredential",
    "ConfiguredLive",
    "ConfiguredPaper",
    "FeedConfig",
    "LaunchConfig",
    "LaunchAccountConfig",
    "RESERVED_LAUNCH_IDS",
    "SYSTEM_LAUNCH_ID",
    "LiveConfigurationError",
    "LiveLaunchResult",
    "LiveMarketFeedFactory",
    "PaperConfigurationError",
    "PaperLaunchResult",
    "PaperMarketFeedFactory",
    "configured_backtest",
    "configured_live",
    "configured_paper",
    "find_manifest_path",
    "load_launch_config",
    "slippage_model",
]
