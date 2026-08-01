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
    return _ordered_child_names(path, tuple(name for name, child in node.commands.items() if not _is_hidden(child)))


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
        current_names = child_names(root, current_path)
        if parts[0].isdigit() or resolve_token(parts[0], names=current_names) is not None:
            return None, parts
    root_name = resolve_token(parts[0], names=root_names)
    if root_name is None:
        return None, parts
    return _match_child_context(root, (root_name,), parts[1:], fallback=(root_name,))


def has_children(command: CommandInfo | None) -> bool:
    return hasattr(command, "commands")


def _is_hidden(command: CommandInfo) -> bool:
    return bool(getattr(command, "hidden", False))


def _ordered_child_names(path: tuple[str, ...], names: tuple[str, ...]) -> tuple[str, ...]:
    order = _CHILD_ORDER.get(path)
    if order is None:
        return names
    rank = {name: index for index, name in enumerate(order)}
    return tuple(sorted(names, key=lambda name: (rank.get(name, len(rank)), names.index(name))))


_CHILD_ORDER = {
    ("project",): (
        "init",
        "status",
        "doctor",
        "config",
    ),
    ("project", "config"): (
        "paths",
        "show",
        "manifest",
        "doctor",
        "explain",
        "operations",
        "profile",
    ),
    ("launch",): (
        "targets",
        "diagnose",
        "replay",
        "timeline",
        "start",
        "stop",
        "status",
        "logs",
        "artifacts",
        "instances",
    ),
    ("launch", "targets"): (
        "add",
        "remove",
        "index",
        "list",
        "browse",
    ),
    ("launch", "diagnose"): (
        "validate",
        "explain",
    ),
    ("launch", "replay"): (
        "events",
    ),
    ("launch", "timeline"): (
        "list",
        "export",
        "open",
        "api",
    ),
    ("account",): (
        "list",
        "browse",
        "schemas",
        "schema",
        "create",
        "modify",
        "delete",
        "remove",
        "show",
        "doctor",
        "credential",
        "query",
        "trade-lock",
    ),
    ("account", "credential"): (
        "add",
        "list",
        "create",
        "show",
        "delete",
        "remove",
    ),
    ("account", "query"): (
        "balance",
        "current",
        "balances",
        "positions",
        "open-orders",
        "snapshot",
    ),
    ("account", "trade-lock"): (
        "status",
        "list",
        "show",
        "release",
    ),
    ("order",): (
        "open",
        "list",
        "browse",
        "closed",
        "history",
        "place",
        "cancel",
        "replace",
        "show",
        "inspect",
    ),
    ("market",): (
        "source",
        "data",
        "dataset",
        "stream",
    ),
    ("market", "source"): (
        "capabilities",
        "check",
        "doctor",
    ),
    ("market", "data"): (
        "download",
        "prefetch",
    ),
    ("market", "dataset"): (
        "list",
        "inspect",
        "alias",
        "prune",
        "read",
    ),
    ("market", "stream"): (
        "replay",
        "watch",
        "persist",
    ),
    ("catalog",): (
        "sync",
        "participants",
        "assets",
        "markets",
        "events",
        "view",
        "query",
        "search",
        "show",
        "status",
    ),
    ("catalog", "sync"): (
        "binance",
        "hyperliquid",
        "massive",
    ),
    ("catalog", "participants"): (
        "brokers",
        "exchanges",
        "providers",
    ),
    ("catalog", "assets"): (
        "add",
        "list",
        "browse",
        "show",
    ),
    ("catalog", "markets"): (
        "list",
        "browse",
        "resolve",
    ),
    ("catalog", "events"): (
        "sync",
    ),
    ("system",): (
        "status",
        "inspect",
        "attach",
        "up",
        "down",
        "restart",
        "account",
        "command",
    ),
    ("system", "account"): (
        "trade-status",
        "trade-acquire",
        "trade-release",
    ),
}


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
        child_name = resolve_token(rest[0], names=child_names(root, path))
        if child_name is None:
            break
        candidate = (*path, child_name)
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
