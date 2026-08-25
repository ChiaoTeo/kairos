"""Curated dark themes for the Kairos Workbench."""

from __future__ import annotations

from dataclasses import dataclass
from textual.theme import Theme


@dataclass(frozen=True, slots=True)
class RichThemeColors:
    """Semantic colors used by Rich renderables outside Textual CSS."""

    primary: str
    muted: str
    success: str
    warning: str
    error: str


def _theme(
    name: str,
    *,
    primary: str,
    secondary: str,
    accent: str,
    foreground: str,
    muted: str,
    background: str,
    surface: str,
    panel: str,
    boost: str,
    success: str,
    warning: str,
    error: str,
) -> Theme:
    return Theme(
        name=name,
        primary=primary,
        secondary=secondary,
        accent=accent,
        foreground=foreground,
        background=background,
        surface=surface,
        panel=panel,
        boost=boost,
        success=success,
        warning=warning,
        error=error,
        dark=True,
        luminosity_spread=0.10,
        text_alpha=1.0,
        variables={
            "text": foreground,
            "text-muted": muted,
            "scrollbar": boost,
            "scrollbar-hover": accent,
            "scrollbar-active": primary,
            "scrollbar-background": background,
            "scrollbar-background-hover": background,
            "scrollbar-background-active": background,
            "scrollbar-corner-color": background,
        },
    )


TOKYO_NIGHT_THEME = _theme(
    "kairos-tokyo-night",
    primary="#7aa2f7",
    secondary="#7dcfff",
    accent="#bb9af7",
    foreground="#a9b1d6",
    muted="#545c7e",
    background="#24283b",
    surface="#1f2335",
    panel="#1b1e2e",
    boost="#3b4261",
    success="#73daca",
    warning="#e0af68",
    error="#f7768e",
)

CATPPUCCIN_THEME = _theme(
    "kairos-catppuccin",
    primary="#89b4fa",
    secondary="#94e2d5",
    accent="#cba6f7",
    foreground="#cdd6f4",
    muted="#a6adc8",
    background="#1e1e2e",
    surface="#313244",
    panel="#45475a",
    boost="#585b70",
    success="#a6e3a1",
    warning="#f9e2af",
    error="#f38ba8",
)

KAIROS_THEME = _theme(
    "kairos-nord",
    primary="#88c0d0",
    secondary="#81a1c1",
    accent="#5e81ac",
    foreground="#eceff4",
    muted="#d8dee9",
    background="#2e3440",
    surface="#3b4252",
    panel="#434c5e",
    boost="#4c566a",
    success="#a3be8c",
    warning="#ebcb8b",
    error="#bf616a",
)

GRUVBOX_THEME = _theme(
    "kairos-gruvbox",
    primary="#83a598",
    secondary="#8ec07c",
    accent="#d3869b",
    foreground="#ebdbb2",
    muted="#928374",
    background="#282828",
    surface="#3c3836",
    panel="#504945",
    boost="#665c54",
    success="#b8bb26",
    warning="#fabd2f",
    error="#fb4934",
)

EVERFOREST_THEME = _theme(
    "kairos-everforest",
    primary="#7fbbb3",
    secondary="#83c092",
    accent="#d699b6",
    foreground="#d3c6aa",
    muted="#859289",
    background="#2d353b",
    surface="#343f44",
    panel="#3d484d",
    boost="#475258",
    success="#a7c080",
    warning="#dbbc7f",
    error="#e67e80",
)

DRACULA_THEME = _theme(
    "kairos-dracula",
    primary="#bd93f9",
    secondary="#8be9fd",
    accent="#ff79c6",
    foreground="#f8f8f2",
    muted="#6272a4",
    background="#282a36",
    surface="#21222c",
    panel="#44475a",
    boost="#6272a4",
    success="#50fa7b",
    warning="#f1fa8c",
    error="#ff5555",
)

KAIROS_THEMES = (
    TOKYO_NIGHT_THEME,
    CATPPUCCIN_THEME,
    KAIROS_THEME,
    GRUVBOX_THEME,
    EVERFOREST_THEME,
    DRACULA_THEME,
)

THEME_CHOICES = (
    ("Tokyo Night", TOKYO_NIGHT_THEME),
    ("Catppuccin Mocha", CATPPUCCIN_THEME),
    ("Nord", KAIROS_THEME),
    ("Gruvbox Dark", GRUVBOX_THEME),
    ("Everforest Dark", EVERFOREST_THEME),
    ("Dracula", DRACULA_THEME),
)

THEME_ALIASES = {
    "tokyo": TOKYO_NIGHT_THEME.name,
    "tokyo-night": TOKYO_NIGHT_THEME.name,
    "catppuccin": CATPPUCCIN_THEME.name,
    "nord": KAIROS_THEME.name,
    "gruvbox": GRUVBOX_THEME.name,
    "everforest": EVERFOREST_THEME.name,
    "dracula": DRACULA_THEME.name,
}

_CANONICAL_ALIASES = {
    TOKYO_NIGHT_THEME.name: "tokyo-night",
    CATPPUCCIN_THEME.name: "catppuccin",
    KAIROS_THEME.name: "nord",
    GRUVBOX_THEME.name: "gruvbox",
    EVERFOREST_THEME.name: "everforest",
    DRACULA_THEME.name: "dracula",
}


def resolve_theme_name(name: str) -> str | None:
    """Resolve a user-facing alias or registered Kairos theme name."""

    normalized = name.strip().lower().removeprefix("kairos-")
    candidate = THEME_ALIASES.get(normalized, f"kairos-{normalized}")
    if candidate not in {theme.name for theme in KAIROS_THEMES}:
        return None
    return candidate


def theme_alias(name: str) -> str | None:
    """Return the stable project-config value for a registered theme."""

    return _CANONICAL_ALIASES.get(name)


def rich_theme_colors(theme: Theme) -> RichThemeColors:
    """Return the active semantic colors in a Rich-friendly shape."""

    return RichThemeColors(
        primary=theme.primary,
        muted=rich_theme_muted(theme),
        success=theme.success or theme.primary,
        warning=theme.warning or theme.primary,
        error=theme.error or theme.primary,
    )


def rich_theme_muted(theme: Theme) -> str:
    """Return a concrete secondary-text color for CSS-adjacent Rich content."""

    return theme.variables.get("text-muted") or theme.foreground or theme.primary


def rich_theme_foreground(theme: Theme) -> str:
    """Return the theme's full-strength primary text color."""

    return theme.foreground or theme.primary


NORD_COLORS = rich_theme_colors(KAIROS_THEME)
PRIMARY = NORD_COLORS.primary
SUCCESS = NORD_COLORS.success
WARNING = NORD_COLORS.warning
ERROR = NORD_COLORS.error

__all__ = [
    "ERROR",
    "KAIROS_THEME",
    "KAIROS_THEMES",
    "NORD_COLORS",
    "PRIMARY",
    "RichThemeColors",
    "SUCCESS",
    "THEME_ALIASES",
    "THEME_CHOICES",
    "WARNING",
    "resolve_theme_name",
    "rich_theme_colors",
    "rich_theme_foreground",
    "rich_theme_muted",
    "theme_alias",
]
