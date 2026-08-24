from __future__ import annotations

from pathlib import Path

import kairospy.research as research_contracts
from kairospy import DataClient, Kairos, ResearchClient
from kairospy.application.data import DatasetCatalogApplication
from kairospy.application.research import ResearchApplication
from kairospy.application.workspace import WorkspaceApplication


ROOT = Path(__file__).parents[1]


def test_application_root_contains_only_the_package_boundary() -> None:
    application_root = ROOT / "kairospy" / "application"
    assert {path.name for path in application_root.glob("*.py")} == {"__init__.py"}


def test_clients_are_implemented_only_in_the_client_surface() -> None:
    assert Kairos.__module__ == "kairospy.surface.client.project"
    assert DataClient.__module__ == "kairospy.surface.client.data"
    assert ResearchClient.__module__ == "kairospy.surface.client.research"
    assert not (ROOT / "kairospy" / "client" / "project.py").exists()
    assert not (ROOT / "kairospy" / "data").exists()
    assert not (ROOT / "kairospy" / "data" / "client.py").exists()
    assert not (ROOT / "kairospy" / "research" / "client.py").exists()


def test_cli_and_client_are_peer_adapters_over_applications() -> None:
    for path in (ROOT / "kairospy" / "surface" / "cli").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "kairospy.surface.client" not in source
        assert "from kairospy import Kairos" not in source

    data_cli = (
        ROOT / "kairospy" / "surface" / "cli" / "commands" / "data.py"
    ).read_text(encoding="utf-8")
    research_cli = (
        ROOT / "kairospy" / "surface" / "cli" / "commands" / "research.py"
    ).read_text(encoding="utf-8")
    assert "DataApplication" in data_cli
    assert "ResearchApplication" in research_cli


def test_research_package_exports_contracts_not_implementations() -> None:
    assert "ResearchClient" not in research_contracts.__all__
    assert "ResearchApplication" not in research_contracts.__all__
    assert "ResearchGateApplication" not in research_contracts.__all__
    assert "ResearchExperimentPolicy" in research_contracts.__all__
    assert not hasattr(research_contracts, "ResearchClient")
    assert not hasattr(research_contracts, "ResearchGateApplication")
    assert research_contracts.ResearchSpec.__module__ == "kairospy.research.protocol"
    assert (
        research_contracts.ResearchExperimentPolicy.__module__
        == "kairospy.research.protocol"
    )


def test_data_catalog_property_does_not_expose_application(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "surface-client", workspace_id="surface-client"
    )
    catalog = DataClient(workspace).catalog
    assert not isinstance(catalog, DatasetCatalogApplication)
    assert catalog.__class__.__module__ == "kairospy.surface.client.data"


def test_research_business_use_cases_are_owned_by_application() -> None:
    for operation in (
        "run_backtests",
        "pin_plan",
        "publish_gate",
        "gate_report",
        "plan",
    ):
        assert operation in ResearchApplication.__dict__


def test_python_does_not_expose_account_fact_mutation_or_legacy_backtest_settlement() -> (
    None
):
    account_contract = (
        ROOT / "kairospy/infrastructure/contracts/account/runtime.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "publish_order_event",
        "publish_fill",
        "apply_simulated_fill",
        '"/v1/order-event"',
        '"/v1/fill"',
        '"/v1/simulated-fill"',
    ):
        assert forbidden not in account_contract

    assert not (ROOT / "kairospy/application/backtest.py").exists()
    application_exports = (ROOT / "kairospy/application/__init__.py").read_text(
        encoding="utf-8"
    )
    assert "run_backtest" not in application_exports
