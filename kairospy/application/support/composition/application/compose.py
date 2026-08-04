"""Single composition-root entry point.

This module intentionally exposes only mode selection and resource assembly.
Lifecycle execution remains in launch/system application.
"""

from __future__ import annotations

from .launcher import ConfiguredLaunchComposer, market_feed_resolver_builder


def compose_launch(configured: object, *, mode: str):
    composer = ConfiguredLaunchComposer()
    builders = {
        "live": composer.live,
        "paper": composer.paper,
        "backtest": composer.backtest,
    }
    try:
        builder = builders[mode.strip().lower()]
    except KeyError as error:
        raise ValueError(f"unsupported composition mode: {mode}") from error
    return builder(configured)  # type: ignore[arg-type]


__all__ = ["ConfiguredLaunchComposer", "compose_launch", "market_feed_resolver_builder"]
