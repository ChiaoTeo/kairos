from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import shlex
import sys
from typing import Callable, Mapping, TextIO

from kairospy.service.domains.reference import ReferenceStore
from kairospy.surface.products.run import RunShellSession
from kairospy.surface.render_text import (
    render_home_screen,
    render_raw_product_screen,
    render_reference_panel_screen,
    render_run_workspace,
)
from kairospy.surface.state import SurfaceContext, SurfaceSnapshot


CommandExecutor = Callable[[list[str]], tuple[int, str]]


class ProductMaturity(StrEnum):
    RAW = "raw"
    PANEL = "panel"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class ProductDescriptor:
    name: str
    label: str
    description: str
    maturity: ProductMaturity

    @property
    def interactive(self) -> bool:
        return self.maturity is ProductMaturity.WORKSPACE


PRODUCTS: tuple[ProductDescriptor, ...] = (
    ProductDescriptor("run", "Run", "runs, daemons, account views, PnL, positions", ProductMaturity.WORKSPACE),
    ProductDescriptor("backtest", "Backtest", "historical strategy runs and artifacts", ProductMaturity.RAW),
    ProductDescriptor("data", "Data", "historical datasets and lake operations", ProductMaturity.RAW),
    ProductDescriptor("streams", "Streams", "live market data streams", ProductMaturity.RAW),
    ProductDescriptor("reference", "Reference", "instruments, markets, lifecycle catalogs", ProductMaturity.PANEL),
    ProductDescriptor("broker", "Broker", "broker and account inspection", ProductMaturity.RAW),
    ProductDescriptor("strategy", "Strategy", "strategy utilities", ProductMaturity.RAW),
    ProductDescriptor("integrations", "Integrations", "provider and exchange checks", ProductMaturity.RAW),
)


class AppSession:
    def __init__(
        self,
        *,
        stdout: TextIO | None = None,
        context: SurfaceContext | None = None,
        command_executor: CommandExecutor | None = None,
    ) -> None:
        self.stdout = stdout or sys.stdout
        self.context = context or SurfaceContext(product="home")
        self.command_executor = command_executor or _missing_executor
        self.product: ProductDescriptor | None = None
        self.run_session = RunShellSession(stdout=self.stdout)

    def banner(self) -> str:
        return "Kairos app. Mature products open as workspaces; unfinished products use the raw command bridge."

    def prompt(self) -> str:
        if self.product is None:
            return "kairos/app> "
        if self.product.name == "run" and self.run_session.run_id is not None:
            return f"kairos/app/run[{self.run_session.mode.value}:{self.run_session.run_id}]> "
        return f"kairos/app/{self.product.name}> "

    def screen(self) -> str:
        snapshot = self.context.snapshot()
        if self.product is None:
            return render_home_screen(snapshot, PRODUCTS)
        if self.product.name == "run":
            self.run_session.run_choices = _run_choices(snapshot)
            return render_run_workspace(snapshot, self.run_session.menu())
        if self.product.name == "reference":
            return render_reference_panel_screen(snapshot, self.product, _reference_summary(snapshot))
        return render_raw_product_screen(snapshot, self.product)

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
        if self.product is not None and self.product.maturity is ProductMaturity.RAW and command == "help":
            self._execute_raw(["--help"])
            return False
        if command in {"help", "?", "menu"}:
            self._write(self.screen())
            return False
        if command in {"home", "products"}:
            self._open_home()
            self._write(self.screen())
            return False
        if command in {"back", "b", ".."}:
            if self.product is None:
                self._write(self.screen())
                return False
            self._open_home()
            self._write(self.screen())
            return False
        if command in {"refresh", "r"}:
            self.context.refresh()
            self._write(self.screen())
            return False
        if self.product is None:
            product = product_for_token(command)
            if product is None:
                self._write(f"Unknown product: {command}")
                self._write(self.screen())
                return False
            self._open_product(product)
            self._write(self.screen())
            return False
        if self.product.name == "run":
            return self.run_session.handle(line)
        if self.product.name == "reference":
            self._execute_raw(parts)
            return False
        self._execute_raw(parts)
        return False

    def _open_home(self) -> None:
        self.product = None
        self.context.set_product("home")

    def _open_product(self, product: ProductDescriptor) -> None:
        self.product = product
        self.context.set_product(product.name)

    def _execute_raw(self, parts: list[str]) -> None:
        if self.product is None:
            return
        exit_code, output = self.command_executor([self.product.name, *parts])
        if output:
            self.stdout.write(output)
            if not output.endswith("\n"):
                self.stdout.write("\n")
        if exit_code:
            self._write(f"Command exited with status {exit_code}")

    def _write(self, text: str) -> None:
        self.stdout.write(text + "\n")
        self.stdout.flush()


def product_for_token(token: str) -> ProductDescriptor | None:
    if token.isdigit():
        index = int(token)
        if 1 <= index <= len(PRODUCTS):
            return PRODUCTS[index - 1]
        return None
    normalized = token.strip().lower()
    for product in PRODUCTS:
        if product.name == normalized:
            return product
    return None


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


def _reference_summary(snapshot: SurfaceSnapshot) -> Mapping[str, object]:
    store = ReferenceStore(snapshot.reference_root)
    try:
        catalog = store.load_catalog()
        events = store.load_events()
    except Exception as error:
        return {
            "root": str(snapshot.reference_root),
            "database": str(store.database_path),
            "error": f"{type(error).__name__}: {error}",
        }
    return {
        "root": str(snapshot.reference_root),
        "database": str(store.database_path),
        "entities": len(catalog.entities()),
        "assets": len(catalog.assets()),
        "instruments": len(catalog.instruments()),
        "listings": len(catalog.listings()),
        "markets": len(catalog.markets()),
        "events": len(events),
    }


def _missing_executor(argv: list[str]) -> tuple[int, str]:
    return 2, f"no command executor configured for: {' '.join(argv)}"


__all__ = [
    "PRODUCTS",
    "AppSession",
    "ProductDescriptor",
    "ProductMaturity",
    "product_for_token",
]
