from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from kairospy.application.launch.application import (
    BacktestSpec,
    LaunchConfigError,
    LaunchConfigurationApplication,
    LaunchRuntimeApplication,
    OptionBacktestConstraints,
)
from kairospy import Kairos
from kairospy.application.data import DatasetRef, DatasetSetRef
from kairospy.application.launch.application.wizard import (
    LaunchDraft,
    build_and_validate,
    draft_preview,
    load_values,
    prompt_agent_config,
    prompt_notification_config,
)
from kairospy.application.workspace import WorkspaceApplication
from kairospy.application.account import AccountConfigurationApplication
from kairospy.application.credential import (
    CredentialConfigurationApplication,
    SecretRef,
)
from kairospy.surface.cli import execute_argv
from kairospy.surface.cli.commands.launch import (
    _launch_config_path,
    _live_start_confirmation,
    _prompt_launch_draft,
)
from kairospy.application.launch.application.wizard import LaunchWizardExit
from io import StringIO


def test_final_preview_is_complete_and_secret_safe() -> None:
    values = {
        "launch": {"id": "live-grid", "mode": "live", "strategy": "grid:run"},
        "accounts": {"main": {"ref": "main", "segments": ["spot"], "trade": True}},
        "execution": {"enabled": True, "routes": [{"route_id": "main-spot"}]},
        "risk": {"profile": "live-default"},
        "live": {"safety": {"max_order_notional": "100"}},
        "agent": {
            "enabled": True,
            "model": {
                "credential": "openai-main",
                "model": "gpt-snapshot",
                "api_key": "must-not-render",
            },
            "profile": {"version": "2"},
            "mcp": [{"allowed_tools": ["account.get_position", "order.submit"]}],
        },
        "notifications": {
            "enabled": True,
            "required": True,
            "routes": {"risk": ["telegram-ops"]},
        },
        "legacy_secret": "also-must-not-render",
    }

    preview = draft_preview(values)

    for expected in (
        "LIVE（会连接真实账户）",
        "main / spot / 允许交易",
        "live-default",
        "单笔最大名义金额 100",
        "openai-main / gpt-snapshot",
        "2 个工具 · 1 个疑似写入工具",
        "telegram-ops · required",
    ):
        assert expected in preview
    assert "must-not-render" not in preview


def test_live_start_confirmation_repeats_account_environment_and_risk(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="live")
    accounts = AccountConfigurationApplication(workspace)
    accounts.connect(
        "live-main",
        broker="paper",
        integration_provider="paper",
        environment="live",
    )
    accounts.test_connection("live-main")
    path = workspace.paths.launch_config("live-grid")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '[launch]\nid = "live-grid"\nmode = "live"\nstrategy = "grid:run"\n\n'
        '[accounts.main]\nref = "live-main"\nsegments = ["spot"]\ntrade = true\n\n'
        "[execution]\nenabled = false\n\n"
        '[risk]\nprofile = "live-default"\n\n'
        "[live.safety]\ntrading_enabled = true\nrequire_limit_orders = true\n"
        'max_order_notional = "100"\n',
        encoding="utf-8",
    )
    config = LaunchConfigurationApplication().load(
        path, workspace_root=workspace.paths.root
    )

    warning = _live_start_confirmation(workspace, config)

    assert "LIVE（会产生真实外部副作用）" in warning
    assert "live-main / live / spot / 允许交易" in warning
    assert "live-default" in warning
    assert "单笔最大范围 100" in warning


def test_live_execution_requires_explicit_side_effect_and_notional_bound(
    tmp_path: Path,
) -> None:
    path = tmp_path / "live.toml"
    path.write_text(
        '[launch]\nid = "live"\nmode = "live"\nstrategy = "x:y"\n\n'
        "[execution]\nenabled = true\n"
        'routes = [{ route_id = "main-spot", account_id = "main", '
        'segment_key = "spot", broker_id = "binance", '
        'execution_channel = "spot" }]\n\n'
        '[accounts.main]\nref = "main"\nsegments = ["spot"]\ntrade = true\n\n'
        '[risk]\nprofile = "production-default"\n\n'
        "[live.safety]\ntrading_enabled = false\nrequire_limit_orders = true\n",
        encoding="utf-8",
    )

    blocked = LaunchConfigurationApplication().validate(path)
    assert "live.safety.trading_enabled must be explicitly true" in " ".join(
        blocked["issues"]
    )
    assert "live.safety.max_order_notional is required" in " ".join(blocked["issues"])
    assert all(item["severity"] == "blocker" for item in blocked["diagnostics"])
    assert any(
        item["owner"] == "Risk/Launch" and item["action"]
        for item in blocked["diagnostics"]
    )

    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "trading_enabled = false",
            'trading_enabled = true\nmax_order_notional = "100"',
        ),
        encoding="utf-8",
    )
    assert LaunchConfigurationApplication().validate(path)["valid"] is True


