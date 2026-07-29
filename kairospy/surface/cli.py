from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Sequence, TextIO

import typer
from typer.testing import CliRunner

from kairospy.application.system.workspace import OperationJournal
from kairospy.surface.products import (
    account_app,
    backtest_app,
    config_app,
    data_app,
    integrations_app,
    order_app,
    reference_app,
    run_app,
    strategy_app,
    streams_app,
)
from kairospy.surface.products.run import RunShellSession
from kairospy.surface.state import SurfaceContext, render_run_strip, render_surface_overview


app = typer.Typer(no_args_is_help=True, help="KairosPy strategy runtime toolkit")
app.add_typer(account_app, name="account")
app.add_typer(backtest_app, name="backtest")
app.add_typer(config_app, name="config")
app.add_typer(data_app, name="data")
app.add_typer(streams_app, name="streams")
app.add_typer(integrations_app, name="integrations")
app.add_typer(order_app, name="order")
app.add_typer(reference_app, name="reference")
app.add_typer(strategy_app, name="strategy")
app.add_typer(run_app, name="run")


@app.command("init")
def init(
    project_name: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force"),
) -> None:
    project_root = Path(project_name).expanduser()
    kairos_root = project_root / ".kairos"
    config_path = kairos_root / "kairos.toml"
    if config_path.exists() and not force:
        raise typer.BadParameter(f"Kairos project already exists: {config_path}")
    kairos_root.mkdir(parents=True, exist_ok=True)
    for directory in ("accounts", "state", "runs", "data", "reference", "orders/journals"):
        (kairos_root / directory).mkdir(parents=True, exist_ok=True)
    config_path.write_text(_workspace_manifest(project_root.name), encoding="utf-8")
    OperationJournal(kairos_root / "state" / "operations.jsonl").append(
        "project.init",
        target={"project": project_root.name},
        payload={"root": project_root, "manifest": config_path},
    )
    typer.echo(str(project_root))


@app.command("shell")
def shell(
    command: list[str] | None = typer.Option(None, "--command"),
) -> None:
    _shell(command)


@app.command("app")
def app_command(
    command: list[str] | None = typer.Option(None, "--command"),
) -> None:
    _app(command)


@app.command("tui", hidden=True)
def tui() -> None:
    from kairospy.surface.tui import RichTui

    RichTui(command_executor=_execute_product_command).run()


def _app(command: list[str] | None = None) -> None:
    from kairospy.surface.tui import TextTui

    session = TextTui(command_executor=_execute_product_command)
    if command:
        for line in command:
            if session.handle(line):
                return
        return
    session.run()


def _shell(command: list[str] | None = None) -> None:
    session = ProductShellSession()
    if command:
        for line in command:
            if session.handle(line):
                return
        return
    typer.echo(session.banner())
    typer.echo(session.menu())
    while True:
        try:
            line = input(session.prompt())
        except EOFError:
            typer.echo("")
            return
        except KeyboardInterrupt:
            typer.echo("\nUse `quit` to exit.")
            continue
        if session.handle(line):
            return


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        if len(sys.argv) == 1 and sys.stdin.isatty() and sys.stdout.isatty():
            _shell()
            return 0
        app()
        return 0
    return execute_argv(argv, sys.stdout)


def execute_argv(argv: Sequence[str], stdout: TextIO) -> int:
    result = CliRunner().invoke(app, list(argv), catch_exceptions=False)
    stdout.write(result.output)
    return int(result.exit_code)


def _execute_product_command(argv: list[str]) -> tuple[int, str]:
    result = CliRunner().invoke(app, argv, catch_exceptions=False)
    return int(result.exit_code), result.output


def _workspace_manifest(project_name: str) -> str:
    return "\n".join(
        [
            "schema_version = 1",
            "",
            "[project]",
            f'name = "{project_name}"',
            'timezone = "UTC"',
            "",
            "[data]",
            'storage_format = "parquet"',
            "",
            "[cli]",
            'format = "text"',
            "run_control = true",
            "",
        ]
    )


