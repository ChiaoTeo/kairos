from __future__ import annotations

import tomllib

import pytest

from kairospy.application.account import AccountAdminApplication, CredentialApplication, TradeLeaseApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli.commands.launch import _acquire_launch_leases, _release_launch_leases


def test_account_modify_persists_model_and_other_fields(tmp_path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="account")
    app = AccountAdminApplication(workspace)
    app.simulate("main")

    value = app.modify("main", account_model="margin", environment="paper")

    assert value["account_model"] == "margin"
    assert app.show("main")["account_model"] == "margin"
    assert app.show("main")["environment"] == "paper"


def test_live_account_requires_credential_unless_forced(tmp_path, monkeypatch) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="account")
    app = AccountAdminApplication(workspace)

    with pytest.raises(ValueError, match="credential"):
        app.connect("live")

    credential = CredentialApplication(workspace).add("binance-live", provider="binance", fields=("api_key", "api_secret"))
    monkeypatch.setenv("KAIROS_CREDENTIAL_BINANCE_LIVE_API_KEY", "key")
    monkeypatch.setenv("KAIROS_CREDENTIAL_BINANCE_LIVE_API_SECRET", "secret")
    assert CredentialApplication(workspace).environment("binance-live") == {"API_KEY": "key", "API_SECRET": "secret"}
    account = app.connect("live", credential=credential["credential_id"])
    assert account["credential"] == "binance-live"
    record = tomllib.loads((workspace.paths.account_config().parent / "live.toml").read_text())
    assert record["account"]["id"] == "live"
    assert "segments" in record
    assert not workspace.paths.account_config().exists()


def test_credential_cannot_be_deleted_while_bound(tmp_path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="account")
    credentials = CredentialApplication(workspace)
    credentials.add("paper-key", provider="paper")
    AccountAdminApplication(workspace).connect("main", broker="paper", environment="paper", credential="paper-key")

    with pytest.raises(ValueError, match="bound"):
        credentials.delete("paper-key")
    assert credentials.delete("paper-key", force=True)["status"] == "deleted"


def test_trade_lease_is_workspace_owned_and_exclusive(tmp_path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="account")
    leases = TradeLeaseApplication(workspace)
    value = leases.acquire(broker="binance", account_id="main", environment="live", launch_id="l", launch_instance_id="i", mode="live")
    assert value["account_key"] == "binance.main"
    assert workspace.paths.account_leases().is_dir()
    with pytest.raises(ValueError, match="already leased"):
        leases.acquire(broker="binance", account_id="main", environment="live", launch_id="other", launch_instance_id="other", mode="live")
    assert leases.release("binance.main", force=True)["status"] == "released"


def test_launch_lease_helpers_bind_account_to_instance(tmp_path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="account")
    AccountAdminApplication(workspace).simulate("paper")
    _acquire_launch_leases(workspace, ["paper"], launch_id="run", instance="one", mode="paper")
    assert TradeLeaseApplication(workspace).list()[0]["launch_id"] == "run"
    _release_launch_leases(workspace, ["paper"], instance="one")
    assert TradeLeaseApplication(workspace).list() == []


def test_launch_lease_helpers_release_normalized_account_key(tmp_path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="account")
    AccountAdminApplication(workspace).simulate("paper-account")
    _acquire_launch_leases(workspace, ["paper-account"], launch_id="run", instance="one", mode="paper")

    assert TradeLeaseApplication(workspace).list()[0]["account_key"] == "paper.paper_account"
    _release_launch_leases(workspace, ["paper-account"], instance="one")
    assert TradeLeaseApplication(workspace).list() == []
