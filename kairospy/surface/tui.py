from __future__ import annotations

from io import StringIO
import shlex
from typing import Any
from typing import TextIO

from kairospy.surface.app import AppSession, CommandExecutor, PRODUCTS, ProductMaturity, product_for_token
from kairospy.surface.render_text import render_raw_bridge_commands, render_reference_commands
from kairospy.surface.state import SurfaceContext, SurfaceSnapshot


class RichTui:
    """Experimental Rich renderer for the App Core.

    This is intentionally a preview, not the mature TUI. A mature TUI needs
    focusable views, keyboard navigation, real-time refresh, and product-specific
    layout contracts instead of command-prompt interaction inside panels.
    """

    def __init__(
        self,
        *,
        console: Any | None = None,
        context: SurfaceContext | None = None,
        command_executor: CommandExecutor | None = None,
    ) -> None:
        from rich.console import Console

        self.console = console or Console()
        self.output = StringIO()
        self.session = AppSession(
            stdout=self.output,
            context=context,
            command_executor=command_executor,
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
        self.message = "" if _screen_only_command(parts, current_product) else output
        return should_exit

    def render(self):
        from rich.console import Group
        from rich.panel import Panel

        snapshot = self.session.context.snapshot()
        sections = [
            _preview_notice(),
            _header(snapshot),
            _main_panel(self.session, snapshot),
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
    """Compatibility adapter for tests and non-Rich callers."""

    def __init__(
        self,
        *,
        stdout: TextIO | None = None,
        context: SurfaceContext | None = None,
        command_executor: CommandExecutor | None = None,
    ) -> None:
        self.session = AppSession(
            stdout=stdout,
            context=context,
            command_executor=command_executor,
        )

    def run(self) -> None:
        self.session.run()

    def handle(self, line: str) -> bool:
        return self.session.handle(line)

    def screen(self) -> str:
        return self.session.screen()

    def prompt(self) -> str:
        return self.session.prompt()


def _header(snapshot: SurfaceSnapshot) -> Panel:
    from rich.panel import Panel
    from rich.text import Text

    text = Text()
    text.append("Kairos", style="bold cyan")
    text.append(f"  {snapshot.project_name}")
    text.append(f"\nview {snapshot.current_product}", style="white")
    text.append(f" | runs {len(snapshot.active_runs)} active / {len(snapshot.runs)} total")
    text.append(f" | refresh {snapshot.refresh_interval_seconds:g}s")
    text.append(f"\n{snapshot.root}", style="dim")
    return Panel(text, border_style="cyan")


def _preview_notice() -> Panel:
    from rich.panel import Panel

    return Panel(
        "Experimental TUI preview. Use `kairospy app` as the stable interactive surface.",
        title="TUI Preview",
        border_style="yellow",
    )


def _main_panel(session: AppSession, snapshot: SurfaceSnapshot) -> Panel:
    from rich.console import Group
    from rich.panel import Panel
    from rich.text import Text

    product = session.product
    if product is None:
        return Panel(_products_table(), title="Products", border_style="blue")
    if product.name == "run":
        session.run_session.run_choices = _run_choices(snapshot)
        return Panel(
            Group(
                _runs_table(snapshot),
                _selected_run(session),
                Text(session.run_session.menu(), style="dim"),
            ),
            title="Run Workspace",
            border_style="green",
        )
    if product.name == "reference":
        return Panel(
            Group(
                _product_summary(product.label, str(product.maturity), product.description),
                _reference_table(snapshot),
                Text(render_reference_commands(), style="dim"),
            ),
            title="Reference Panel",
            border_style="magenta",
        )
    return Panel(
        Group(
            _product_summary(product.label, str(product.maturity), product.description),
            Text(render_raw_bridge_commands(product), style="dim"),
        ),
        title=product.label,
        border_style="white",
    )


def _products_table() -> Table:
    from rich.table import Table
    from rich.text import Text

    table = Table(expand=True)
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("Product", style="bold")
    table.add_column("Maturity")
    table.add_column("Description")
    for index, product in enumerate(PRODUCTS, start=1):
        style = "green" if product.maturity is ProductMaturity.WORKSPACE else "magenta" if product.maturity is ProductMaturity.PANEL else "dim"
        table.add_row(str(index), product.name, Text(str(product.maturity), style=style), product.description)
    return table


def _runs_table(snapshot: SurfaceSnapshot) -> Table:
    from rich.table import Table
    from rich.text import Text

    table = Table(expand=True)
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("Mode")
    table.add_column("Run")
    table.add_column("Status")
    table.add_column("Age", justify="right")
    table.add_column("Detail")
    for index, run in enumerate(snapshot.runs[:10], start=1):
        age = "-" if run.heartbeat_age_seconds is None else f"{run.heartbeat_age_seconds:.1f}s"
        status_style = "green" if run.active else "red" if run.status == "failed" else "dim"
        detail = run.strategy or ("config" if run.config_file else "log" if run.log_file else "")
        table.add_row(str(index), run.mode, run.run_id, Text(run.status, style=status_style), age, detail)
    if not snapshot.runs:
        table.add_row("-", "-", "no recorded runs", "-", "-", "")
    return table


def _selected_run(session: AppSession) -> Panel:
    from rich.panel import Panel

    run_id = session.run_session.run_id
    if run_id is None:
        text = "selected: none"
    else:
        text = f"selected: {session.run_session.mode.value}:{run_id}"
    return Panel(text, border_style="green")


def _reference_table(snapshot: SurfaceSnapshot) -> Table:
    from rich.table import Table

    from kairospy.surface.app import _reference_summary

    summary = _reference_summary(snapshot)
    table = Table(expand=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value")
    for key in ("root", "database", "entities", "assets", "instruments", "listings", "markets", "events", "error"):
        if key in summary:
            table.add_row(key, str(summary[key]))
    return table


def _product_summary(label: str, maturity: str, description: str) -> Panel:
    from rich.panel import Panel
    from rich.text import Text

    text = Text()
    text.append(label, style="bold")
    text.append(f"\nmaturity: {maturity}")
    text.append(f"\n{description}", style="dim")
    return Panel(text)


def _run_choices(snapshot: SurfaceSnapshot) -> list[dict[str, object]]:
    return [
        {
            "index": index,
            "mode": run.mode,
            "run_id": run.run_id,
            "status": run.status,
            "phase": run.phase,
            "heartbeat_age_seconds": run.heartbeat_age_seconds,
            "strategy": run.strategy,
            "config": run.config_file,
            "log_file": run.log_file,
        }
        for index, run in enumerate(snapshot.runs, start=1)
    ]


def _footer(prompt: str) -> Align:
    from rich.align import Align
    from rich.text import Text

    return Align.left(Text(f"{prompt}  help | refresh | back | quit", style="bold"))


def _screen_only_command(parts: list[str], current_product) -> bool:
    if not parts:
        return True
    command = parts[0]
    if command in {"help", "?", "menu", "refresh", "r", "home", "products", "back", "b", ".."}:
        return True
    if current_product is None and product_for_token(command) is not None:
        return True
    return False


__all__ = ["RichTui", "TextTui"]
