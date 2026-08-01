from __future__ import annotations

from io import StringIO
import shlex
from typing import Any, TextIO

from kairospy.surface.interactive.navigation import resolve_token
from kairospy.surface.interactive.session import SurfaceContext, SurfaceSnapshot
from kairospy.surface.interactive.shell import AppSession, CommandExecutor, CommandView


class RichTui:
    """Rich renderer for the shared AppSession command context."""

    def __init__(
        self,
        *,
        console: Any | None = None,
        context: SurfaceContext | None = None,
        command_executor: CommandExecutor | None = None,
        surface_name: str = "tui",
    ) -> None:
        from rich.console import Console

        self.console = console or Console()
        self.output = StringIO()
        self.session = AppSession(
            stdout=self.output,
            context=context,
            command_executor=command_executor,
            surface_name=surface_name,
        )
        self.message = ""

    def run(self) -> None:
        while True:
            self.console.clear()
            self.console.print(self.render())
            try:
                from rich.prompt import Prompt

                line = Prompt.ask(self.session.prompt().strip(), console=self.console, default="")
            except (EOFError, KeyboardInterrupt):
                self.console.print()
                return
            if self.handle(line):
                return

    def handle(self, line: str) -> bool:
        parts = shlex.split(line.strip())
        current_product = self.session.product
        should_exit = self.session.handle(line)
        output = self._drain_output()
        self.message = "" if _screen_only_command(self.session, parts, current_product) else output
        return should_exit

    def render(self):
        from rich.console import Group
        from rich.panel import Panel

        snapshot = self.session.context.snapshot()
        sections = [
            _header(snapshot),
            _main_panel(self.session),
        ]
        if self.message.strip():
            sections.append(Panel(self.message.rstrip(), title="Output", border_style="yellow"))
        sections.append(_footer(self.session.prompt()))
        return Group(*sections)

    def prompt(self) -> str:
        return self.session.prompt()

    def _drain_output(self) -> str:
        value = self.output.getvalue()
        self.output.seek(0)
        self.output.truncate(0)
        return value


class TextTui:
    """Plain text adapter around AppSession."""

    def __init__(
        self,
        *,
        stdout: TextIO | None = None,
        context: SurfaceContext | None = None,
        command_executor: CommandExecutor | None = None,
        streaming_command_executor: Any | None = None,
        surface_name: str = "app",
    ) -> None:
        self.session = AppSession(
            stdout=stdout,
            context=context,
            command_executor=command_executor,
            streaming_command_executor=streaming_command_executor,
            surface_name=surface_name,
        )

    def run(self) -> None:
        self.session.run()

    def handle(self, line: str) -> bool:
        return self.session.handle(line)

    def screen(self) -> str:
        return self.session.screen()

    def prompt(self) -> str:
        return self.session.prompt()


def _header(snapshot: SurfaceSnapshot):
    from rich.panel import Panel
    from rich.text import Text

    text = Text()
    text.append("Kairos", style="bold cyan")
    text.append(f"  {snapshot.project_name}")
    text.append(f"\nview {snapshot.current_product}", style="white")
    text.append(f" | launches {len(snapshot.active_launches)} active / {len(snapshot.launches)} total")
    text.append(f" | refresh {snapshot.refresh_interval_seconds:g}s")
    text.append(f"\n{snapshot.root}", style="dim")
    return Panel(text, border_style="cyan")


def _main_panel(session: AppSession):
    from rich.console import Group
    from rich.panel import Panel
    from rich.text import Text

    command = session._current_view()
    if command is None:
        return Panel(_commands_table(session._root_views()), title="Products", border_style="blue")
    children = session._child_views(session.context_path)
    return Panel(
        Group(
            _command_detail(command),
            _commands_table(children) if children else Text("Leaf command context", style="dim"),
        ),
        title=command.label,
        border_style="blue",
    )


def _commands_table(commands: tuple[CommandView | None, ...]):
    from rich.table import Table

    table = Table(expand=True)
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("Command", style="bold")
    table.add_column("Description")
    visible_commands = tuple(command for command in commands if command is not None)
    for index, command in enumerate(visible_commands, start=1):
        table.add_row(str(index), command.name, command.description)
    if not visible_commands:
        table.add_row("-", "-", "no subcommands")
    return table


def _command_detail(command: CommandView):
    from rich.panel import Panel
    from rich.text import Text

    text = Text()
    text.append("/".join(command.argv_prefix), style="bold")
    text.append(f"\n{' '.join(command.argv_prefix)}")
    if command.description:
        text.append(f"\n{command.description}", style="dim")
    return Panel(text, border_style="white")


def _footer(prompt: str):
    from rich.align import Align
    from rich.text import Text

    return Align.left(Text(f"{prompt}  help | refresh | back | home | quit", style="bold"))


def _screen_only_command(session: AppSession, parts: list[str], current_product: CommandView | None) -> bool:
    if not parts:
        return True
    command = parts[0]
    if command in {"help", "?", "menu", "refresh", "r", "home", "products", "back", "b", ".."}:
        return True
    if current_product is None and len(parts) == 1 and resolve_token(command, names=session._root_names()) is not None:
        return True
    return False


__all__ = ["RichTui", "TextTui"]
