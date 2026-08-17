"""Private concrete construction for Reference application access."""

from __future__ import annotations

from kairospy.application.workspace import Workspace
from kairospy.infrastructure.contracts.reference import ReferenceClient

from .application import ReferenceApplication


def build_strategy_access(workspace: Workspace) -> ReferenceApplication:
    """Build Reference read access for one Strategy process."""

    return ReferenceApplication(
        ReferenceClient(view_root=workspace.paths.child("snapshots", "v2"))
    )
