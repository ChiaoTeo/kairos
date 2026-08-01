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
from kairospy.surface.interactive.line_reader import LineReader, clear_history, default_line_reader, load_history_entries
from kairospy.surface.interactive.session import SurfaceContext
from kairospy.surface.rendering.text import (
    render_context_screen,
    render_home_screen,
)
from kairospy.surface.interactive.account import AccountCreateWizard, account_create_direct_argv, is_account_create_argv


CommandExecutor = Callable[[list[str]], tuple[int, str]]
StreamingCommandExecutor = Callable[[list[str], TextIO], int]


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
        streaming_command_executor: StreamingCommandExecutor | None = None,
        command_root: CommandInfo | None = None,
        surface_name: str = "app",
        line_reader: LineReader | None = None,
    ) -> None:
        self.stdout = stdout or sys.stdout
        self.context = context or SurfaceContext(product="home")
        self.command_executor = command_executor or _missing_executor
        self.streaming_command_executor = streaming_command_executor
        self.command_root = command_root or _default_command_root()
        self.surface_name = surface_name
        self.line_reader = line_reader
        self.context_path: tuple[str, ...] = ()
        self.account_create_wizard: AccountCreateWizard | None = None
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
        return f"Kairos {self.surface_name}. Navigate Typer command contexts; commands keep the same argv semantics as the plain CLI."

    def prompt(self) -> str:
        if self.account_create_wizard is not None:
            return f"kairos/{self.surface_name}/account/create> "
        if not self.context_path:
            return f"kairos/{self.surface_name}> "
        return f"kairos/{self.surface_name}/{'/'.join(self.context_path)}> "

    def screen(self) -> str:
        snapshot = self.context.snapshot()
        node = self._current_view()
        product = self.product
        if node is None or product is None:
            return render_home_screen(snapshot, self._root_views(), surface_name=self.surface_name)
        return render_context_screen(snapshot, node, self._child_views(self.context_path))

    def run(self) -> None:
        self._write(self.banner())
        self._write_screen()
        while True:
            try:
                line = self._line_reader().read(self.prompt())
            except EOFError:
                self._write("")
                return
            except KeyboardInterrupt:
                self._write("\nUse `quit` to exit.")
                continue
            if self.handle(line):
                return

    def _line_reader(self) -> LineReader:
        if self.line_reader is None:
            self.line_reader = default_line_reader()
        return self.line_reader

    def handle(self, line: str) -> bool:
        parts = shlex.split(line.strip())
        if self.account_create_wizard is not None:
            return self._handle_account_create_wizard(line)
        if not parts:
            self._write_screen()
            return False
        command = parts[0]
        if command in {"quit", "exit", "q"}:
            return True
        if command in {"help", "?", "menu"}:
            self._write_screen()
            return False
        if command == "history" and not self._context_has_command(command):
            self._handle_history(parts[1:])
            return False
        if command in {"home", "products"}:
            self._open_home()
            self._write_screen()
            return False
        if command in {"back", "b", ".."}:
            if not self.context_path:
                self._write_screen()
                return False
            self._set_context_path(self.context_path[:-1])
            self._write_screen()
            return False
        if command in {"refresh", "r"}:
            self.context.refresh()
            self._write_screen()
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
            self._write_screen()
            return False
        if not self.context_path:
            if resolve_token(command, names=self._root_names()) is None:
                self._write(f"Unknown product: {command}")
                self._write_screen()
                return False
        return self._handle_in_context(parts)

    def _handle_in_context(self, parts: list[str]) -> bool:
        product = self.product
        if not self.context_path or product is None:
            return False
        resolved_parts = self._resolve_context_command(parts)
        argv = [*self.context_path, *resolved_parts]
        direct_argv = account_create_direct_argv(argv)
        if direct_argv is not None:
            self._execute_raw(direct_argv[len(self.context_path):])
            return False
        if is_account_create_argv(argv):
            self._enter_account_create_wizard()
            return False
        self._execute_raw(resolved_parts)
        return False

    def _handle_history(self, parts: list[str]) -> None:
        if parts == ["clear"]:
            reader_clear = getattr(self.line_reader, "clear_history", None)
            if callable(reader_clear):
                reader_clear()
            else:
                clear_history()
            self._write("Shell history cleared.")
            return
        if len(parts) > 1:
            self._write("usage: history [limit|clear]")
            return
        limit = 20
        if parts:
            try:
                limit = int(parts[0])
            except ValueError:
                self._write("usage: history [limit|clear]")
                return
            if limit <= 0:
                self._write("usage: history [limit|clear]")
                return
        entries = load_history_entries(limit=limit)
        if not entries:
            self._write("No shell history.")
            return
        start = max(1, len(load_history_entries()) - len(entries) + 1)
        width = len(str(start + len(entries) - 1))
        for index, entry in enumerate(entries, start=start):
            self._write(f"{index:>{width}}  {entry}")

    def _context_has_command(self, command: str) -> bool:
        if not self.context_path:
            return False
        return resolve_token(command, names=child_names(self.command_root, self.context_path)) is not None

    def _enter_account_create_wizard(self) -> None:
        self.account_create_wizard = AccountCreateWizard()
        self._set_context_path(("account",))
        self._write(self.account_create_wizard.start())

    def _handle_account_create_wizard(self, line: str) -> bool:
        assert self.account_create_wizard is not None
        message = self.account_create_wizard.handle(line)
        self._write(message)
        if self.account_create_wizard.complete:
            self.account_create_wizard = None
            self.context.refresh()
            self._write_screen()
        return False

    def _resolve_context_command(self, parts: list[str]) -> list[str]:
        if not parts:
            return parts
        child_name = resolve_token(parts[0], names=child_names(self.command_root, self.context_path))
        if child_name is None:
            return parts
        return [child_name, *parts[1:]]

    def _open_home(self) -> None:
        self._set_context_path(())

    def _set_context_path(self, path: tuple[str, ...]) -> None:
        self.context_path = path
        self.context.set_product("/".join(path) if path else "home")

    def _execute_raw(self, parts: list[str]) -> None:
        product = self.product
        if not self.context_path or product is None:
            return
        argv = [*self.context_path, *parts]
        fullscreen = _is_fullscreen_shell_command(tuple(argv), self.stdout)
        if not fullscreen:
            self._write(_command_output_header(tuple(argv)))
        try:
            if self.streaming_command_executor is not None:
                exit_code = self.streaming_command_executor(argv, self.stdout)
            else:
                exit_code, output = self.command_executor(argv)
                if output:
                    self.stdout.write(output)
                    if not output.endswith("\n"):
                        self.stdout.write("\n")
        except Exception as error:
            exit_code = 1
            self._write(f"error: {error}")
        if exit_code:
            self._write(f"Command exited with status {exit_code}")
        if not fullscreen:
            self._write(_COMMAND_OUTPUT_FOOTER)

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

    def _write_screen(self) -> None:
        self._write(_SCREEN_SEPARATOR)
        self._write(self.screen())


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


def _command_output_header(argv: tuple[str, ...]) -> str:
    return f"--- kairospy {' '.join(argv)}"


def _is_fullscreen_shell_command(argv: tuple[str, ...], stdout: TextIO) -> bool:
    if not argv or argv[-1] != "browse":
        return False
    try:
        return bool(stdout.isatty())
    except (AttributeError, OSError):
        return False


_COMMAND_OUTPUT_FOOTER = "---"
_SCREEN_SEPARATOR = "-" * 72
_ROOT_COMMANDS_EXCLUDED_FROM_PRODUCTS = {"shell", "tui"}


__all__ = [
    "AppSession",
    "CommandView",
    "product_for_token",
    "StreamingCommandExecutor",
]