class ProductShellSession:
    def __init__(self, *, stdout: TextIO | None = None) -> None:
        self.stdout = stdout or sys.stdout
        self.context = SurfaceContext()
        self.run_session = RunShellSession(stdout=self.stdout)

    @property
    def product(self) -> str:
        return self.context.product

    @product.setter
    def product(self, value: str) -> None:
        self.context.set_product(value)

    def banner(self) -> str:
        return "Kairos application shell. Choose a product, then work inside its live context."

    def prompt(self) -> str:
        if self.product == "top":
            return "kairospy> "
        return f"kairospy/{self.product}> "

    def menu(self) -> str:
        snapshot = self.context.snapshot()
        overview = render_surface_overview(snapshot)
        runs = render_run_strip(snapshot)
        if self.product == "top":
            return "\n\n".join([
                overview,
                runs,
                "\n".join([
                    "Products",
                    "1  run          manage run specs, daemons, status, and artifacts",
                    "2  account      configured accounts and account inspection",
                    "3  order        order placement, cancellation, and journals",
                    "4  config       workspace configuration and diagnostics",
                    "5  backtest     historical strategy runs and artifacts",
                    "6  data         historical datasets",
                    "7  streams      live market data streams",
                    "8  reference    instruments, markets, lifecycle catalogs",
                    "9  strategy     strategy utilities",
                    "10 integrations provider and exchange checks",
                    "r  refresh",
                    "q  quit",
                ]),
            ])
        if self.product == "run":
            self.run_session.run_choices = _run_choices(snapshot)
            return "\n\n".join([overview, runs, self.run_session.menu()])
        return "\n\n".join([
            overview,
            runs,
            "\n".join([
                f"{self.product} product",
                "Type a command for this product without the product prefix.",
                f"Example: `--help` runs `kairospy {self.product} --help`.",
                "r  refresh",
                "b  back",
            ]),
        ])

    def handle(self, line: str) -> bool:
        parts = shlex.split(line.strip())
        if not parts:
            if self.product == "run":
                return self.run_session.handle("list")
            self._write(self.menu())
            return False
        command = parts[0]
        if command in {"quit", "exit", "q"}:
            return True
        if command in {"help", "?", "menu"}:
            self._write(self.menu())
            return False
        if command in {"status", "refresh", "r"}:
            snapshot = self.context.refresh()
            if self.product == "run":
                self.run_session.run_choices = _run_choices(snapshot)
            self._write("\n\n".join([render_surface_overview(snapshot), render_run_strip(snapshot)]))
            return False
        if command in {"back", "b", ".."}:
            self.product = "top"
            self._write(self.menu())
            return False
        if self.product == "top":
            return self._handle_top(parts)
        if self.product == "run":
            return self.run_session.handle(line)
        self._run_product_command(parts)
        return False

    def _handle_top(self, parts: list[str]) -> bool:
        command = parts[0]
        product = {
            "1": "run",
            "run": "run",
            "2": "account",
            "account": "account",
            "3": "order",
            "order": "order",
            "4": "config",
            "config": "config",
            "5": "backtest",
            "backtest": "backtest",
            "6": "data",
            "data": "data",
            "7": "streams",
            "streams": "streams",
            "8": "reference",
            "reference": "reference",
            "9": "strategy",
            "strategy": "strategy",
            "10": "integrations",
            "integrations": "integrations",
        }.get(command)
        if product is None:
            self._write(f"Unknown product: {command}")
            self._write(self.menu())
            return False
        self.product = product
        if len(parts) > 1:
            if self.product == "run":
                self.run_session.handle(" ".join(shlex.quote(part) for part in parts[1:]))
            else:
                self._run_product_command(parts[1:])
            return False
        self._write(self.menu())
        return False

    def _run_product_command(self, parts: list[str]) -> None:
        argv = [self.product, *parts]
        result = CliRunner().invoke(app, argv, catch_exceptions=False)
        self.stdout.write(result.output)
        if result.exit_code:
            self._write(f"Command exited with status {result.exit_code}")

    def _write(self, text: str) -> None:
        self.stdout.write(text + "\n")
        self.stdout.flush()


def _run_choices(snapshot) -> list[dict[str, object]]:
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


__all__ = [
    "app",
    "execute_argv",
    "main",
    "ProductShellSession",
]
