from __future__ import annotations

from textual.app import App
from textual.screen import Screen

from kairospy.surface.tui.models import ResourceList
from kairospy.surface.tui.screens import ResourceBrowserScreen
from kairospy.surface.tui.routing import screen_for_resource
from kairospy.surface.tui.styles import RESOURCE_BROWSER_CSS


class KairosTuiApp(App[None]):
    """Top-level Textual app for Kairos screens."""

    CSS = RESOURCE_BROWSER_CSS

    def __init__(self, initial_screen: Screen[None] | None = None) -> None:
        super().__init__()
        self.initial_screen = initial_screen
        self.title = "Kairos"

    def on_mount(self) -> None:
        if self.initial_screen is not None:
            self.push_screen(self.initial_screen)


class ResourceBrowserApp(KairosTuiApp):
    """Single-screen app shell for CLI browse commands."""

    def __init__(
        self,
        resource: ResourceList,
        *,
        screen_type: type[ResourceBrowserScreen] | None = None,
    ) -> None:
        self.resource = resource
        self.screen_type = screen_type or screen_for_resource(resource)
        super().__init__()
        self.title = f"Kairos | {resource.title}"

    def on_mount(self) -> None:
        self.push_screen(self.screen_type(self.resource, id="resource-browser"))


__all__ = ["KairosTuiApp", "ResourceBrowserApp"]
