"""Public Launch use cases loaded only for the requested capability."""

from __future__ import annotations

from typing import Any


_EXPORT_MODULE = {
    "LaunchInstanceApplication": "instance",
    "InstanceControlTarget": "control",
    "LaunchControlApplication": "control",
    "LaunchRegistryApplication": "registry",
    "LaunchInstanceTimelineApplication": "instance_timeline",
    "LaunchConfig": "configuration",
    "LaunchConfigError": "configuration",
    "LaunchConfigReport": "configuration",
    "LaunchConfigurationApplication": "configuration",
    "LaunchEnvironment": "configuration",
    "LaunchPlan": "configuration",
    "new_instance_id": "identity",
    "BacktestResult": "specs",
    "BacktestSpec": "specs",
    "BacktestApplication": "backtests",
    "OptionBacktestConstraints": "semantics",
    "LaunchRuntimeApplication": "runtime",
    "LaunchRuntimeError": "runtime",
    "BacktestCorrelationTrace": "reporting",
    "BacktestReportContext": "reporting",
    "CanonicalBacktestReportApplication": "reporting",
    "StrategyProcessController": "strategy_process",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if module_name == "identity":
        from ..domain.identity import new_instance_id

        return new_instance_id
    from importlib import import_module

    module = import_module(f"{__name__}.{module_name}")
    return getattr(module, name)


__all__ = list(_EXPORT_MODULE)
