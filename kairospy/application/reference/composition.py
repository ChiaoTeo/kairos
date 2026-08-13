"""Private concrete construction for Reference application access."""

from __future__ import annotations

from kairospy.application.workspace import Workspace
from kairospy.infrastructure.contracts.reference_client import ReferenceClient

from .application import ReferenceApplication


def build_strategy_access(workspace: Workspace) -> ReferenceApplication:
    """Build Reference read access for one Strategy process."""

    return ReferenceApplication(
        ReferenceClient(database_path=workspace.paths.reference_database())
    )
