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
    load_values,
)
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli import execute_argv
from kairospy.surface.cli.commands.launch import _launch_config_path
from io import StringIO


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


def test_launch_draft_preserves_advanced_values_and_writes_valid_toml(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manual.toml"
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

    report = build_and_validate(path, updated, tmp_path)

    assert report["valid"] is True
    values = load_values(path)
    assert values["launch"]["strategy"] == "builtin:interactive"
    assert values["strategy"]["params"] == {"symbol": "BTCUSDT"}
    assert values["paper"]["events"] == "events.jsonl"
    assert values["accounts"]["account_2"]["ref"] == "secondary"


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
        execution_participant_id="simulated",
    ).apply({})

    assert values["execution"]["routes"] == [
        {
            "route_id": "main-spot",
            "account_id": "main",
            "segment_key": "spot",
            "participant_id": "simulated",
            "product": "spot",
        },
        {
            "route_id": "hedge-spot",
            "account_id": "hedge",
            "segment_key": "spot",
            "participant_id": "simulated",
            "product": "spot",
        },
    ]


def test_live_launch_draft_materializes_risk_and_execution_selection() -> None:
    values = LaunchDraft(
        launch_id="live",
        mode="live",
        strategy="builtin:interactive",
        accounts=("main",),
        execution_enabled=True,
        execution_participant_id="ibkr",
        execution_product="equity",
        execution_segment_key="equity",
        risk_profile="production-conservative",
    ).apply({})

    assert values["risk"] == {"profile": "production-conservative"}
    assert values["execution"]["routes"][0] == {
        "route_id": "main-equity",
        "account_id": "main",
        "segment_key": "equity",
        "participant_id": "ibkr",
        "product": "equity",
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
    assert environment.process_environment["KAIROS_LAUNCH_INSTANCE_ID"] == "one"
    assert environment.process_environment["KAIROS_LAUNCH_NORMALIZED_CONFIG"] == str(
        environment.normalized_config_path
    )
    assert environment.process_environment["KAIROS_EXECUTION_DRY_RUN"] == "true"


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
participant_id = "binance"
product = "spot"
credential_id = "binance-main"

[[execution.routes]]
route_id = "okx-swap"
account_id = "paper-account"
segment_key = "swap"
participant_id = "okx"
product = "swap"
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
        foreign_account.replace('account_id = "outside"', 'account_id = "paper-account"', 1),
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
        '[execution]\nenabled = false\n\n'
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
        '[execution]\nenabled = false\n\n'
        '[live.safety]\ntrading_enabled = false\n',
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
    assert '"valid": true' in output.getvalue()


def test_launch_id_resolves_workspace_owned_config_without_explicit_path(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch"
    )
    config = workspace.paths.launch_config("demo-launch")
    _write_config(config)

    assert _launch_config_path(workspace, "demo-launch") == config
