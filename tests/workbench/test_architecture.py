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


def test_cli_depends_only_on_the_public_workbench_launcher() -> None:
    cli = PACKAGE / "surface" / "cli"
    forbidden = (
        "KairosWorkbenchApp",
        "load_workbench_state",
        "surface.workbench.app",
        "surface.workbench.screens",
        "surface.workbench.state",
    )
    for path in _python_files(cli):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{token} leaked into {path}"


def test_product_modules_do_not_own_process_or_terminal_boundaries() -> None:
    products = WORKBENCH / "screens" / "flows"
    forbidden = (
        "subprocess",
        "prompt_toolkit",
        "typer",
        "surface.cli",
        "execute_argv",
        "redirect_stdout",
    )
    for path in _python_files(products):
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


def test_command_screen_is_only_a_textual_and_product_router_boundary() -> None:
    source = (WORKBENCH / "screens" / "command_line.py").read_text(encoding="utf-8")
    for prefix in (
        "kairospy.investment.apps",
        "kairospy.strategy.apps",
        "kairospy.system.apps",
        "kairospy.surface.cli",
    ):
        assert prefix not in source


def test_workbench_state_owns_no_product_selection() -> None:
    path = WORKBENCH / "state.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    state = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "WorkbenchState"
    )
    fields = {
        node.target.id
        for node in state.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert not {name for name in fields if name.startswith("selected_")}


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
    session = (WORKBENCH / "screens" / "session.py").read_text(encoding="utf-8")
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


def test_workbench_session_composes_owned_product_state() -> None:
    path = WORKBENCH / "screens" / "session.py"
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
        "account",
        "strategy",
        "execution",
        "launch_market",
    } <= fields
    assert not fields & {
        "resource_wizard",
        "research_action",
        "business_prompt",
        "order_prompt",
        "launch_wizard",
        "execution_prompt",
    }


def test_product_sessions_do_not_retain_untyped_result_payloads() -> None:
    path = WORKBENCH / "screens" / "session.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    product_sessions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name.endswith("Session")
    }

    for name, session in product_sessions.items():
        for node in session.body:
            if not isinstance(node, ast.AnnAssign):
                continue
            annotation = ast.unparse(node.annotation)
            assert "Any" not in annotation, f"Any state leaked into {name}"
            assert "dict[" not in annotation, f"dict state leaked into {name}"


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


def test_navigation_control_flow_uses_canonical_route_identities() -> None:
    """Route spelling belongs to navigation.identity, not individual flows."""

    route_owners = {
        "project",
        "market",
        "reference",
        "strategy",
        "account",
        "resources",
        "research",
        "operations",
    }
    routing_files = (
        WORKBENCH / "screens" / "command_line.py",
        WORKBENCH / "screens" / "navigation" / "tree.py",
        WORKBENCH / "screens" / "flows" / "__init__.py",
        WORKBENCH / "screens" / "flows" / "market" / "runtime.py",
        WORKBENCH / "screens" / "flows" / "reference" / "runtime.py",
        WORKBENCH / "screens" / "flows" / "research" / "runtime.py",
        WORKBENCH / "screens" / "flows" / "operations" / "runtime.py",
        WORKBENCH / "screens" / "flows" / "account" / "runtime.py",
        WORKBENCH / "screens" / "flows" / "resources" / "configuration.py",
        WORKBENCH / "screens" / "flows" / "launch" / "runtime.py",
        WORKBENCH / "screens" / "flows" / "launch" / "execution.py",
        WORKBENCH / "screens" / "flows" / "launch" / "market.py",
    )
    for path in routing_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Tuple) or not node.elts:
                continue
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value in route_owners:
                raise AssertionError(
                    f"raw navigation route remains in {path}:{node.lineno}"
                )


def test_primary_task_catalog_uses_typed_action_identities() -> None:
    """Stable visible tasks must not repeat action spellings as raw strings."""

    path = WORKBENCH / "screens" / "navigation" / "catalog.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    primary_catalogs = {
        "HOME_ACTIONS",
        "MARKET_ACTIONS",
        "REFERENCE_ACTIONS",
        "STRATEGY_ACTIONS",
        "RESOURCE_ACTIONS",
        "AI_MODEL_ACTIONS",
        "RESEARCH_ACTIONS",
        "OPERATIONS_ACTIONS",
    }
    for assignment in tree.body:
        if not isinstance(assignment, ast.Assign):
            continue
        names = {
            target.id for target in assignment.targets if isinstance(target, ast.Name)
        }
        if not names & primary_catalogs:
            continue
        for node in ast.walk(assignment.value):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ActionItem"
                and node.args
            ):
                continue
            assert not isinstance(node.args[0], ast.Constant), (
                f"raw primary action identity remains in {path}:{node.lineno}"
            )


def test_account_input_continuations_have_an_explicit_product_owner() -> None:
    router = (WORKBENCH / "screens" / "flows" / "__init__.py").read_text(
        encoding="utf-8"
    )
    account = (WORKBENCH / "screens" / "flows" / "account" / "runtime.py").read_text(
        encoding="utf-8"
    )

    assert "if token.feature is Feature.ACCOUNT:" in router
    assert "Feature.ACCOUNT" in account
    assert "Feature.RESOURCES" not in account


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
    for lifecycle in (
        "market.handle_input",
        "operations.handle_input",
        "resources.handle_input",
        "strategy.handle_input",
        "market.handle_command",
        "operations.handle_command",
        "resources.handle_command",
        "strategy.handle_command",
        "market.handle_success",
        "operations.handle_success",
        "resources.handle_success",
        "strategy.handle_success",
        "market.handle_failure",
        "operations.handle_failure",
        "resources.handle_failure",
        "strategy.handle_failure",
        "market.handle_cancel",
        "operations.handle_cancel",
        "resources.handle_cancel",
        "strategy.handle_cancel",
    ):
        assert lifecycle not in source


def test_parallel_guided_product_tree_has_been_removed() -> None:
    guided = WORKBENCH / "screens" / "guided"
    assert not guided.exists() or not tuple(guided.glob("*.py"))
