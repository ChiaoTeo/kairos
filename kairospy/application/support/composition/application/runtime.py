"""Composition of generic runtime launch resources."""

from kairospy.application.support.launch.application.resources import LaunchAssembly
from kairospy.application.support.composition.application.artifacts import launch_output


def compose_runtime_assembly() -> LaunchAssembly:
    return LaunchAssembly(output=launch_output)


__all__ = ["compose_runtime_assembly"]
