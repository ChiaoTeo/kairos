from __future__ import annotations

from typing import Protocol

import typer
from typer.main import get_command


class CommandInfo(Protocol):
    name: str | None
    help: str | None


class _CommandContainer(CommandInfo, Protocol):
    commands: dict[str, CommandInfo]


def root_command(app: typer.Typer) -> CommandInfo:
    command = get_command(app)
    if not has_children(command):
        raise TypeError("interactive shell requires a command group root")
    return command


def command_at(root: CommandInfo, path: tuple[str, ...]) -> CommandInfo | None:
    node: CommandInfo = root
    for part in path:
        if not has_children(node):
            return None
        node = node.commands.get(part)
        if node is None:
            return None
    return node


def child_names(root: CommandInfo, path: tuple[str, ...]) -> tuple[str, ...]:
    node = command_at(root, path)
    if not has_children(node):
        return ()
    return tuple(node.commands)


def resolve_token(token: str, *, names: tuple[str, ...]) -> str | None:
    if token.isdigit():
        index = int(token)
        if 1 <= index <= len(names):
            return names[index - 1]
        return None
    normalized = token.strip().lower()
    return normalized if normalized in names else None


def match_group_context(
    root: CommandInfo,
    current_path: tuple[str, ...],
    parts: list[str],
    *,
    root_names: tuple[str, ...],
) -> tuple[tuple[str, ...] | None, list[str]]:
    if not parts:
        return None, []
    if current_path:
        matched, rest = _match_child_context(root, current_path, parts)
        if matched is not None:
            return matched, rest
    root_name = resolve_token(parts[0], names=root_names)
    if root_name is None:
        return None, parts
    return _match_child_context(root, (root_name,), parts[1:], fallback=(root_name,))


def has_children(command: CommandInfo | None) -> bool:
    return hasattr(command, "commands")


def _match_child_context(
    root: CommandInfo,
    start_path: tuple[str, ...],
    parts: list[str],
    *,
    fallback: tuple[str, ...] | None = None,
) -> tuple[tuple[str, ...] | None, list[str]]:
    path = start_path
    rest = parts
    matched = fallback if _is_group_path(root, path) else None
    while rest and _is_group_path(root, path):
        candidate = (*path, rest[0])
        if not _is_group_path(root, candidate):
            break
        path = candidate
        rest = rest[1:]
        matched = path
    if matched is None:
        return None, parts
    return matched, rest


def _is_group_path(root: CommandInfo, path: tuple[str, ...]) -> bool:
    node = command_at(root, path)
    return node is not None and has_children(node)
