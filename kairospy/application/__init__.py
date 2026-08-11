"""Public Python application APIs."""

from .workspace import (
    Workspace,
    WorkspaceApplication,
    WorkspaceIdentity,
    WorkspacePaths,
)


def run_backtest(*args, **kwargs):
    """Lazy facade that avoids importing transport clients during bootstrap."""
    from .backtest import run_backtest as implementation

    return implementation(*args, **kwargs)


__all__ = [
    "Workspace",
    "WorkspaceApplication",
    "WorkspaceIdentity",
    "WorkspacePaths",
    "run_backtest",
]
