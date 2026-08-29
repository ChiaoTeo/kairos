"""Dispatch advanced Workbench input to owner Application facades."""

from __future__ import annotations

from typing import Any

from kairospy.investment.apps.account.application.cli import AccountCliApplication
from kairospy.investment.apps.market.application.cli import (
    MarketCliApplication,
    parse_market_command_line,
)
from kairospy.investment.apps.reference.application.cli import ReferenceCliApplication
from kairospy.system.apps.components.application import NativeCliApplication
from kairospy.system.apps.integration.application import IntegrationCliApplication


NATIVE_COMPONENTS = {"capital", "execution", "risk"}
SUPPORTED_COMPONENTS = NATIVE_COMPONENTS | {
    "account",
    "integration",
    "market",
    "reference",
}
DANGEROUS_ACTIONS = {
    "add",
    "cancel",
    "create",
    "delete",
    "disable",
    "down",
    "enable",
    "modify",
    "publish",
    "register",
    "remove",
    "repair",
    "replace",
    "restart",
    "start",
    "stop",
    "submit",
    "sync",
    "test",
    "transfer",
    "up",
}


def normalize(state: Any, argv: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize a pasted shell equivalent into Workbench component input."""

    if not argv:
        return argv
    if argv[0] == "kairos":
        argv = argv[1:]
    if not argv or argv[0] != "market":
        return argv
    command = parse_market_command_line(argv[1:])
    if command.workspace is not None:
        if state.owner is None:
            raise RuntimeError(state.load_error or "当前没有可用的 workspace")
        expected = state.owner.paths.root.expanduser().resolve()
        supplied = command.workspace.expanduser().resolve()
        if supplied != expected:
            raise ValueError(
                f"命令 workspace 为 {supplied}，当前 Workbench workspace 为 {expected}。"
            )
    return ("market", *command.arguments)


def run(state: Any, argv: tuple[str, ...]) -> Any:
    """Run one canonical owner CLI command with machine-readable output."""

    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 workspace")
    if not argv:
        raise ValueError("请输入 kairos 子命令。")
    validate(argv)
    component, arguments = argv[0], list(argv[1:])
    if component == "market":
        return MarketCliApplication(state.owner).run(_standalone(arguments))
    if component == "reference":
        return ReferenceCliApplication(state.owner).run(_reference_arguments(arguments))
    if component == "account":
        return AccountCliApplication(state.owner).run(arguments)
    if component in NATIVE_COMPONENTS:
        return NativeCliApplication(state.owner).run(component, _standalone(arguments))
    if component == "integration":
        return IntegrationCliApplication().run(arguments)
    raise AssertionError(f"validated unsupported component: {component}")


def validate(argv: tuple[str, ...]) -> None:
    """Reject commands that cannot reach an owner Application, including previews."""

    if not argv:
        raise ValueError("请输入 kairos 子命令。")
    component = argv[0]
    if component not in SUPPORTED_COMPONENTS:
        raise ValueError(
            f"kairos {component} 尚未接入单输入 Application 分派；"
            "请选择当前菜单中的操作。"
        )


def is_dangerous(argv: tuple[str, ...]) -> bool:
    return any(part.casefold() in DANGEROUS_ACTIONS for part in argv[1:])


def preview(argv: tuple[str, ...]) -> dict[str, Any]:
    return {
        "command": ["kairos", *argv],
        "executed": False,
        "reason": "dry-run/no-exec",
    }


def _standalone(arguments: list[str]) -> list[str]:
    if arguments and arguments[0] in {"standalone", "connected"}:
        return arguments
    return ["standalone", *arguments]


def _reference_arguments(arguments: list[str]) -> list[str]:
    if not arguments:
        raise ValueError("kairos reference 需要一个目录查询命令。")
    if arguments[0] in {"standalone", "connected"}:
        return arguments
    command, rest = arguments[0], arguments[1:]
    if command == "markets":
        return ["standalone", "markets", "list", *rest]
    if command == "assets":
        if rest and rest[0] in {"list", "show"}:
            return ["standalone", "assets", *rest]
        return ["standalone", "assets", "list", *rest]
    if command in {"exchanges", "instruments", "listings"}:
        return ["standalone", "catalog", command, *rest]
    if command == "catalog" and not rest:
        return ["standalone", "snapshot"]
    return ["standalone", *arguments]


__all__ = ["is_dangerous", "normalize", "preview", "run", "validate"]
