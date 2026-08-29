from __future__ import annotations

import json
from io import StringIO

import pytest

from kairospy.surface.cli.app import execute_argv
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.integration.application import (
    ProviderConnectionConfigurationApplication,
)
from kairospy.system.apps.configuration.application import ConfigApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.strategy.apps.runtime.services.loader import load_strategy


def test_catalog_exposes_versioned_product_templates() -> None:
    templates = WorkspaceApplication().list_templates()

    assert [value["template_id"] for value in templates] == [
        "backtest-basic",
        "paper-manual",
        "paper-strategy",
    ]
    assert {value["version"] for value in templates} == {"1.0.0"}
    strategy = WorkspaceApplication().show_template("paper-strategy")
    assert strategy["requirements"] == [
        {
            "kind": "provider-connection",
            "parameter": "market_connection",
            "capability": "market-query",
            "products": ["equity"],
        }
    ]


def test_backtest_alias_installs_user_owned_files_and_tracks_changes(tmp_path) -> None:
    project = tmp_path / "project"
    workspace = WorkspaceApplication().init_project(project, workspace_id="project")

    created = WorkspaceApplication().install_template(workspace, template="backtest")

    assert project / "kairos_demo" / "strategy.py" in created
    assert WorkspaceApplication().template_status(workspace) == [
        {
            "installation_id": "demo-backtest",
            "template_id": "backtest-basic",
            "installed_version": "1.0.0",
            "available_version": "1.0.0",
            "update_available": False,
            "files": [
                {"path": "kairos_demo/__init__.py", "status": "current"},
                {"path": "kairos_demo/strategy.py", "status": "current"},
                {
                    "path": ".kairos/config/accounts/demo-paper.toml",
                    "status": "current",
                },
                {
                    "path": ".kairos/data/examples/demo-market.jsonl",
                    "status": "current",
                },
                {
                    "path": ".kairos/config/launches/demo-backtest.toml",
                    "status": "current",
                },
                {"path": "KAIROS_QUICKSTART.md", "status": "current"},
            ],
            "status": "current",
        }
    ]

    strategy = project / "kairos_demo" / "strategy.py"
    strategy.write_text(strategy.read_text() + "\n# user change\n")
    assert WorkspaceApplication().template_status(workspace)[0]["status"] == "customized"

    with pytest.raises(FileExistsError, match="would overwrite"):
        WorkspaceApplication().install_template(workspace, template="backtest-basic")
    assert "# user change" in strategy.read_text()