def test_launch_wizard_q_offers_persistent_draft_exit(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="q")
    application = LaunchConfigurationApplication()
    values = {"launch": {"id": "working", "mode": "paper", "strategy": "x:y"}}
    application.save_draft(workspace.paths.root, "working", values)
    monkeypatch.setattr(
        "kairospy.surface.cli.commands.launch.prompt_draft",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(LaunchWizardExit()),
    )
    monkeypatch.setattr(
        "kairospy.surface.cli.commands.launch.typer.prompt",
        lambda *_args, **_kwargs: "1",
    )

    draft, status = _prompt_launch_draft(
        values,
        default_launch_id="working",
        owner=workspace,
        application=application,
    )

    assert draft is None
    assert status == "draft_saved"
    assert application.draft_path(workspace.paths.root, "working").is_file()


def test_launch_wizard_atomically_persists_each_completed_step_before_q(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="step-draft"
    )
    application = LaunchConfigurationApplication()
    initial = {
        "launch": {
            "id": "working",
            "mode": "paper",
            "strategy": "builtin:interactive",
        }
    }
    application.save_draft(workspace.paths.root, "working", initial)
    answers = iter(["paper", "custom:Strategy", "2", "q", "1"])
    monkeypatch.setattr("typer.prompt", lambda *_args, **_kwargs: next(answers))

    draft, status = _prompt_launch_draft(
        initial,
        default_launch_id="working",
        owner=workspace,
        application=application,
    )

    persisted = application.load_draft(workspace.paths.root, "working")
    assert draft is None
    assert status == "draft_saved"
    assert persisted["launch"] == {
        "id": "working",
        "mode": "paper",
        "strategy": "custom:Strategy",
    }
    assert persisted["accounts"] == {}


def test_launch_wizard_records_account_resource_return_without_changing_intent(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="resource-return"
    )
    application = LaunchConfigurationApplication()
    initial = {
        "launch": {
            "id": "working",
            "mode": "paper",
            "strategy": "builtin:interactive",
        }
    }
    application.save_draft(workspace.paths.root, "working", initial)
    answers = iter(["paper", "custom:Strategy", "1"])
    monkeypatch.setattr("typer.prompt", lambda *_args, **_kwargs: next(answers))

    draft, status = _prompt_launch_draft(
        initial,
        default_launch_id="working",
        owner=workspace,
        application=application,
    )

    assert draft is None
    assert status == "resource_required:accounts"
    assert application.draft_return(workspace.paths.root, "working") == {
        "launch_id": "working",
        "resource": "accounts",
        "step": "accounts_and_execution_scope",
    }
    assert (
        application.load_draft(workspace.paths.root, "working")["launch"]["strategy"]
        == "custom:Strategy"
    )


def test_launch_rejects_an_unverified_arbitrary_workspace_data_profile(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="unverified-data"
    )
    path = workspace.paths.launch_config("paper-unverified-data")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '[launch]\nid = "paper-unverified-data"\nmode = "paper"\n'
        'strategy = "builtin:interactive"\n\n'
        "[execution]\nenabled = false\n\n"
        '[paper.market]\nprofile = "some-unverified-profile"\nscope = "shared"\n',
        encoding="utf-8",
    )

    report = LaunchConfigurationApplication().validate(
        path, workspace_root=workspace.paths.root
    )

    assert report["valid"] is False
    assert report["diagnostics"] == [
        {
            "owner": "Reference/Market",
            "resource": "data_provider",
            "severity": "blocker",
            "reason": (
                "Workspace data connection is not supported or verified: "
                "some-unverified-profile"
            ),
            "action": "configure and manually test the selected data connection",
        }
    ]


def test_enabled_agent_and_notification_keep_incomplete_intent_for_resource_fix(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="incomplete-resources"
    )
    confirmations = iter([True, False, True])
    monkeypatch.setattr("typer.confirm", lambda *_args, **_kwargs: next(confirmations))
    monkeypatch.setattr(
        "typer.prompt",
        lambda *_args, **kwargs: str(kwargs.get("default") or ""),
    )

    agent = prompt_agent_config({}, mode="paper", workspace=workspace)
    notifications = prompt_notification_config({}, mode="paper", workspace=workspace)

    assert agent["enabled"] is True
    assert agent["required"] is False
    assert "model" not in agent
    assert notifications == {"enabled": True, "required": False, "routes": {}}


def _write_config(path: Path, *, mode: str = "paper") -> Path:
    content = (
        "[launch]\n"
        'id = "demo-launch"\n'
        f'mode = "{mode}"\n'
        'strategy = "strategy:Factory"\n\n'
        "[account]\n"
        'ref = "paper-account"\n\n'
        f"[{mode}]\n"
    )
    if mode == "live":
        content += "\n[live.safety]\ntrading_enabled = false\n"
    path.write_text(content, encoding="utf-8")
    return path


def test_launch_config_validates_and_explains_toml(tmp_path: Path) -> None:
    config = _write_config(tmp_path / "demo.toml")
    application = LaunchConfigurationApplication()

    result = application.validate(config)
    explanation = application.explain(config)

    assert result["valid"] is True
    assert explanation["launch"]["id"] == "demo-launch"
    assert explanation["account_refs"] == ["paper-account"]


