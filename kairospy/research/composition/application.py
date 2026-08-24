"""Construct project-scoped Research use cases."""

from kairospy.research.application import ResearchApplication
from kairospy.system.apps.launch.application.backtests import BacktestApplication
from kairospy.system.apps.workspace.application import Workspace


def compose_research_application(workspace: Workspace) -> ResearchApplication:
    return ResearchApplication(workspace, backtests=BacktestApplication(workspace))


__all__ = ["compose_research_application"]
