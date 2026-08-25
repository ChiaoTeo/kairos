from __future__ import annotations

from kairospy.surface.workbench.theme import KAIROS_THEMES, rich_theme_muted


def test_curated_theme_tokens_match_upstream_palettes() -> None:
    expected = {
        "kairos-tokyo-night": (
            "#24283b",
            "#1f2335",
            "#1b1e2e",
            "#3b4261",
            "#7aa2f7",
            "#7dcfff",
            "#bb9af7",
            "#a9b1d6",
            "#545c7e",
            "#73daca",
            "#e0af68",
            "#f7768e",
        ),
        "kairos-catppuccin": (
            "#1e1e2e",
            "#313244",
            "#45475a",
            "#585b70",
            "#89b4fa",
            "#94e2d5",
            "#cba6f7",
            "#cdd6f4",
            "#a6adc8",
            "#a6e3a1",
            "#f9e2af",
            "#f38ba8",
        ),
        "kairos-nord": (
            "#2e3440",
            "#3b4252",
            "#434c5e",
            "#4c566a",
            "#88c0d0",
            "#81a1c1",
            "#5e81ac",
            "#eceff4",
            "#d8dee9",
            "#a3be8c",
            "#ebcb8b",
            "#bf616a",
        ),
        "kairos-gruvbox": (
            "#282828",
            "#3c3836",
            "#504945",
            "#665c54",
            "#83a598",
            "#8ec07c",
            "#d3869b",
            "#ebdbb2",
            "#928374",
            "#b8bb26",
            "#fabd2f",
            "#fb4934",
        ),
        "kairos-everforest": (
            "#2d353b",
            "#343f44",
            "#3d484d",
            "#475258",
            "#7fbbb3",
            "#83c092",
            "#d699b6",
            "#d3c6aa",
            "#859289",
            "#a7c080",
            "#dbbc7f",
            "#e67e80",
        ),
        "kairos-dracula": (
            "#282a36",
            "#21222c",
            "#44475a",
            "#6272a4",
            "#bd93f9",
            "#8be9fd",
            "#ff79c6",
            "#f8f8f2",
            "#6272a4",
            "#50fa7b",
            "#f1fa8c",
            "#ff5555",
        ),
    }

    actual = {
        theme.name: (
            theme.background,
            theme.surface,
            theme.panel,
            theme.boost,
            theme.primary,
            theme.secondary,
            theme.accent,
            theme.foreground,
            rich_theme_muted(theme),
            theme.success,
            theme.warning,
            theme.error,
        )
        for theme in KAIROS_THEMES
    }

    assert actual == expected