def test_launch_config_supports_disabled_and_multiple_accounts(tmp_path: Path) -> None:
    config = tmp_path / "manual.toml"
    config.write_text(
        """[launch]
id = "manual"
mode = "paper"
strategy = "builtin:interactive"

[accounts.main]
ref = "main"
enabled = true

[accounts.secondary]
ref = "secondary"
enabled = false

[execution]
enabled = false
""",
        encoding="utf-8",
    )

    plan = LaunchConfigurationApplication().load(config, workspace_root=tmp_path).plan()
    assert plan.account_refs == ("main",)
    assert plan.execution["enabled"] is False


def test_capital_is_optional_but_enabled_instance_requires_stable_identity(
    tmp_path: Path,
) -> None:
    disabled = _write_config(tmp_path / "capital-disabled.toml")
    assert LaunchConfigurationApplication().validate(disabled)["valid"] is True

    enabled = tmp_path / "capital-enabled.toml"
    enabled.write_text(
        """[launch]
id = "capital-enabled"
mode = "paper"
strategy = "builtin:interactive"

[account]
ref = "paper-account"

[capital]
enabled = true
capital_group_id = "strategy-capital"
strategy_id = "strategy-a"

[capital.member_readiness]
paper-account = "optional"

[[capital.policies]]
minimum = "100"
default_target = "200"
maximum = "500"
stress_buffer = "25"
minimum_movement = "10"
hysteresis = "5"
deficit_dwell_millis = 1000
cooldown_millis = 5000
max_fact_age_millis = 3000

[capital.policies.destination]
broker = "binance"
account_id = "paper-account"
segment = "usd-m"
asset = "USDT"

[paper]
""",
        encoding="utf-8",
    )
    plan = LaunchConfigurationApplication().load(enabled).plan()
    assert plan.capital["enabled"] is True
    assert plan.capital["capital_group_id"] == "strategy-capital"
    assert plan.capital["strategy_id"] == "strategy-a"
    assert plan.capital["member_readiness"] == {"paper-account": "optional"}
    assert plan.capital["policies"][0]["maximum"] == "500"

    automatic = tmp_path / "capital-automatic.toml"
    automatic.write_text(
        enabled.read_text(encoding="utf-8").replace(
            'strategy_id = "strategy-a"',
            'strategy_id = "strategy-a"\nautomatic_execution = true',
        ),
        encoding="utf-8",
    )
    automatic_report = LaunchConfigurationApplication().validate(automatic)
    assert automatic_report["valid"] is False
    assert "capital.routes" in " ".join(automatic_report["issues"])

    missing_identity = enabled.read_text(encoding="utf-8").replace(
        'capital_group_id = "strategy-capital"\n', ""
    )
    enabled.write_text(missing_identity, encoding="utf-8")
    report = LaunchConfigurationApplication().validate(enabled)
    assert report["valid"] is False
    assert "capital.capital_group_id" in " ".join(report["issues"])

    invalid_role = tmp_path / "capital-invalid-member-role.toml"
    invalid_role.write_text(
        automatic.read_text(encoding="utf-8")
        .replace("automatic_execution = true", "automatic_execution = false")
        .replace('paper-account = "optional"', 'paper-account = "best_effort"'),
        encoding="utf-8",
    )
    role_report = LaunchConfigurationApplication().validate(invalid_role)
    assert role_report["valid"] is False
    assert "critical or optional" in " ".join(role_report["issues"])


def test_launch_config_preserves_required_segments_per_account(tmp_path: Path) -> None:
    config = tmp_path / "required-segments.toml"
    config.write_text(
        """[launch]
id = "required-segments"
mode = "paper"
strategy = "builtin:interactive"

[accounts.primary]
ref = "main"
required_segments = ["spot", "usd_m_futures", "spot"]

[execution]
enabled = false
""",
        encoding="utf-8",
    )

    plan = LaunchConfigurationApplication().load(config, workspace_root=tmp_path).plan()

    assert plan.required_account_segments == {"main": ("spot", "usd_m_futures")}
    assert plan.normalized()["account_requirements"] == {
        "main": {"required_segments": ["spot", "usd_m_futures"]}
    }


def test_launch_draft_preserves_advanced_values_and_writes_valid_toml(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch-draft"
    )
    accounts = AccountConfigurationApplication(workspace)
    for account_id in ("main", "secondary"):
        accounts.simulate(account_id)
        accounts.test_connection(account_id)
    path = workspace.paths.launch_config("manual")
    original = {
        "launch": {"id": "old", "mode": "paper", "strategy": "old:Strategy"},
        "strategy": {"params": {"symbol": "BTCUSDT"}},
        "paper": {"events": "events.jsonl"},
    }
    updated = LaunchDraft(
        launch_id="manual",
        mode="paper",
        strategy="builtin:interactive",
        accounts=("main", "secondary"),
        execution_enabled=False,
    ).apply(original)

    report = build_and_validate(path, updated, workspace.paths.root)

    assert report["valid"] is True
    values = load_values(path)
    assert values["launch"]["strategy"] == "builtin:interactive"
    assert values["strategy"]["params"] == {"symbol": "BTCUSDT"}
    assert values["paper"]["events"] == "events.jsonl"
    assert values["accounts"]["account_2"]["ref"] == "secondary"


