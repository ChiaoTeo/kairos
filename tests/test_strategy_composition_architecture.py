from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from kairospy.application.launch.application.connections import (
    resolve_instance_connections,
)
from kairospy.application.launch.application.strategy_runtime import (
    StrategyLaunchConfig,
)
from kairospy.application.system.clients import MarketSystemClient
from kairospy.application.workspace import WorkspaceApplication
from kairospy.domain_types import AccountId
from kairospy.strategy import StrategyIdentity
from kairospy.application.market.composition import (
    MarketAccessConfig,
    build_strategy_access as build_market_strategy_access,
)


def test_strategy_has_no_other_module_infrastructure_construction() -> None:
    root = Path(__file__).parents[1]
    strategy = root / "kairospy/application/strategy"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in strategy.rglob("*.py")
    )
    for forbidden in (
        "kairospy.infrastructure",
        "MarketProjection",
        "UnixJsonCommandClient",
        "StrategyClient" + "Bundle",
        "compose_strategy_" + "applications",
        "._release_owner",
        "._command_status",
        "KAIROS_LIVE_",
        "KAIROS_MARKET_SCOPE",
        "KAIROS_BACKTEST_END",
    ):
        assert forbidden not in source


def test_launch_owns_strategy_process_control_and_strategy_owns_composition() -> None:
    root = Path(__file__).parents[1]
    assert (
        root / "kairospy/application/launch/application/strategy_process.py"
    ).is_file()
    assert not (root / "kairospy/application/strategy/application/process.py").exists()
    assert (root / "kairospy/application/strategy/composition.py").is_file()
    assert not (root / "kairospy/application/strategy/services/composition.py").exists()


def test_business_applications_do_not_import_composition_or_infrastructure() -> None:
    root = Path(__file__).parents[1]
    for module in (
        "reference",
        "market",
        "account",
        "risk",
        "execution",
        "portfolio",
        "capital",
        "notification",
    ):
        source = (root / f"kairospy/application/{module}/application.py").read_text(
            encoding="utf-8"
        )
        assert "kairospy.infrastructure" not in source
        assert "import composition" not in source
        assert "from .composition" not in source
        assert (root / f"kairospy/application/{module}/composition.py").is_file()


def test_business_composition_uses_system_clients_for_contract_views() -> None:
    root = Path(__file__).parents[1]
    for module in ("account", "capital", "execution", "risk"):
        source = (root / f"kairospy/application/{module}/composition.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "ViewReader",
            "CurrentViewReader",
            "ViewKey",
            "Projection",
            "from kairospy.infrastructure.contracts",
        ):
            assert forbidden not in source


def test_reference_application_does_not_construct_contract_client() -> None:
    root = Path(__file__).parents[1]
    source = (
        (root / "kairospy/application/reference/composition.py").read_text(
            encoding="utf-8"
        )
        + "\n"
        + (root / "kairospy/application/reference/validation.py").read_text(
            encoding="utf-8"
        )
    )
    assert "kairospy.infrastructure.contracts.reference" not in source
    assert "ReferenceClient(" not in source


def test_business_applications_do_not_mirror_dependencies_as_private_protocols() -> (
    None
):
    root = Path(__file__).parents[1]
    for module in (
        "reference",
        "market",
        "account",
        "risk",
        "execution",
        "portfolio",
        "notification",
    ):
        source = (root / f"kairospy/application/{module}/application.py").read_text(
            encoding="utf-8"
        )
        assert not re.search(
            r"class\s+_(?:\w)*(?:Commands|Snapshots|Reader|Projection|Handle)\s*\([^)]*Protocol",
            source,
        )


def test_strategy_launch_config_reads_canonical_values(tmp_path: Path) -> None:
    path = tmp_path / "normalized-config.json"
    path.write_text(
        json.dumps(
            {
                "launch": {"id": "launch", "mode": "live"},
                "market_scope": "shared",
                "execution": {"enabled": True},
                "live_safety": {
                    "trading_enabled": True,
                    "require_limit_orders": True,
                    "max_order_notional": "1000",
                },
            }
        ),
        encoding="utf-8",
    )

    config = StrategyLaunchConfig.load(path, launch_id="launch", mode="live")

    assert config.authoritative is True
    assert config.market_scope == "shared"
    assert config.execution_enabled is True
    assert config.allow_trading is True
    assert str(config.max_order_notional) == "1000"
    assert config.require_limit_orders is True


def test_strategy_launch_config_rejects_identity_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "normalized-config.json"
    path.write_text(
        json.dumps({"launch": {"id": "other", "mode": "paper"}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="identity"):
        StrategyLaunchConfig.load(path, launch_id="launch", mode="paper")


def test_instance_connections_validate_identity_and_accounts(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "w", workspace_id="typed")
    instance = workspace.instance("paper", "launch", "instance")
    instance.prepare()
    instance.component_manifest().write_text(
        json.dumps(
            {
                "schema_version": 1,
                "launch_id": "launch",
                "instance_id": "instance",
                "mode": "paper",
                "components": {
                    "market": {"socket": str(instance.socket("market"))},
                    "risk": {"socket": str(instance.socket("risk"))},
                    "execution": {"socket": str(instance.socket("execution"))},
                },
                "accounts": {
                    "main": {
                        "socket": str(instance.socket("account-main")),
                        "view_root": str(instance.snapshot()),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    connections = resolve_instance_connections(instance)

    assert connections.accounts[AccountId("main")].socket == instance.socket(
        "account-main"
    )
    assert connections.accounts[AccountId("main")].view_root == instance.snapshot()
    assert connections.market is not None
    assert connections.risk is not None
    assert connections.execution is not None


def test_instance_connections_reject_manifest_for_another_instance(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "w", workspace_id="typed")
    instance = workspace.instance("paper", "launch", "instance")
    instance.prepare()
    instance.component_manifest().write_text(
        json.dumps(
            {
                "schema_version": 1,
                "launch_id": "launch",
                "instance_id": "other",
                "mode": "paper",
                "components": {},
                "accounts": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="instance_id"):
        resolve_instance_connections(instance)


def test_shared_market_access_does_not_claim_launch_instance_scope(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "w", workspace_id="shared")
    instance = workspace.instance("paper", "launch", "instance")
    instance.prepare()

    access = build_market_strategy_access(
        workspace=workspace,
        instance=instance,
        identity=StrategyIdentity("strategy", "launch", "instance"),
        config=MarketAccessConfig(scope="shared"),
        client=MarketSystemClient(workspace.paths.process_socket("market")),
    )

    assert access.application._launch_id is None