def test_backtest_install_rolls_back_when_replay_profile_is_incompatible(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    workspace = WorkspaceApplication().init_project(project, workspace_id="project")
    manifest = workspace.paths.manifest
    manifest.write_text(
        manifest.read_text()
        + '\n[market.profiles.replay]\nscope = "shared"\n',
        encoding="utf-8",
    )
    original = manifest.read_text()

    with pytest.raises(ValueError, match="replay market profile is incompatible"):
        WorkspaceApplication().install_template(
            workspace, template="backtest-basic"
        )

    assert manifest.read_text() == original
    assert not (project / "kairos_demo" / "strategy.py").exists()
    assert not workspace.paths.launch_config("demo-backtest").exists()
    assert not workspace.paths.child(
        "config", "templates", "demo-backtest.toml"
    ).exists()


def test_paper_manual_accepts_resource_ids_as_install_parameters(tmp_path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="project"
    )

    WorkspaceApplication().install_template(
        workspace,
        template="paper-manual",
        installation_id="desk",
        parameters={"launch_id": "desk-paper", "account_id": "desk-account"},
    )

    assert workspace.paths.launch_config("desk-paper").is_file()
    assert (workspace.paths.account_config().parent / "desk-account.toml").is_file()
    assert WorkspaceApplication().template_status(
        workspace, installation_id="desk"
    )[0]["status"] == "current"


def test_project_doctor_respects_customization_and_reports_missing_files(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    workspace = WorkspaceApplication().init_project(
        project, workspace_id="project", template="backtest"
    )
    strategy = project / "kairos_demo" / "strategy.py"
    strategy.write_text(strategy.read_text() + "\n# intentionally customized\n")

    customized = ConfigApplication(workspace).doctor()

    assert customized["ok"] is True
    assert customized["templates"][0]["status"] == "customized"
    guide = project / "KAIROS_QUICKSTART.md"
    guide.unlink()

    missing = ConfigApplication(workspace).doctor()

    assert missing["ok"] is False
    assert missing["templates"][0]["status"] == "missing"
    assert "generated file is missing" in missing["issues"][0]


def test_paper_strategy_requires_a_verified_equity_market_connection(tmp_path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="project"
    )
    values = {"market_connection": "market-data"}

    with pytest.raises(ValueError, match="existing provider connection"):
        WorkspaceApplication().install_template(
            workspace, template="paper-strategy", parameters=values
        )

    CredentialConfigurationApplication(workspace).configure(
        "massive-readonly",
        provider="massive",
        values={"api_key": "secret"},
    )
    providers = ProviderConnectionConfigurationApplication(workspace)
    providers.configure(
        "market-data",
        provider="massive",
        credential_id="massive-readonly",
        products=("equity",),
        purposes=("market-query",),
    )
    with pytest.raises(ValueError, match="verified provider connection"):
        WorkspaceApplication().install_template(
            workspace, template="paper-strategy", parameters=values
        )

    providers.test_connection(
        "market-data",
        probe=lambda _connection, _secrets: {
            "capabilities": ["market-query"],
            "observed_permissions": ["market-read"],
            "warnings": [],
        },
    )
    created = WorkspaceApplication().install_template(
        workspace, template="paper-strategy", parameters=values
    )
    assert workspace.paths.launch_config("demo-paper-strategy") in created
    launch = workspace.paths.launch_config("demo-paper-strategy").read_text()
    assert 'profile = "market-data"' in launch
    assert "secret" not in launch
    record = workspace.paths.child(
        "config", "templates", "demo-paper-strategy.toml"
    ).read_text()
    assert "secret" not in record


def test_template_cli_lists_installs_and_reports_status(tmp_path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="project"
    )
    output = StringIO()

    assert execute_argv(["template", "list", "--format", "json"], output) == 0
    assert len(json.loads(output.getvalue())) == 3

    output = StringIO()
    assert (
        execute_argv(
            [
                "template",
                "install",
                "paper-manual",
                "--workspace",
                str(workspace.paths.root),
                "--name",
                "manual",
                "--param",
                "launch_id=manual-paper",
                "--param",
                "account_id=manual-account",
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )
    assert json.loads(output.getvalue())["ownership"].startswith(
        "generated files belong"
    )

    output = StringIO()
    assert (
        execute_argv(
            [
                "template",
                "status",
                "manual",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )
    assert json.loads(output.getvalue())[0]["status"] == "current"


def test_template_cli_installs_strategy_in_a_user_selected_directory(tmp_path) -> None:
    project = tmp_path / "project"
    workspace = WorkspaceApplication().init_project(project, workspace_id="project")
    output = StringIO()

    assert (
        execute_argv(
            [
                "template",
                "install",
                "backtest-basic",
                "--workspace",
                str(workspace.paths.root),
                "--destination",
                "strategies/momentum",
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )

    assert (project / "strategies" / "momentum" / "strategy.py").is_file()
    launch = workspace.paths.launch_config("demo-backtest").read_text()
    assert 'strategy = "strategies.momentum.strategy:DemoBacktest"' in launch
    entrypoint = load_strategy(
        "strategies.momentum.strategy:DemoBacktest", root=project, params={}
    )
    assert entrypoint.strategy.strategy_id == "demo-backtest"
    status = WorkspaceApplication().template_status(workspace)[0]
    assert status["files"][0] == {
        "path": "strategies/momentum/__init__.py",
        "status": "current",
    }


def test_template_cli_rejects_a_destination_outside_the_project(tmp_path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="project"
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "template",
                "install",
                "backtest-basic",
                "--workspace",
                str(workspace.paths.root),
                "--destination",
                "../outside",
            ],
            output,
        )
        != 0
    )
    assert not (tmp_path / "outside").exists()