def test_launch_draft_writes_agent_selection_without_account_or_secret_copy() -> None:
    values = LaunchDraft(
        launch_id="agent-paper",
        mode="paper",
        strategy="builtin:interactive",
        accounts=("main",),
        execution_enabled=False,
        agent={
            "enabled": True,
            "required": False,
            "runtime": "openai-agents",
            "profile": "intent-review-v1",
            "model": {
                "provider": "openai",
                "model": "gpt-5.4-2026-03-05",
                "credential": "openai-prod",
            },
            "capabilities": {
                "intent_review": {
                    "initial_mode": "shadow",
                    "strategy_selectable_modes": ["shadow", "gate"],
                    "operations": ["target_position"],
                    "failure_policy": "reject_new_exposure",
                    "required_contexts": [],
                    "revisions": {},
                }
            },
            "mcp": [],
        },
    ).apply({})

    assert values["agent"]["model"]["credential"] == "openai-prod"
    assert "api_key" not in repr(values["agent"])
    assert "accounts" not in values["agent"]


def test_launch_draft_does_not_replace_existing_file_when_validation_fails(
    tmp_path: Path,
) -> None:
    path = tmp_path / "existing.toml"
    path.write_text(
        '[launch]\nid = "existing"\nmode = "paper"\nstrategy = "strategy:Factory"\n',
        encoding="utf-8",
    )
    draft = LaunchDraft(
        launch_id="broken",
        mode="backtest",
        strategy="strategy:Factory",
        accounts=(),
        execution_enabled=True,
    )

    with pytest.raises(LaunchConfigError):
        build_and_validate(path, draft.apply({}), tmp_path)

    assert 'id = "existing"' in path.read_text(encoding="utf-8")


def test_launch_draft_materializes_one_explicit_execution_route_per_account() -> None:
    values = LaunchDraft(
        launch_id="multi-account",
        mode="paper",
        strategy="builtin:interactive",
        accounts=("main", "hedge"),
        execution_enabled=True,
        execution_broker_id="simulated",
    ).apply({})

    assert values["execution"]["routes"] == [
        {
            "route_id": "main-spot",
            "account_id": "main",
            "segment_key": "spot",
            "broker_id": "simulated",
            "execution_channel": "spot",
        },
        {
            "route_id": "hedge-spot",
            "account_id": "hedge",
            "segment_key": "spot",
            "broker_id": "simulated",
            "execution_channel": "spot",
        },
    ]


def test_live_launch_draft_materializes_risk_and_execution_selection() -> None:
    values = LaunchDraft(
        launch_id="live",
        mode="live",
        strategy="builtin:interactive",
        accounts=("main",),
        execution_enabled=True,
        execution_broker_id="ibkr",
        execution_channel="equity",
        execution_segment_key="equity",
        risk_profile="production-conservative",
    ).apply({})

    assert values["risk"] == {"profile": "production-conservative"}
    assert values["execution"]["routes"][0] == {
        "route_id": "main-equity",
        "account_id": "main",
        "segment_key": "equity",
        "broker_id": "ibkr",
        "execution_channel": "equity",
    }


def test_launch_config_allows_zero_accounts(tmp_path: Path) -> None:
    config = tmp_path / "market-only.toml"
    config.write_text(
        """[launch]
id = "market-only"
mode = "paper"
strategy = "strategy:Factory"
""",
        encoding="utf-8",
    )

    assert LaunchConfigurationApplication().validate(config)["valid"] is True


def test_launch_environment_writes_normalized_config_inside_instance(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch"
    )
    accounts = AccountConfigurationApplication(workspace)
    accounts.simulate("paper-account")
    accounts.test_connection("paper-account")
    config = _write_config(tmp_path / "demo.toml")

    environment = LaunchConfigurationApplication().environment(
        config, workspace_root=workspace.paths.root, instance_id="one"
    )

    expected = (
        workspace.paths.root
        / "launches"
        / "paper"
        / "demo-launch"
        / "instances"
        / "one"
    )
    assert environment.instance_directory == expected
    normalized = json.loads(
        environment.normalized_config_path.read_text(encoding="utf-8")
    )
    assert normalized["launch"]["mode"] == "paper"
    assert normalized["snapshot_schema_version"] == 1
    assert (
        normalized["resource_snapshots"]["accounts"]["paper-account"]["verification"][
            "verification_status"
        ]
        == "verified"
    )
    assert normalized["resource_hashes"]["accounts:paper-account"]
    assert environment.process_environment["KAIROS_LAUNCH_INSTANCE_ID"] == "one"
    assert environment.process_environment["KAIROS_LAUNCH_NORMALIZED_CONFIG"] == str(
        environment.normalized_config_path
    )
    assert environment.process_environment["KAIROS_EXECUTION_DRY_RUN"] == "true"


