"""Launch result assembly owned by launch application."""

from __future__ import annotations

from kairospy.application.support.launch.application.runtime import LaunchRuntimeResult
from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.usecases.execution.application.backtest import (
    MetricsModel,
    closed_trades_from_fills,
    equity_point_from_account_view,
)
from kairospy.domain.account import account_current_view_key


def backtest_result(configured: object, resources: object, runtime: LaunchRuntimeResult) -> object:
    account = resources.account
    account_config = account.account
    account_view = runtime.views.get(account_current_view_key(account_config.context), None)
    fills = resources.execution.fills
    equity_curve = _equity_curve(runtime)
    trades = closed_trades_from_fills(fills)
    metrics = MetricsModel().evaluate(equity_curve, trades, initial_equity=account_config.initial_cash)
    from kairospy.application.support.launch.application.configuration import BacktestLaunchResult

    return BacktestLaunchResult(
        launch_id=configured.launch_id,
        mode=RuntimeMode.BACKTEST,
        runtime=runtime.runtime,
        views=runtime.views,
        intents=runtime.intents,
        account=account_config.context,
        account_view=account_view,
        fills=fills,
        equity_curve=equity_curve,
        trades=trades,
        metrics=metrics,
    )


def paper_result(configured: object, resources: object, runtime: LaunchRuntimeResult) -> object:
    account_context = resources.account.account.context
    fills = tuple(resources.execution.fills)
    from kairospy.application.support.launch.application.configuration import PaperLaunchResult

    return PaperLaunchResult(
        launch_id=configured.launch_id,
        mode=RuntimeMode.PAPER,
        runtime=runtime.runtime,
        views=runtime.views,
        intents=runtime.intents,
        account=account_context,
        account_view=runtime.views.get(account_current_view_key(account_context), None),
        fills=fills,
        trades=(),
        metrics={},
    )


def live_result(configured: object, resources: object, runtime: LaunchRuntimeResult) -> object:
    from kairospy.application.support.launch.application.configuration import LiveLaunchResult

    account_context = resources.account.account
    return LiveLaunchResult(
        launch_id=configured.launch_id,
        mode=RuntimeMode.LIVE,
        runtime=runtime.runtime,
        views=runtime.views,
        intents=runtime.intents,
        account=account_context,
        account_view=runtime.views.get(account_current_view_key(account_context), None),
    )


def _equity_curve(runtime: LaunchRuntimeResult) -> tuple[object, ...]:
    equity_view = runtime.views.get("account.equity_curve", None)
    points = tuple(getattr(equity_view, "points", ()) or ())
    if points:
        return points
    account_keys = tuple(key for key in runtime.views.envelopes() if key.startswith("account.current."))
    account_view = runtime.views.get(account_keys[0], None) if account_keys else None
    return tuple(
        item
        for item in (
            equity_point_from_account_view(
                None if runtime.runtime.last_event is None else runtime.runtime.last_event.time,
                account_view,
            ),
        )
        if item is not None
    )


__all__ = ["backtest_result", "live_result", "paper_result"]
