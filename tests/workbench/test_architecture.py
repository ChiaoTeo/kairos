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
                assert node.func.id != "input", (
                    f"input() remains in {path}:{node.lineno}"
                )
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


def test_command_screen_composes_one_activity_stream_and_one_input() -> None:
    source = (WORKBENCH / "screens" / "command_line.py").read_text(encoding="utf-8")

    assert source.count("yield ActivityStream(") == 1
    assert source.count("yield WorkbenchCommandInput(") == 1


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
    assert "\ntui = [" not in project


def test_legacy_tui_alias_is_not_registered() -> None:
    source = (PACKAGE / "surface" / "cli" / "app.py").read_text(encoding="utf-8")
    assert '@app.command("tui"' not in source


def test_product_flows_are_widget_independent_vertical_slices() -> None:
    flows = WORKBENCH / "screens" / "flows"
    forbidden_calls = {"query_one", "set_focus", "run_worker", "push_screen"}
    for path in _python_files(flows):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        assert "textual.screen" not in source
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert not any(
                    isinstance(base, ast.Name)
                    and base.id in {"Screen", "ModalScreen", "App"}
                    for base in node.bases
                ), f"Textual owner leaked into {path}:{node.lineno}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls, (
                    f"Widget API {node.func.attr} leaked into {path}:{node.lineno}"
                )


def test_command_screen_has_no_parallel_operation_or_prompt_compatibility_state() -> (
    None
):
    screen = (WORKBENCH / "screens" / "command_line.py").read_text(encoding="utf-8")
    session = (WORKBENCH / "screens" / "guided" / "models.py").read_text(
        encoding="utf-8"
    )
    for token in (
        "_pending_operation",
        "_pending_action_name",
        "_pending_operation_arguments",
        "_pending_equivalent_command",
        "_operation_committed",
        "_skip_next_operation_output",
        "_terminal_activity_parts",
        "def _run(",
        "def _emit_operation(",
    ):
        assert token not in screen
    for compatibility in (
        "def prompt_mode(",
        "def pending_action(",
        "def argument_prompt(",
        "def confirmation_prompt(",
    ):
        assert compatibility not in session


def test_guided_session_composes_owned_product_state() -> None:
    path = WORKBENCH / "screens" / "guided" / "models.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    guided = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GuidedSession"
    )
    fields = {
        node.target.id
        for node in guided.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert {
        "market",
        "reference",
        "operations",
        "research",
        "resources",
        "strategy",
    } <= fields
    assert not fields & {
        "resource_wizard",
        "research_action",
        "business_prompt",
        "order_prompt",
        "launch_wizard",
        "execution_prompt",
    }


def test_shared_input_uses_typed_action_tokens() -> None:
    path = WORKBENCH / "widgets" / "interaction_region.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    interaction = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "InputInteraction"
    )
    action = next(
        node
        for node in interaction.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "action"
    )
    assert isinstance(action.annotation, ast.Name)
    assert action.annotation.id == "ActionToken"


def test_screen_does_not_import_product_orchestration_symbols() -> None:
    source = (WORKBENCH / "screens" / "command_line.py").read_text(encoding="utf-8")
    for module in (
        ".guided.account",
        ".guided.business",
        ".guided.execution",
        ".guided.launch_market",
        ".guided.operations",
        ".guided.orders",
        ".guided.reference",
        ".guided.research",
        ".guided.workspace_market",
    ):
        assert f"from {module} import" not in source
    for prefix in (
        'command.startswith("research:")',
        'command.startswith("resource:")',
        'command.startswith("strategy:launch")',
        'command.startswith("order:field:")',
    ):
        assert prefix not in source
