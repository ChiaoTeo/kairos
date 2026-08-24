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
    if not generated.is_dir():
        failures.append("kairospy/infrastructure/protocol/generated is missing")

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
                and "kairospy.infrastructure.protocol.generated" in module
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