def test_instance_resource_drift_uses_secret_ref_identity_not_secret_value(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="drift"
    )
    monkeypatch.setenv("KAIROS_PAPER_NOTE", "first-secret-value")
    credentials = CredentialConfigurationApplication(workspace)
    credentials.configure(
        "paper-credential",
        provider="paper",
        fields={"note": SecretRef("env", "KAIROS_PAPER_NOTE")},
    )
    accounts = AccountConfigurationApplication(workspace)
    accounts.connect(
        "paper-account",
        broker="paper",
        environment="paper",
        credential="paper-credential",
    )
    accounts.test_connection("paper-account")
    config = _write_config(workspace.paths.launch_config("demo-launch"))
    application = LaunchConfigurationApplication()
    environment = application.environment(
        config, workspace_root=workspace.paths.root, instance_id="one"
    )
    encoded = environment.normalized_config_path.read_text()
    assert "first-secret-value" not in encoded

    monkeypatch.setenv("KAIROS_PAPER_NOTE", "rotated-secret-value")
    assert application.instance_resource_drift(
        environment.normalized_config_path, workspace_root=workspace.paths.root
    ) == {"valid": True, "issues": []}

    monkeypatch.setenv("KAIROS_PAPER_NOTE_V2", "rotated-secret-value")
    credentials.configure(
        "paper-credential",
        provider="paper",
        fields={"note": SecretRef("env", "KAIROS_PAPER_NOTE_V2")},
        overwrite=True,
    )
    drift = application.instance_resource_drift(
        environment.normalized_config_path, workspace_root=workspace.paths.root
    )
    assert drift["valid"] is False
    assert drift["issues"][0]["resource"] == "accounts:paper-account"


def test_launch_configuration_persists_unready_draft_and_return_point(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="draft-return"
    )
    application = LaunchConfigurationApplication()
    values = {
        "launch": {
            "id": "live-draft",
            "mode": "live",
            "strategy": "builtin:interactive",
        },
        "accounts": {"main": {"ref": "missing", "enabled": True}},
        "execution": {"enabled": False},
        "risk": {"profile": "production-default"},
        "live": {"safety": {"trading_enabled": False}},
    }

    saved = application.save_draft(workspace.paths.root, "live-draft", values)
    return_point = application.record_draft_return(
        workspace.paths.root,
        "live-draft",
        resource="accounts",
        step="accounts",
    )

    assert saved["status"] == "draft"
    assert saved["ready"] is False
    assert "Account" in " ".join(saved["issues"])
    assert application.load_draft(workspace.paths.root, "live-draft") == values
    assert application.list_drafts(workspace.paths.root)[0]["launch_id"] == "live-draft"
    assert application.draft_return(workspace.paths.root, "live-draft") == return_point
    assert not workspace.paths.launch_config("live-draft").exists()

    application.clear_draft_return(workspace.paths.root, "live-draft")
    assert application.draft_return(workspace.paths.root, "live-draft") is None


def test_live_requires_live_table(tmp_path: Path) -> None:
    config = _write_config(tmp_path / "demo.toml", mode="live")
    config.write_text(
        "[launch]\nid = 'demo-launch'\nmode = 'live'\nstrategy = 'strategy:Factory'\n\n[account]\nref = 'live-account'\n",
        encoding="utf-8",
    )

    with pytest.raises(LaunchConfigError, match="live.*table is required"):
        LaunchConfigurationApplication().environment(config, workspace_root=tmp_path)


def test_backtest_requires_market_window(tmp_path: Path) -> None:
    config = tmp_path / "backtest.toml"
    config.write_text(
        '[launch]\nid = "backtest"\nmode = "backtest"\nstrategy = "strategy:Factory"\n\n'
        '[account]\nref = "simulated"\n\n[backtest]\n',
        encoding="utf-8",
    )

    report = LaunchConfigurationApplication().validate(config)
    assert report["valid"] is False
    assert "backtest.market" in " ".join(report["issues"])


