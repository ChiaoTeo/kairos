from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "kairospy"
WORKBENCH = PACKAGE / "surface" / "workbench"


def _python_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.rglob("*.py")))


def test_old_interactive_runtime_is_deleted() -> None:
    for path in (
        PACKAGE / "surface" / "cli" / "interactive",
        PACKAGE / "surface" / "cli" / "guided_setup.py",
        PACKAGE / "surface" / "cli" / "notification_setup.py",
        PACKAGE / "surface" / "console" / "app.py",
    ):
        assert not path.exists(), f"legacy interactive runtime remains: {path}"


def test_workbench_does_not_route_actions_through_typer_or_cli_executor() -> None:
    forbidden = (
        "execute_argv",
        "GuidedCommand",
        "execute_guided_command",
        "shell_path",
        "surface.cli",
        "prettytable",
        "prompt_toolkit",
        "typer",
    )
    for path in _python_files(WORKBENCH):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{token} leaked into {path}"


def test_guided_command_modules_do_not_own_process_or_terminal_boundaries() -> None:
    guided = WORKBENCH / "screens" / "guided"
    forbidden = (
        "subprocess",
        "prompt_toolkit",
        "typer",
        "surface.cli",
        "execute_argv",
        "redirect_stdout",
    )
    for path in _python_files(guided):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{token} leaked into {path}"


def test_product_source_has_no_terminal_prompt_calls() -> None:
    for path in _python_files(PACKAGE):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                assert node.func.id != "input", f"input() remains in {path}:{node.lineno}"
            if isinstance(node.func, ast.Attribute) and isinstance(
                node.func.value, ast.Name
            ):
                assert not (
                    node.func.value.id == "typer"
                    and node.func.attr in {"prompt", "confirm"}
                ), f"typer.{node.func.attr} remains in {path}:{node.lineno}"


def test_only_one_textual_application_shell_exists() -> None:
    app_subclasses: list[tuple[Path, str]] = []
    for path in _python_files(PACKAGE):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if any(
                (isinstance(base, ast.Name) and base.id == "App")
                or (
                    isinstance(base, ast.Subscript)
                    and isinstance(base.value, ast.Name)
                    and base.value.id == "App"
                )
                for base in node.bases
            ):
                app_subclasses.append((path.relative_to(ROOT), node.name))
    assert app_subclasses == [
        (Path("kairospy/surface/workbench/app.py"), "KairosWorkbenchApp")
    ]


def test_application_shell_only_routes_through_command_line_screen() -> None:
    path = WORKBENCH / "app.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    pushed_screens = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "push_screen"
    ]

    assert len(pushed_screens) == 1
    assert "from .screens import CommandLineScreen" in source
    for legacy in (
        "HomeScreen",
        "MarketScreen",
        "ReferenceScreen",
        "StrategyScreen",
        "ResourcesScreen",
        "ResearchScreen",
        "OperationsScreen",
        "ConfirmDialog",
    ):
        assert legacy not in source


def test_workbench_has_one_product_screen_and_no_dialog_package() -> None:
    screen_subclasses: list[tuple[Path, str]] = []
    for path in _python_files(WORKBENCH):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if any(
                (isinstance(base, ast.Name) and base.id in {"Screen", "ModalScreen"})
                or (
                    isinstance(base, ast.Subscript)
                    and isinstance(base.value, ast.Name)
                    and base.value.id in {"Screen", "ModalScreen"}
                )
                for base in node.bases
            ):
                screen_subclasses.append((path.relative_to(ROOT), node.name))

    assert screen_subclasses == [
        (
            Path("kairospy/surface/workbench/screens/command_line.py"),
            "CommandLineScreen",
        )
    ]
    assert not tuple((WORKBENCH / "dialogs").glob("*.py"))


def test_removed_prompt_toolkit_is_not_a_direct_dependency() -> None:
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "prompt-toolkit" not in project
    assert '\ntui = [' not in project


def test_legacy_tui_alias_is_not_registered() -> None:
    source = (PACKAGE / "surface" / "cli" / "app.py").read_text(encoding="utf-8")
    assert '@app.command("tui"' not in source
