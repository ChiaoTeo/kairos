from __future__ import annotations

def __getattr__(name: str) -> object:
    if name == "RunControl":
        from .facade.run_control import RunControl

        return RunControl
    if name == "RunAlreadyActiveError":
        from .control.daemon import RunAlreadyActiveError

        return RunAlreadyActiveError
    if name == "TradingConfigurationError":
        from .facade.trading import TradingConfigurationError

        return TradingConfigurationError
    if name == "TradingSystemLauncher":
        from .facade.trading import TradingSystemLauncher

        return TradingSystemLauncher
    raise AttributeError(name)

__all__ = ["RunAlreadyActiveError", "RunControl", "TradingConfigurationError", "TradingSystemLauncher"]