def test_backtest_instance_does_not_parse_workspace_live_secrets(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="backtest-secret-isolation"
    )
    credential = workspace.paths.credential_config().parent / "broken-openai.toml"
    credential.parent.mkdir(parents=True, exist_ok=True)
    credential.write_text(
        '[credential]\nid = "broken-openai"\nprovider = "openai"\n'
        'api_key = "legacy-secret-must-not-enter-instance"\n',
        encoding="utf-8",
    )
    workspace.paths.notification_config().write_text(
        'version = 1\n[destinations.ops]\nsender = "telegram"\n'
        'bot_token = "legacy-notification-secret"\n',
        encoding="utf-8",
    )
    config = workspace.paths.launch_config("isolated-backtest")
    config.write_text(
        '[launch]\nid = "isolated-backtest"\nmode = "backtest"\n'
        'strategy = "builtin:interactive"\n\n'
        "[execution]\nenabled = false\n\n"
        "[agent]\nenabled = false\nrequired = false\n\n"
        "[notifications]\nenabled = false\nrequired = false\n\n"
        '[backtest.market]\nstart = "2024-01-01T00:00:00Z"\n'
        'end = "2024-01-02T00:00:00Z"\nevents = "data/events.jsonl"\n',
        encoding="utf-8",
    )

    environment = LaunchConfigurationApplication().environment(
        config, workspace_root=workspace.paths.root, instance_id="run-1"
    )
    normalized = json.loads(
        environment.normalized_config_path.read_text(encoding="utf-8")
    )

    assert normalized["resource_snapshots"] == {
        "accounts": {},
        "data_providers": {},
        "models": {},
        "notifications": {},
        "mcp_credentials": {},
    }
    encoded = json.dumps(normalized)
    assert "legacy-secret-must-not-enter-instance" not in encoded
    assert "legacy-notification-secret" not in encoded


def test_mode_plan_resolves_backtest_paths_and_defaults_execution(
    tmp_path: Path,
) -> None:
    config = tmp_path / "backtest.toml"
    config.write_text(
        '[launch]\nid = "backtest"\nmode = "backtest"\nstrategy = "strategy:Factory"\n\n'
        '[account]\nref = "simulated"\n\n'
        '[backtest]\ndata_root = "data"\nstorage_format = "jsonl"\n\n'
        '[backtest.market]\nstart = "2024-01-01T00:00:00Z"\nend = "2024-01-02T00:00:00Z"\n',
        encoding="utf-8",
    )

    plan = LaunchConfigurationApplication().plan(config)
    assert plan.backtest_data_root == (tmp_path / "data").resolve()
    assert plan.backtest_storage_format == "jsonl"
    assert plan.execution["dry_run"] is True


def test_toml_and_backtest_spec_share_one_canonical_launch_plan(tmp_path: Path) -> None:
    member = DatasetRef(
        dataset_id="market.quote/SPY-options",
        version="v1",
        content_hash="abc123",
        owner="market",
        kind="quote",
        subject="SPY-options",
        start_time_unix_nanos=1,
        end_time_unix_nanos=2,
        event_count=2,
        product="options",
        source="fixture",
    )
    dataset_set = DatasetSetRef((member,))
    config = tmp_path / "spy-put-spread.toml"
    config.write_text(
        f'''[launch]
id = "spy-put-spread"
mode = "backtest"
strategy = "spy_put_spread.strategy:SpyPutSpread"

[account]
ref = "spy-options-paper"

[risk]
profile = "options-conservative"

[backtest]
seed = 42

[backtest.option_constraints]
hold_through_expiry = false
assignment_enabled = false
exercise_enabled = false
naked_options_enabled = false
zero_dte_enabled = false
dynamic_delta_hedging_enabled = false
avoid_short_dividend_window = true
package_fill_required = true
maximum_open_spreads = 1
exit_before_expiry_days = 1
dividend_buffer_days = 1

[backtest.market]
start = "2025-01-01T00:00:00Z"
end = "2025-02-01T00:00:00Z"
scope = "instance"
profile = "replay"

[backtest.data]
composition_hash = "{dataset_set.composition_hash}"

[backtest.data.composition_policy]

[[backtest.data.members]]
dataset_id = "{member.dataset_id}"
version = "{member.version}"
content_hash = "{member.content_hash}"
owner = "{member.owner}"
kind = "{member.kind}"
subject = "{member.subject}"
start_time_unix_nanos = {member.start_time_unix_nanos}
end_time_unix_nanos = {member.end_time_unix_nanos}
event_count = {member.event_count}
schema_version = "{member.schema_version}"
product = "{member.product}"
source = "{member.source}"
quality_status = "{member.quality_status}"
''',
        encoding="utf-8",
    )
    typed = BacktestSpec(
        launch_id="spy-put-spread",
        strategy="spy_put_spread.strategy:SpyPutSpread",
        data=dataset_set,
        account="spy-options-paper",
        risk_profile="options-conservative",
        start="2025-01-01T00:00:00Z",
        end="2025-02-01T00:00:00Z",
        seed=42,
        option_constraints=OptionBacktestConstraints(),
    )
    application = LaunchConfigurationApplication()

    toml_plan = application.plan(config, workspace_root=tmp_path)
    typed_plan = typed.to_launch_config(workspace_root=tmp_path).plan()

    assert typed_plan == toml_plan
    assert typed_plan.normalized() == toml_plan.normalized()
    assert typed_plan.backtest_dataset_set == dataset_set
    assert typed_plan.backtest_seed == 42
    assert typed_plan.risk_profile == "options-conservative"
    constraints = typed_plan.mode_config["option_constraints"]
    assert constraints["package_fill_required"] is True
    assert constraints["hold_through_expiry"] is False


