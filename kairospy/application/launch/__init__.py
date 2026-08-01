from __future__ import annotations

from .accounts import LaunchAccountBinding, LaunchAccountDirectory
from .scoped_account import LaunchScopedAccountRuntime


def __getattr__(name: str) -> object:
    if name == "LaunchBuilder":
        from .builder import LaunchBuilder

        return LaunchBuilder
    if name == "LaunchEnvironment":
        from .environment import LaunchEnvironment

        return LaunchEnvironment
    if name == "LaunchAlreadyActiveError":
        from .daemon import LaunchAlreadyActiveError

        return LaunchAlreadyActiveError
    if name == "LaunchControl":
        from .control import LaunchControl

        return LaunchControl
    if name == "LaunchFacade":
        from .facade import LaunchFacade

        return LaunchFacade
    if name == "TradingConfigurationError":
        from .launcher import TradingConfigurationError

        return TradingConfigurationError
    if name == "TradingSystemLauncher":
        from .launcher import TradingSystemLauncher

        return TradingSystemLauncher
    raise AttributeError(name)

__all__ = [
    "LaunchAccountBinding",
    "LaunchAccountDirectory",
    "LaunchScopedAccountRuntime",
    "LaunchAlreadyActiveError",
    "LaunchBuilder",
    "LaunchControl",
    "LaunchEnvironment",
    "LaunchFacade",
    "TradingConfigurationError",
    "TradingSystemLauncher",
]
