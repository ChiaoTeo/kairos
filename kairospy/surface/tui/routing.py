from __future__ import annotations

from kairospy.surface.tui.models import ResourceList
from kairospy.surface.tui.screens import (
    AccountsScreen,
    LaunchTargetsScreen,
    OrdersScreen,
    ReferenceAssetsScreen,
    ReferenceMarketsScreen,
    ResourceBrowserScreen,
)


def screen_for_resource(resource: ResourceList) -> type[ResourceBrowserScreen]:
    return _RESOURCE_SCREENS.get(resource.title, ResourceBrowserScreen)


_RESOURCE_SCREENS: dict[str, type[ResourceBrowserScreen]] = {
    "Accounts": AccountsScreen,
    "Launch Targets": LaunchTargetsScreen,
    "Open Orders": OrdersScreen,
    "Reference Assets": ReferenceAssetsScreen,
    "Reference Markets": ReferenceMarketsScreen,
}


__all__ = ["screen_for_resource"]
