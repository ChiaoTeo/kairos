from __future__ import annotations

from kairospy.surface.cli.commands.account import account_app
from kairospy.surface.cli.commands.market import market_app
from kairospy.surface.cli.commands.order import order_app
from kairospy.surface.cli.commands.project import init as project_init, project_app
from kairospy.surface.cli.commands.reference import reference_app
from kairospy.surface.cli.commands.launch import launch_app
from kairospy.surface.cli.commands.system import system_app


__all__ = [
    "account_app",
    "market_app",
    "order_app",
    "project_app",
    "project_init",
    "reference_app",
    "launch_app",
    "system_app",
]
