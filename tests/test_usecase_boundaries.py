from __future__ import annotations

import ast
import importlib
from pathlib import Path


ROOT = Path(__file__).parents[1]
USECASES = ROOT / "kairospy" / "application" / "usecases"
MODULES = {"account", "execution", "intent", "market", "reference", "risk", "strategy", "workspace"}


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


def test_system_application_is_the_single_public_layer() -> None:
    system = ROOT / "kairospy/application/system"
    assert (system / "application").is_dir()
    assert (system / "application/__init__.py").exists()
    assert not (system / "core").exists()
    assert not (system / "biz").exists()


def test_system_does_not_define_reverse_business_ports() -> None:
    system = ROOT / "kairospy/application/system"
    assert not (system / "biz").exists()
    assert not (system / "core").exists()
    assert not (system / "services/business_tasks.py").exists()


def test_system_application_is_the_runtime_entrypoint() -> None:
    system = ROOT / "kairospy/application/system"
    files = {path.relative_to(system).as_posix() for path in system.rglob("*.py")}
    assert "application/business.py" in files
    assert "application/resources.py" in files
    assert "application/runtime.py" in files
    assert "services/system.py" in files
    violations: list[str] = []
    for path in system.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("kairospy.application.usecases")
                and ".services" in node.module
            ):
                violations.append(f"{path}: {node.module}")
    assert violations == []


def test_system_business_runtime_does_not_hold_usecase_graph() -> None:
    source = (ROOT / "kairospy/application/system/application/business.py").read_text()
    tree = ast.parse(source)
    runtime = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SystemBusinessRuntime")
    fields = {
        target.id
        for node in runtime.body
        if isinstance(node, ast.AnnAssign)
        and isinstance((target := node.target), ast.Name)
    }
    assert fields.isdisjoint({"market_service", "account_service", "capabilities", "projectors", "artifact_projector"})
    assert "MonitorOutputCoordinator" in source
    assert "MarketCommandActor" not in source
    assert "handler=market_actor" in source
    core = (ROOT / "kairospy/application/system/services/system.py").read_text()
    assert "_intents" not in core
    assert "IntentJournal" not in core
    account_actor = (ROOT / "kairospy/application/actor/account/application/actor.py").read_text()
    assert "self._intents" in account_actor


def test_business_actors_are_three_application_boundaries() -> None:
    actor = ROOT / "kairospy/application/actor"
    assert (actor / "support").is_dir()
    assert not (actor / "base.py").exists()
    assert not (actor / "commands.py").exists()
    assert not (actor / "connections.py").exists()
    assert not (actor / "protocol.py").exists()
    assert not (actor / "source.py").exists()
    for name in ("market", "account", "risk"):
        application = actor / name / "application"
        assert application.is_dir()
        assert (application / "__init__.py").exists()
    assert not (actor / "market_commands.py").exists()
    assert not (actor / "reference.py").exists()
    assert not (actor / "execution.py").exists()
    source = (ROOT / "kairospy/application/system/application/business.py").read_text()
    assert "kairospy.application.usecases." not in source
    assert "AccountActorDependencies" in source


def test_system_biz_only_dispatches_control_topics_to_actors() -> None:
    source = (ROOT / "kairospy/application/system/application/business.py").read_text()
    assert '"market.refresh.requested"' in source
    assert '"reference.refresh.requested"' in source
    assert '"account.refresh.requested"' in source
    assert '"execution.refresh.requested"' in source
    assert '"risk.requested"' in source
    assert '"market.quote"' not in source


def test_system_biz_does_not_own_launch_command_dispatch() -> None:
    system = ROOT / "kairospy/application/system"
    assert not (system / "application/session.py").exists()
    assert not (system / "services/trading_authorization.py").exists()
    dispatcher = ROOT / "kairospy/application/support/launch/application/system_commands.py"
    assert dispatcher.exists()
    source = dispatcher.read_text()
    assert "kairospy.application.usecases.account.application" in source
    assert "kairospy.application.system" not in source


def test_system_biz_does_not_own_projection_query_read_side() -> None:
    biz_query = ROOT / "kairospy/application/system/application/projection_query"
    support_query = ROOT / "kairospy/application/support/query/projections"
    assert not any(biz_query.glob("*.py"))
    assert (support_query / "service.py").exists()


def test_system_only_recognizes_strategy_usecase() -> None:
    system = ROOT / "kairospy/application/system"
    imported_modules: set[str] = set()
    for path in system.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                prefix = "kairospy.application.usecases."
                if node.module.startswith(prefix):
                    imported_modules.add(node.module.removeprefix(prefix).split(".", 1)[0])
    assert imported_modules == {"strategy"}


def test_business_domains_do_not_import_other_business_modules() -> None:
    violations: list[str] = []
    for path in (USECASES).rglob("domain/*.py"):
        owner = next((name for name in MODULES if f"/usecases/{name}/" in str(path)), None)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            imported = node.module if isinstance(node, ast.ImportFrom) else None
            if not imported or not imported.startswith("kairospy.application.usecases."):
                continue
            target = imported.split(".")[3]
            if owner is not None and target != owner:
                violations.append(f"{path}: {imported}")
    assert violations == []