def test_option_backtest_constraints_reject_unsupported_silent_approximations(
    tmp_path: Path,
) -> None:
    config = tmp_path / "unsupported-option-semantics.toml"
    config.write_text(
        """[launch]
id = "unsupported-options"
mode = "backtest"
strategy = "strategy:Factory"

[backtest]

[backtest.market]
start = "2025-01-01T00:00:00Z"
end = "2025-01-02T00:00:00Z"

[backtest.option_constraints]
hold_through_expiry = true
""",
        encoding="utf-8",
    )

    report = LaunchConfigurationApplication().validate(config)

    assert report["valid"] is False
    assert "hold_through_expiry" in " ".join(report["issues"])
    with pytest.raises(ValueError, match="does not support"):
        OptionBacktestConstraints(zero_dte_enabled=True)
    assert "package semantics" in " ".join(OptionBacktestConstraints().limitations)


def test_cli_and_python_backtest_use_the_same_launch_runtime_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="shared-runtime"
    )
    config = workspace.paths.launch_config("cli-backtest")
    config.write_text(
        """[launch]
id = "cli-backtest"
mode = "backtest"
strategy = "strategy:Factory"

[backtest.market]
start = "2025-01-01T00:00:00Z"
end = "2025-01-02T00:00:00Z"
scope = "instance"
profile = "replay"
""",
        encoding="utf-8",
    )
    member = DatasetRef(
        dataset_id="market.quote/SPY-options",
        version="v1",
        content_hash="abc123",
        owner="market",
        kind="quote",
        subject="SPY-options",
        start_time_unix_nanos=1,
        end_time_unix_nanos=2,
        event_count=2,
    )
    spec = BacktestSpec(
        launch_id="python-backtest",
        strategy="strategy:Factory",
        data=DatasetSetRef((member,)),
        account="simulated",
        risk_profile="options-conservative",
        start="2025-01-01T00:00:00Z",
        end="2025-01-02T00:00:00Z",
        seed=42,
    )
    starts: list[tuple[str, str]] = []

    def fake_start(self, canonical, **_kwargs):
        starts.append((self.workspace.identity.workspace_id, canonical.launch_id))
        return {
            "status": "running",
            "launch_id": canonical.launch_id,
            "instance_id": f"instance-{len(starts)}",
            "normalized_config_hash": canonical.normalized_hash,
        }

    def fake_wait(self, launch_id, *, instance=None, timeout=3600.0):
        del self, timeout
        return {
            "status": "completed",
            "launch_id": launch_id,
            "instance_id": instance,
            "report": {"orders": 1},
        }

    monkeypatch.setattr(LaunchRuntimeApplication, "start", fake_start)
    monkeypatch.setattr(LaunchRuntimeApplication, "wait", fake_wait)

    output = StringIO()
    assert (
        execute_argv(
            [
                "launch",
                "start",
                "cli-backtest",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )
    result = asyncio.run(Kairos(workspace).run_backtest(spec, timeout=5))

    assert starts == [
        ("shared-runtime", "cli-backtest"),
        ("shared-runtime", "python-backtest"),
    ]
    assert json.loads(output.getvalue())["instance_id"] == "instance-1"
    assert result.instance_id == "instance-2"
    assert result.status == "completed"
    assert result.report == {"orders": 1}
    assert (
        result.normalized_config_hash
        == spec.to_launch_config(workspace_root=workspace.paths.root).normalized_hash
    )


def test_execution_routes_are_validated_without_inline_secrets(tmp_path: Path) -> None:
    config = _write_config(tmp_path / "routes.toml")
    config.write_text(
        config.read_text(encoding="utf-8")
        + """
[execution]

[[execution.routes]]
route_id = "binance-spot"
account_id = "paper-account"
segment_key = "spot"
broker_id = "binance"
execution_channel = "spot"
credential_id = "binance-main"

[[execution.routes]]
route_id = "okx-swap"
account_id = "paper-account"
segment_key = "swap"
broker_id = "okx"
execution_channel = "swap"
credential_id = "okx-main"
""",
        encoding="utf-8",
    )
    report = LaunchConfigurationApplication().validate(config)
    assert report["valid"] is True

    foreign_account = config.read_text(encoding="utf-8").replace(
        'account_id = "paper-account"', 'account_id = "outside"', 1
    )
    config.write_text(foreign_account, encoding="utf-8")
    report = LaunchConfigurationApplication().validate(config)
    assert report["valid"] is False
    assert any("not an enabled launch account" in issue for issue in report["issues"])

    config.write_text(
        foreign_account.replace(
            'account_id = "outside"', 'account_id = "paper-account"', 1
        ),
        encoding="utf-8",
    )

    forbidden = config.read_text(encoding="utf-8").replace(
        'credential_id = "okx-main"', 'api_key = "must-not-be-here"'
    )
    config.write_text(forbidden, encoding="utf-8")
    report = LaunchConfigurationApplication().validate(config)
    assert report["valid"] is False
    assert "use credential_id" in " ".join(report["issues"])


def test_backtest_environment_serializes_resolved_paths(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="backtest"
    )
    config = workspace.paths.launch_config("backtest")
    config.write_text(
        """[launch]
id = "backtest"
mode = "backtest"
strategy = "strategy:Factory"

[backtest]
data_root = "data"

[backtest.market]
start = "2024-01-01T00:00:00Z"
end = "2024-01-02T00:00:00Z"
events = "data/events.jsonl"
""",
        encoding="utf-8",
    )

    environment = LaunchConfigurationApplication().environment(
        config, workspace_root=workspace.paths.root, instance_id="run"
    )
    normalized = json.loads(
        environment.normalized_config_path.read_text(encoding="utf-8")
    )

    assert normalized["backtest_data_root"] == str(workspace.paths.root / "data")
    assert normalized["backtest_replay_file"] == str(
        workspace.paths.root / "data" / "events.jsonl"
    )


def test_backtest_plan_resolves_instance_replay_source(tmp_path: Path) -> None:
    config = tmp_path / "backtest.toml"
    config.write_text(
        """[launch]
id = "backtest"
mode = "backtest"
strategy = "strategy:Factory"

[account]
ref = "simulated"

[backtest]
data_root = "data"

[backtest.market]
start = "2024-01-01T00:00:00Z"
end = "2024-01-02T00:00:00Z"
events = "data/events.jsonl"
""",
        encoding="utf-8",
    )
    plan = LaunchConfigurationApplication().load(config, workspace_root=tmp_path).plan()
    assert plan.backtest_replay_file == (tmp_path / "data/events.jsonl").resolve()


def test_live_market_scope_defaults_shared_and_can_be_instance_local(
    tmp_path: Path,
) -> None:
    config = tmp_path / "live.toml"
    config.write_text(
        '[launch]\nid = "live"\nmode = "live"\nstrategy = "strategy:Factory"\n\n'
        '[account]\nref = "live-account"\n\n'
        "[execution]\nenabled = false\n\n"
        '[risk]\nprofile = "live-default"\n\n'
        '[live.market]\nscope = "instance"\n\n'
        "[live.safety]\ntrading_enabled = false\n",
        encoding="utf-8",
    )
    plan = LaunchConfigurationApplication().load(config, workspace_root=tmp_path).plan()
    assert plan.market_scope == "instance"
    config.write_text(
        config.read_text(encoding="utf-8").replace('scope = "instance"\n', ""),
        encoding="utf-8",
    )
    assert (
        LaunchConfigurationApplication()
        .load(config, workspace_root=tmp_path)
        .plan()
        .market_scope
        == "shared"
    )


def test_live_launch_requires_non_simulation_risk_profile(tmp_path: Path) -> None:
    config = tmp_path / "live-risk.toml"
    config.write_text(
        '[launch]\nid = "live-risk"\nmode = "live"\nstrategy = "strategy:Factory"\n\n'
        "[execution]\nenabled = false\n\n"
        "[live.safety]\ntrading_enabled = false\n",
        encoding="utf-8",
    )
    report = LaunchConfigurationApplication().validate(config)
    assert report["valid"] is False
    assert "risk.profile is required for live launches" in report["issues"]

    config.write_text(
        config.read_text(encoding="utf-8")
        + '\n[risk]\nprofile = "simulation-default"\n',
        encoding="utf-8",
    )
    report = LaunchConfigurationApplication().validate(config)
    assert report["valid"] is False
    assert any("forbidden for live" in issue for issue in report["issues"])


def test_replay_market_cannot_use_shared_scope(tmp_path: Path) -> None:
    config = tmp_path / "replay.toml"
    config.write_text(
        '[launch]\nid = "replay"\nmode = "paper"\nstrategy = "strategy:Factory"\n\n'
        '[account]\nref = "paper-account"\n\n'
        '[paper]\nevents = "events.jsonl"\n\n'
        '[paper.market]\nscope = "shared"\n',
        encoding="utf-8",
    )
    report = LaunchConfigurationApplication().validate(config, workspace_root=tmp_path)
    assert report["valid"] is False
    assert "paper.market.scope must be instance" in " ".join(report["issues"])


def test_launch_diagnose_reads_workspace_launch_toml(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch"
    )
    accounts = AccountConfigurationApplication(workspace)
    accounts.simulate("paper-account")
    accounts.test_connection("paper-account")
    config_dir = workspace.paths.config / "launches"
    config_dir.mkdir(parents=True, exist_ok=True)
    _write_config(config_dir / "demo-launch.toml")
    output = StringIO()

    assert (
        execute_argv(
            [
                "launch",
                "diagnose",
                "validate",
                "demo-launch",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        == 0
    )
    assert "valid: true" in output.getvalue()


def test_launch_id_resolves_workspace_owned_config_without_explicit_path(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch"
    )
    config = workspace.paths.launch_config("demo-launch")
    _write_config(config)

    assert _launch_config_path(workspace, "demo-launch") == config
