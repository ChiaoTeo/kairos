from __future__ import annotations

import ast
import importlib
from pathlib import Path


ROOT = Path(__file__).parents[1]
USECASES = ROOT / "kairospy" / "application" / "usecases"
MODULES = {"account", "execution", "intent", "market", "reference", "strategy"}


def _python_files() -> tuple[Path, ...]:
    return tuple(USECASES.rglob("*.py"))


def test_cross_usecase_services_are_not_imported() -> None:
    violations: list[str] = []
    for path in _python_files():
        owner = next((name for name in MODULES if f"/usecases/{name}/" in str(path)), None)
        if owner is None:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            imported = node.module if isinstance(node, ast.ImportFrom) else None
            if imported is None:
                continue
            parts = imported.split(".")
            if len(parts) >= 6 and parts[:4] == ["kairospy", "application", "usecases", parts[3]]:
                target = parts[3]
                if target in MODULES and target != owner and "services" in parts:
                    violations.append(f"{path}: {imported}")
    assert violations == []


def test_non_owner_code_cannot_import_usecase_services() -> None:
    violations: list[str] = []
    for path in (ROOT / "kairospy").rglob("*.py"):
        source_text = str(path)
        owner = next((name for name in MODULES if f"/usecases/{name}/" in source_text), None)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            imported = node.module if isinstance(node, ast.ImportFrom) else None
            if not imported:
                continue
            parts = imported.split(".")
            if len(parts) < 6 or parts[:3] != ["kairospy", "application", "usecases"] or "services" not in parts:
                continue
            target = parts[3]
            if target not in MODULES or owner != target:
                violations.append(f"{path}: {imported}")
    assert violations == []


def test_usecase_application_packages_do_not_aggregate_exports() -> None:
    for module in MODULES:
        init = USECASES / module / "application" / "__init__.py"
        tree = ast.parse(init.read_text(), filename=str(init))
        imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
        assert imports == [], f"{init} must not aggregate application exports"


def test_all_usecase_application_modules_are_importable() -> None:
    for module in MODULES:
        directory = USECASES / module / "application"
        for source in directory.glob("*.py"):
            importlib.import_module(f"kairospy.application.usecases.{module}.application.{source.stem}")
