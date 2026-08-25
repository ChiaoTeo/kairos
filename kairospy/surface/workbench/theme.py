"""Kairos Workbench theme based on the Nord color system."""

from textual.theme import Theme

PRIMARY = "#88c0d0"
SUCCESS = "#a3be8c"
WARNING = "#ebcb8b"
ERROR = "#bf616a"

KAIROS_THEME = Theme(
    name="kairos-nord",
    primary=PRIMARY,
    secondary="#81a1c1",
    warning=WARNING,
    error=ERROR,
    success=SUCCESS,
    accent="#5e81ac",
    foreground="#eceff4",
    background="#2e3440",
    surface="#3b4252",
    panel="#434c5e",
    boost="#4c566a",
    dark=True,
    luminosity_spread=0.10,
    text_alpha=0.96,
    variables={
        "scrollbar": "#4c566a",
        "scrollbar-hover": "#5e81ac",
        "scrollbar-active": PRIMARY,
        "scrollbar-background": "#2e3440",
        "scrollbar-background-hover": "#2e3440",
        "scrollbar-background-active": "#2e3440",
        "scrollbar-corner-color": "#2e3440",
    },
)

__all__ = ["ERROR", "KAIROS_THEME", "PRIMARY", "SUCCESS", "WARNING"]
