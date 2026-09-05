#!/usr/bin/env python3
"""Validate stable CLI ownership and package-boundary invariants.

Business command inventories belong to their owner CLIs. This check guards
package placement and invocation shape without duplicating every command
variant or private function name from those implementations.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLI_ROOT = ROOT / "kairospy" / "surface" / "cli"
CLI_COMMANDS = CLI_ROOT / "commands"


@dataclass(frozen=True, slots=True)
class CliBoundary:
    product: str
    python_command: str
    crate: str
    system_component: bool
    launch_component: bool = True

    @property
    def module(self) -> Path:
        return ROOT / "crates" / "modules" / self.crate

    @property
    def rust_cli(self) -> Path:
        return self.module / "src" / "bin" / f"kairos-{self.crate}-cli.rs"

    @property
    def cli_application_name(self) -> str:
        return f"Cli{self.product}Application"

    @property
    def connected_application_name(self) -> str:
        return f"Connected{self.product}Application"


BOUNDARIES = (
    CliBoundary("Account", "account", "account", False),
    CliBoundary("Market", "market", "market", True),
    CliBoundary("Reference", "reference", "reference", True),
    CliBoundary("Execution", "order", "execution", False),
    CliBoundary("Risk", "risk", "risk", True),
    CliBoundary("Capital", "capital", "capital", True),
)

SUPPORT_COMMANDS = {
    "data",
    "integration",
    "notifications",
    "project",
    "research",
    "template",
}
FORBIDDEN_STANDALONE_IMPORTS = (
    "ComponentProcessApplication",
    "AccountSystemClient",
    "MarketSystemClient",
    "ReferenceSystemClient",
)
FORBIDDEN_ROOT_BUSINESS_IMPLEMENTATION = (
    "AccountAdminApplication",
    "CredentialApplication",
    "MarketDataApplication",
    "TradeLeaseApplication",
    "order_place",
    "order_cancel",
    "order_replace",
)
FORBIDDEN_RUST_MODE_NORMALIZATION = (
    "fn normalize(self)",
    "impl From<StandaloneCommand>",
    "impl From<ConnectedCommand>",
)
FORBIDDEN_ACCOUNT_BIN_ASSEMBLY = (
    "Conflux::new(",
    "AccountRpcService",
    "compose_binance_async_account_application",
    "compose_okx_async_account_application",
    "compose_ibkr_async_account_application",
)


def _source_text(path: Path) -> str:
    files = sorted(path.rglob("*.rs")) if path.is_dir() else [path]
    return "\n".join(file.read_text(encoding="utf-8") for file in files)


def _python_tree(path: Path) -> str:
    return "\n".join(
        file.read_text(encoding="utf-8") for file in sorted(path.glob("*.py"))
    )


def _cli_application_source(boundary: CliBoundary) -> Path:
    application = boundary.module / "src" / "application"
    candidates = (
        application / "cli.rs",
        application / "cli",
        boundary.module / "src" / "composition" / "cli.rs",
    )
    return next(
        (candidate for candidate in candidates if candidate.exists()), candidates[0]
    )


def _connected_application_source(boundary: CliBoundary) -> Path:
    application = boundary.module / "src" / "application"
    candidates = (application / "connected.rs", application / "cli" / "remote.rs")
    return next(
        (candidate for candidate in candidates if candidate.exists()), candidates[0]
    )


def _enum_body(text: str, name: str) -> str | None:
    match = re.search(rf"\benum\s+{re.escape(name)}\s*\{{", text)
    if match is None:
        return None
    start = match.end()
    depth = 1
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start:index]
    return None


def _top_level_variants(body: str) -> set[str]:
    variants: set[str] = set()
    depth = 0
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("//", "#[")):
            continue
        if depth == 0:
            match = re.match(r"([A-Z][A-Za-z0-9_]*)\s*(?:[({,]|$)", line)
            if match:
                variants.add(match.group(1))
        depth += line.count("{") + line.count("(")
        depth -= line.count("}") + line.count(")")
        depth = max(depth, 0)
    return variants


def _check_python_surface(failures: list[str]) -> None:
    expected = {boundary.python_command for boundary in BOUNDARIES} | SUPPORT_COMMANDS
    actual = {
        path.stem for path in CLI_COMMANDS.glob("*.py") if path.stem != "__init__"
    }
    for name in sorted(actual - expected):
        failures.append(f"undeclared top-level Python CLI command: {name}")
    for name in sorted(expected - actual):
        failures.append(f"declared top-level Python CLI command is missing: {name}")

    app_text = (CLI_ROOT / "app.py").read_text(encoding="utf-8")
    system_text = _python_tree(CLI_COMMANDS / "system")
    launch_text = _python_tree(CLI_COMMANDS / "launch")
    root_text = app_text + "\n" + system_text
    for token in FORBIDDEN_ROOT_BUSINESS_IMPLEMENTATION:
        if token in root_text:
            failures.append(f"root/System CLI owns private business behavior: {token}")

    for boundary in BOUNDARIES:
        command_path = CLI_COMMANDS / f"{boundary.python_command}.py"
        if not command_path.is_file():
            continue
        command_text = command_path.read_text(encoding="utf-8")
        for token in FORBIDDEN_STANDALONE_IMPORTS:
            if token in command_text:
                failures.append(
                    f"{command_path.relative_to(ROOT)} imports runtime adapter {token}"
                )
        if "standalone" not in command_text or "connected" not in command_text:
            failures.append(
                f"{command_path.relative_to(ROOT)} must reject or route explicit CLI modes"
            )
        if boundary.system_component:
            token = f"system_component_{boundary.crate}_app"
            if token not in system_text:
                failures.append(
                    f"System CLI is missing the {boundary.product} namespace"
                )
        if boundary.launch_component:
            token = f"instance_component_{boundary.crate}_app"
            if token not in launch_text:
                failures.append(
                    f"Launch CLI is missing the {boundary.product} namespace"
                )


def _check_rust_boundary(boundary: CliBoundary, failures: list[str]) -> None:
    cli_source = _cli_application_source(boundary)
    connected_source = _connected_application_source(boundary)
    for source, type_name, role in (
        (cli_source, boundary.cli_application_name, "standalone application"),
        (
            connected_source,
            boundary.connected_application_name,
            "connected application",
        ),
    ):
        if not source.exists():
            failures.append(
                f"{boundary.product} is missing its {role} source: "
                f"{source.relative_to(ROOT)}"
            )
        elif f"struct {type_name}" not in _source_text(source):
            failures.append(f"{source.relative_to(ROOT)} must define {type_name}")

    path = boundary.rust_cli
    if not path.is_file():
        failures.append(f"missing owner CLI: {path.relative_to(ROOT)}")
        return
    text = path.read_text(encoding="utf-8")
    command = _enum_body(text, "Command")
    if command is None:
        failures.append(f"{path.relative_to(ROOT)} has no enum Command")
    elif _top_level_variants(command) != {"Standalone", "Connected"}:
        failures.append(
            f"{path.relative_to(ROOT)} must expose only Standalone and Connected modes"
        )
    for name in ("StandaloneCommand", "ConnectedCommand"):
        body = _enum_body(text, name)
        if body is None or not _top_level_variants(body):
            failures.append(f"{path.relative_to(ROOT)} has no non-empty enum {name}")
    for token in FORBIDDEN_RUST_MODE_NORMALIZATION:
        if token in text:
            failures.append(f"{path.relative_to(ROOT)} hides CLI ownership via {token}")
    for type_name in (
        boundary.cli_application_name,
        boundary.connected_application_name,
    ):
        if f"struct {type_name}" in text or f"pub struct {type_name}" in text:
            failures.append(
                f"{path.relative_to(ROOT)} defines {type_name}; move it to its owner layer"
            )
    if boundary.product == "Account":
        for token in FORBIDDEN_ACCOUNT_BIN_ASSEMBLY:
            if token in text:
                failures.append(
                    f"{path.relative_to(ROOT)} performs Account runtime assembly: {token}"
                )


def main() -> int:
    failures: list[str] = []
    _check_python_surface(failures)
    for boundary in BOUNDARIES:
        _check_rust_boundary(boundary, failures)

    declared = {boundary.rust_cli for boundary in BOUNDARIES}
    discovered = set(ROOT.glob("crates/modules/*/src/bin/kairos-*-cli.rs"))
    for path in sorted(discovered - declared):
        failures.append(f"undeclared Rust business CLI: {path.relative_to(ROOT)}")

    if failures:
        print("CLI boundary checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("CLI boundary checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
