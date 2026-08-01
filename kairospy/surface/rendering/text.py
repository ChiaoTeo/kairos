from __future__ import annotations

from typing import Protocol

from kairospy.surface.interactive.session import SurfaceSnapshot, render_surface_overview


class CommandView(Protocol):
    name: str
    label: str
    description: str
    argv_prefix: tuple[str, ...]


def render_home_screen(snapshot: SurfaceSnapshot, commands: tuple[CommandView, ...], *, surface_name: str = "app") -> str:
    return "\n\n".join([
        render_surface_overview(snapshot),
        render_command_registry("Products", commands),
        render_global_commands(surface_name=surface_name),
    ])


def render_context_screen(
    snapshot: SurfaceSnapshot,
    command: CommandView,
    children: tuple[CommandView, ...],
) -> str:
    sections = [
        render_surface_overview(snapshot),
        render_command_detail(command),
    ]
    if children:
        sections.append(render_command_registry("Subcommands", children))
        sections.append(render_context_commands(command))
    else:
        sections.append(render_leaf_commands(command))
    return "\n\n".join(sections)


def render_command_registry(title: str, commands: tuple[CommandView, ...]) -> str:
    lines = [
        title,
        "  #  command       description",
        "  -  ------------  -----------",
    ]
    for index, command in enumerate(commands, start=1):
        lines.append(f"  {index:<2} {command.name:<12}  {command.description}")
    return "\n".join(lines)


def render_command_detail(command: CommandView) -> str:
    path = "/".join(command.argv_prefix)
    return "\n".join([
        command.label,
        f"  context  {path}",
        f"  command  {' '.join(command.argv_prefix)}",
        f"  help     {command.description or '-'}",
    ])


def render_global_commands(*, surface_name: str = "app") -> str:
    return _render_command_rows((
        ("<#>|<product>", "open command context"),
        ("refresh", f"reload {surface_name} state"),
        ("quit", f"exit {surface_name}"),
    ))


def render_context_commands(command: CommandView) -> str:
    prefix = " ".join(command.argv_prefix)
    return _render_command_rows((
        ("<#>|<subcommand>", "open subcommand context"),
        ("<command>", f"execute `kairospy {prefix} <command>`"),
        ("help", "show this context"),
        ("back", "return to parent context"),
        ("home", "return to products"),
    ))


def render_leaf_commands(command: CommandView) -> str:
    prefix = " ".join(command.argv_prefix)
    return _render_command_rows((
        ("<args>", f"execute `kairospy {prefix} <args>`"),
        ("help", "show command help"),
        ("back", "return to parent context"),
        ("home", "return to products"),
    ))


def _render_command_rows(rows: tuple[tuple[str, str], ...]) -> str:
    width = max((len(command) for command, _ in rows), default=0)
    lines = ["Commands"]
    for command, description in rows:
        lines.append(f"  {command:<{width}}  {description}")
    return "\n".join(lines)


__all__ = [
    "render_command_detail",
    "render_command_registry",
    "render_context_commands",
    "render_context_screen",
    "render_global_commands",
    "render_home_screen",
    "render_leaf_commands",
]
