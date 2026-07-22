"""Kairos quantitative data, workspace, strategy protocol, and run toolkit."""

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
    if name == "Workspace":
        from kairospy.workspace import Workspace

        return Workspace
    raise AttributeError(f"module 'kairospy' has no attribute {name!r}")
