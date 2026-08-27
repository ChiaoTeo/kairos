#!/usr/bin/env python3
"""Enforce the completed kairospy subsystem architecture."""

from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "kairospy"
SUBSYSTEMS = {"system", "investment", "strategy", "research"}
ENTRYPOINT_ROOTS = (PACKAGE / "client", PACKAGE / "surface", PACKAGE / "bin")
RUST_BACKED_INVESTMENT_APPS = {
    "reference",
    "market",
    "account",
    "risk",
    "capital",
    "execution",
}
CURRENT_VIEW_OWNERS = ("account", "capital", "execution", "market", "risk")
OWNER_CONTRACT_PY_BINDINGS = (*CURRENT_VIEW_OWNERS, "reference")
OWNER_NATIVE_MODULES = {
    f"kairospy._native_{owner}_contract" for owner in CURRENT_VIEW_OWNERS
}


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_from(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = _module_name(path)
    if path.name != "__init__.py":
        package = package.rpartition(".")[0]
    parts = package.split(".") if package else []
    keep = max(0, len(parts) - node.level + 1)
    prefix = parts[:keep]
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def _imports(path: Path) -> tuple[tuple[int, str], ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.append((node.lineno, _resolve_from(path, node)))
    return tuple(result)


def _subsystem(path: Path) -> str | None:
    relative = path.relative_to(PACKAGE)
    return relative.parts[0] if relative.parts[0] in SUBSYSTEMS else None


def _app_identity(path: Path) -> tuple[str, str] | None:
    relative = path.relative_to(PACKAGE)
    parts = relative.parts
    if len(parts) >= 4 and parts[0] in SUBSYSTEMS and parts[1] == "apps":
        return parts[0], parts[2]
    return None


def _failure(failures: list[str], path: Path, line: int, message: str) -> None:
    failures.append(f"{path.relative_to(ROOT)}:{line}: {message}")


def _is_generated_protocol(module: str) -> bool:
    root = "kairospy.infrastructure.protocol.generated"
    return module == root or module.startswith(root + ".")


def main() -> int:
    failures: list[str] = []
    python_files = tuple(sorted(PACKAGE.rglob("*.py")))

    legacy_paths = (
        PACKAGE / "application",
        PACKAGE / "surface" / "client",
        PACKAGE / "infrastructure" / "transport" / "generated",
    )
    for path in legacy_paths:
        if path.exists():
            failures.append(f"legacy path remains: {path.relative_to(ROOT)}")
    for name in ("account", "capital", "execution", "market", "reference", "risk"):
        path = PACKAGE / "infrastructure" / "transport" / f"{name}.py"
        if path.exists():
            failures.append(f"legacy business transport remains: {path.relative_to(ROOT)}")

    generated = PACKAGE / "infrastructure" / "protocol" / "generated"
    if generated.exists():
        failures.append("removed Python generated protocol tree has returned")

    generic_indexed_view = PACKAGE / "infrastructure" / "transport" / "indexed_view.py"
    if generic_indexed_view.exists():
        failures.append(
            "generic Python indexed-view reader remains: "
            f"{generic_indexed_view.relative_to(ROOT)}"
        )

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    strategy_protocol = (PACKAGE / "strategy" / "api" / "protocol.py").read_text(
        encoding="utf-8"
    )
    if "reference: ReferenceApplication" not in strategy_protocol:
        failures.append(
            "StrategyContext.reference must expose ReferenceApplication, not a raw contract"
        )
    native_transport = (
        ROOT / "crates" / "platform" / "python-transport" / "src" / "lib.rs"
    ).read_text(encoding="utf-8")
    if "IndexedViewReader" in native_transport:
        failures.append("generic IndexedViewReader remains in kairos-python-transport")
    reference_client = (
        PACKAGE / "infrastructure" / "contracts" / "reference" / "client.py"
    )
    reference_source = reference_client.read_text(encoding="utf-8")
    for forbidden in ("reference_meta", "reference_markets_current", "SELECT ", "sqlite3"):
        if forbidden in reference_source:
            failures.append(
                "Reference Python contract contains SQLite implementation detail "
                f"{forbidden!r}: {reference_client.relative_to(ROOT)}"
            )
    for owner in OWNER_CONTRACT_PY_BINDINGS:
        binding = ROOT / "crates" / "modules" / owner / "contract" / "py"
        if not (binding / "Cargo.toml").is_file() or not (binding / "src" / "lib.rs").is_file():
            failures.append(f"owner contract PyO3 binding is missing: {binding.relative_to(ROOT)}")
        target = f'kairospy._native_{owner}_contract'
        if target not in pyproject:
            failures.append(f"owner contract extension is missing from pyproject.toml: {target}")

    forbidden_owner_files = {
        "account": ("control.py", "runtime.py", "source.py", "view_contract.py"),
        "capital": ("client.py", "source.py"),
        "execution": ("control.py", "current.py", "source.py"),
        "market": ("control.py", "source.py"),
        "risk": ("control.py", "source.py"),
    }
    for owner, names in forbidden_owner_files.items():
        facade = PACKAGE / "infrastructure" / "contracts" / owner
        for name in names:
            path = facade / name
            if path.exists():
                failures.append(
                    "owner Python implementation has returned: "
                    f"{path.relative_to(ROOT)}"
                )
        rust = (
            ROOT / "crates" / "modules" / owner / "contract" / "py" / "src" / "lib.rs"
        ).read_text(encoding="utf-8")
        source_module = f"kairospy.infrastructure.contracts.{owner}.source"
        if source_module in rust:
            failures.append(
                f"{owner} native companion delegates events back to Python source.py"
            )

    scoped_owner_roots = tuple(
        PACKAGE / "infrastructure" / "contracts" / owner
        for owner in CURRENT_VIEW_OWNERS
    ) + tuple(
        PACKAGE / "investment" / "apps" / owner
        for owner in CURRENT_VIEW_OWNERS
    )
    for path in python_files:
        if not any(path.is_relative_to(root) for root in scoped_owner_roots):
            continue
        source = path.read_text(encoding="utf-8")
        for forbidden in ("UnixJsonRpcClient", "JsonRpcCaller"):
            if forbidden in source:
                failures.append(
                    f"owner Python path contains handwritten RPC client {forbidden}: "
                    f"{path.relative_to(ROOT)}"
                )

    for path in python_files:
        relative = path.relative_to(PACKAGE)
        source_subsystem = _subsystem(path)
        source_app = _app_identity(path)
        is_infrastructure = relative.parts[0] == "infrastructure"
        is_entrypoint = any(path.is_relative_to(root) for root in ENTRYPOINT_ROOTS)
        is_domain = "domain" in relative.parts
        is_service = source_app is not None and "services" in relative.parts
        is_system_composition = path.is_relative_to(PACKAGE / "system" / "composition") or path.is_relative_to(
            PACKAGE / "system" / "apps" / "launch" / "composition"
        ) or path == PACKAGE / "system" / "apps" / "launch" / "composition.py"

        for line, module in _imports(path):
            target_parts = module.split(".")

            if path.is_relative_to(PACKAGE / "strategy" / "api") and module.startswith(
                "kairospy.infrastructure.contracts.reference"
            ):
                _failure(
                    failures,
                    path,
                    line,
                    "Strategy API exposes the raw Reference contract instead of ReferenceApplication",
                )

            if (
                path.is_relative_to(
                    PACKAGE / "infrastructure" / "contracts" / "reference"
                )
                and module == "sqlite3"
            ):
                _failure(
                    failures,
                    path,
                    line,
                    "Reference Python contract bypasses its owner binding via sqlite3",
                )

            if (
                path.is_relative_to(PACKAGE / "infrastructure" / "contracts")
                and any(owner in relative.parts for owner in CURRENT_VIEW_OWNERS)
                and path.stem in {"view", "view_contract", "current", "runtime"}
                and (
                    module == "kairospy.infrastructure.transport.indexed_view"
                    or _is_generated_protocol(module)
                )
            ):
                _failure(
                    failures,
                    path,
                    line,
                    f"business current view bypasses its owner PyO3 contract via {module}",
                )

            if module in OWNER_NATIVE_MODULES and not path.is_relative_to(
                PACKAGE / "infrastructure" / "contracts"
            ):
                _failure(
                    failures,
                    path,
                    line,
                    f"private owner extension leaks outside its public facade via {module}",
                )

            if (
                any(
                    path.is_relative_to(
                        PACKAGE / "infrastructure" / "contracts" / owner
                    )
                    for owner in CURRENT_VIEW_OWNERS
                )
                and module in {"typing.Mapping", "collections.abc.Mapping"}
            ):
                _failure(
                    failures,
                    path,
                    line,
                    "owner facade reintroduced a generic Mapping contract",
                )
            target_subsystem = (
                target_parts[1]
                if len(target_parts) > 1
                and target_parts[0] == "kairospy"
                and target_parts[1] in SUBSYSTEMS
                else None
            )

            if is_infrastructure and target_subsystem is not None:
                _failure(failures, path, line, f"Infrastructure imports {module}")

            if is_entrypoint and (
                module.startswith("kairospy.infrastructure")
                or ".services" in module
                or ".composition" in module
            ):
                _failure(failures, path, line, f"entry point bypasses Application via {module}")

            if is_domain and (
                module.startswith("kairospy.infrastructure")
                or ".application" in module
                or ".composition" in module
            ):
                _failure(failures, path, line, f"Domain depends outward on {module}")

            if is_service and source_app is not None and ".apps." in module:
                target_app = None
                if len(target_parts) >= 4 and target_parts[:2] == ["kairospy", source_app[0]] and target_parts[2] == "apps":
                    target_app = target_parts[3]
                if target_app not in {None, source_app[1]} and (
                    ".services" in module or ".composition" in module
                ):
                    _failure(failures, path, line, f"Service crosses sub-app boundary via {module}")

            if (
                source_subsystem is not None
                and target_subsystem is not None
                and source_subsystem != target_subsystem
                and (".services" in module or ".composition" in module or ".domain" in module)
                and not is_system_composition
            ):
                _failure(failures, path, line, f"cross-subsystem call bypasses Application via {module}")

            if (
                (source_subsystem is not None or is_entrypoint)
                and _is_generated_protocol(module)
            ):
                _failure(failures, path, line, f"Generated Protocol leaks through {module}")

            if source_subsystem == "strategy" and module.startswith(
                "kairospy.system.apps.launch"
            ):
                _failure(failures, path, line, f"Strategy reads Launch internals via {module}")

            if source_subsystem == "research" and module.startswith(
                "kairospy.system.apps.launch"
            ) and not (
                ".application.backtests" in module
                or ".application.specs" in module
            ):
                _failure(failures, path, line, f"Research duplicates Launch control via {module}")

    for app in RUST_BACKED_INVESTMENT_APPS:
        domain = PACKAGE / "investment" / "apps" / app / "domain"
        if domain.exists():
            failures.append(
                f"Rust-backed Investment app owns a Python Domain: {domain.relative_to(ROOT)}"
            )

    if failures:
        print("Python architecture violations:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Python architecture checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
