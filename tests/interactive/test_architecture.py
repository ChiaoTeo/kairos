from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[2]
INTERACTIVE = ROOT / "kairospy" / "surface" / "cli" / "interactive"


def test_interactive_package_has_all_owned_sections() -> None:
    expected = {
        "sections/getting_started/home.py",
        "sections/getting_started/project.py",
        "sections/strategy/launch.py",
        "sections/strategy/observe.py",
        "sections/research_data/data.py",
        "sections/research_data/research.py",
        "sections/business/account.py",
        "sections/business/market.py",
        "sections/business/reference.py",
        "sections/business/order.py",
        "sections/business/risk.py",
        "sections/business/capital.py",
        "sections/business/integration.py",
        "sections/business/notifications.py",
        "sections/system/runtime.py",
        "sections/system/config.py",
    }
    assert expected <= {
        str(path.relative_to(INTERACTIVE)) for path in INTERACTIVE.rglob("*.py")
    }


def test_every_product_section_owns_standard_entry_functions() -> None:
    for path in (INTERACTIVE / "sections").rglob("*.py"):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        assert {"print_menu", "print_help", "handle"} <= functions, path


def test_session_has_no_business_command_construction() -> None:
    tree = ast.parse((INTERACTIVE / "session.py").read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "GuidedCommand"
    ]
    assert calls == []


def test_core_dependency_direction_and_public_api_are_narrow() -> None:
    for name in ("models.py", "context.py", "execution.py"):
        text = (INTERACTIVE / name).read_text(encoding="utf-8")
        assert ".sections" not in text
        assert ".session" not in text
    init_text = (INTERACTIVE / "__init__.py").read_text(encoding="utf-8")
    assert '__all__ = ["run_interactive"]' in init_text
    for forbidden in ("Router", "Manager", "Registry", "Handler", "Protocol"):
        assert f"class {forbidden}" not in "\n".join(
            path.read_text(encoding="utf-8") for path in INTERACTIVE.rglob("*.py")
        )
