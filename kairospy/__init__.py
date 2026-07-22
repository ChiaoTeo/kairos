"""Kairos quantitative data, workspace, strategy protocol, and run toolkit."""

from kairospy.workspace import Workspace

__version__ = "0.1.0"

__all__ = [
    "Workspace",
    "__version__",
    "initialize_project",
]


def __getattr__(name: str):
    if name == "initialize_project":
        from kairospy.surface.project import initialize_project

        return initialize_project
    raise AttributeError(f"module 'kairospy' has no attribute {name!r}")
