from __future__ import annotations

from .accounts import AccountRegistry, RuntimeAccount
from .journal import RunAccountJournal

__all__ = [
    "AccountRegistry",
    "ConfiguredEventMode",
    "RunConfigurationError",
    "RunAccountJournal",
    "RuntimeAccount",
    "configured_event_mode",
    "configured_live_target",
    "configured_streaming_paper_target",
]


def __getattr__(name: str) -> object:
    if name == "configured_live_target":
        from kairospy.application.service.modes.live import configured_live_target

        return configured_live_target
    if name in {
        "ConfiguredEventMode",
        "RunConfigurationError",
        "configured_event_mode",
        "configured_streaming_paper_target",
    }:
        from . import config

        return getattr(config, name)
    raise AttributeError(name)
