from __future__ import annotations

import shlex
import sys
from typing import Callable, TextIO

from kairospy.surface.interactive.navigation import (
    CommandInfo,
    child_names,
    command_at,
    match_group_context,
    resolve_token,
    root_command,
)
from kairospy.surface.interactive.session import SurfaceContext
from kairospy.surface.rendering.text import (
    render_context_screen,
    render_home_screen,
)


CommandExecutor = Callable[[list[str]], tuple[int, str]]


class CommandView:
    def __init__(self, *, name: str, command: CommandInfo, argv_prefix: tuple[str, ...]) -> None:
        self.name = name
        self.label = _label(argv_prefix)
        self.description = command.help or ""
        self.argv_prefix = argv_prefix


class AppSession:
    def __init__(
        self,
        *,
        stdout: TextIO | None = None,
        context: SurfaceContext | None = None,
        command_executor: CommandExecutor | None = None,
        command_root: CommandInfo | None = None,
    ) -> None:
        self.stdout = stdout or sys.stdout
        self.context = context or SurfaceContext(product="home")
        self.command_executor = command_executor or _missing_executor
        self.command_root = command_root or _default_command_root()
        self.context_path: tuple[str, ...] = ()
        self.context.set_product("home")

    @property
    def product(self) -> CommandView | None:
        if not self.context_path:
            return None
        return self._view((self.context_path[0],))

    @property
    def current_node(self) -> CommandInfo | None:
        return command_at(self.command_root, self.context_path)

    def banner(self) -> str:
        return "Kairos app. Navigate Typer command contexts; commands keep the same argv semantics as the plain CLI."

    def prompt(self) -> str:
        if not self.context_path:
            return "kairos/app> "
        return f"kairos/app/{'/'.join(self.context_path)}> "

    def screen(self) -> str:
        snapshot = self.context.snapshot()
        node = self._current_view()
        product = self.product
        if node is None or product is None:
            return render_home_screen(snapshot, self._root_views())
        return render_context_screen(snapshot, node, self._child_views(self.context_path))

    def run(self) -> None:
        self._write(self.banner())
        self._write(self.screen())
        while True:
            try:
                line = input(self.prompt())
            except EOFError:
                self._write("")
                return
            except KeyboardInterrupt:
                self._write("\nUse `quit` to exit.")
                continue
            if self.handle(line):
                return

    def handle(self, line: str) -> bool:
        parts = shlex.split(line.strip())
        if not parts:
            self._write(self.screen())
            return False
        command = parts[0]
        if command in {"quit", "exit", "q"}:
            return True
        if command in {"help", "?", "menu"}:
            self._write(self.screen())
            return False
        if command in {"home", "products"}:
            self._open_home()
            self._write(self.screen())
            return False
        if command in {"back", "b", ".."}:
            if not self.context_path:
                self._write(self.screen())
                return False
            self._set_context_path(self.context_path[:-1])
            self._write(self.screen())
            return False
        if command in {"refresh", "r"}:
            self.context.refresh()
            self._write(self.screen())
            return False
        matched_context, rest = match_group_context(
            self.command_root,
            self.context_path,
            parts,
            root_names=self._root_names(),
        )
        if matched_context is not None:
            self._set_context_path(matched_context)
            if rest:
                return self._handle_in_context(rest)
            self._write(self.screen())
            return False
        if not self.context_path:
            if resolve_token(command, names=self._root_names()) is None:
                self._write(f"Unknown product: {command}")
                self._write(self.screen())
                return False
        return self._handle_in_context(parts)

    def _handle_in_context(self, parts: list[str]) -> bool:
        product = self.product
        if not self.context_path or product is None:
            return False
        self._execute_raw(parts)
        return False

    def _open_home(self) -> None:
        self._set_context_path(())

    def _set_context_path(self, path: tuple[str, ...]) -> None:
        self.context_path = path
        self.context.set_product("/".join(path) if path else "home")

    def _execute_raw(self, parts: list[str]) -> None:
        product = self.product
        if not self.context_path or product is None:
            return
        exit_code, output = self.command_executor([*self.context_path, *parts])
        if output:
            self.stdout.write(output)
            if not output.endswith("\n"):
                self.stdout.write("\n")
        if exit_code:
            self._write(f"Command exited with status {exit_code}")

    def _root_views(self) -> tuple[CommandView, ...]:
        return tuple(
            self._view((name,))
            for name in self._root_names()
        )

    def _root_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in child_names(self.command_root, ())
            if name not in _ROOT_COMMANDS_EXCLUDED_FROM_PRODUCTS
        )

    def _current_view(self) -> CommandView | None:
        if not self.context_path:
            return None
        return self._view(self.context_path)

    def _child_views(self, path: tuple[str, ...]) -> tuple[CommandView, ...]:
        views = [self._view((*path, name)) for name in child_names(self.command_root, path)]
        return tuple(view for view in views if view is not None)

    def _view(self, path: tuple[str, ...]) -> CommandView | None:
        command = command_at(self.command_root, path)
        if command is None:
            return None
        return CommandView(name=path[-1], command=command, argv_prefix=path)

    def _write(self, text: str) -> None:
        self.stdout.write(text + "\n")
        self.stdout.flush()


def _missing_executor(argv: list[str]) -> tuple[int, str]:
    return 2, f"no command executor configured for: {' '.join(argv)}"


def _default_command_root() -> CommandInfo:
    from kairospy.surface.cli import app

    return root_command(app)


def product_for_token(token: str) -> CommandView | None:
    root = _default_command_root()
    name = resolve_token(token, names=child_names(root, ()))
    if name is None:
        return None
    command = command_at(root, (name,))
    if command is None:
        return None
    return CommandView(name=name, command=command, argv_prefix=(name,))


def _label(path: tuple[str, ...]) -> str:
    return " ".join(part.replace("-", " ").title() for part in path)


_ROOT_COMMANDS_EXCLUDED_FROM_PRODUCTS = {"shell", "app", "tui"}


__all__ = [
    "AppSession",
    "CommandView",
    "product_for_token",
]
