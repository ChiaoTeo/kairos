from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from pathlib import Path

import pytest

from kairospy.contracts.market import MarketControlUnavailableError
from kairospy.system.apps.launch.application.configuration import (
    LaunchConfigError,
    LaunchConfigurationApplication,
)
from kairospy.strategy.apps.notification.composition import (
    NotificationConfigError,
    compose_notifications,
    notification_config_hash,
    validate_notification_resources,
)
from kairospy.strategy.apps.notification.application import NotificationAdminApplication
from kairospy.system.apps.launch.composition import compose_strategy_process
from kairospy.system.apps.components.application.event_routes import (
    ensure_instance_event_route,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.strategy import StrategyIdentity, StrategyLogger
from kairospy.strategy import ImmediateAlgorithm, InstrumentId


def _workspace(tmp_path: Path):
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="ws")
    workspace.paths.notification_config().write_text(
        """version = 1

[destinations.feishu-options]
sender = "feishu"
credential_id = "feishu-options"

[destinations.telegram-personal]
sender = "telegram"
credential_id = "telegram-options"
chat_id = "-10042"
""",
        encoding="utf-8",
    )
    (workspace.paths.credentials_root() / "feishu-options.toml").write_text(
        """[credential]
id = "feishu-options"
provider = "feishu"

[credential.values]
webhook_url = "https://open.feishu.cn/open-apis/bot/v2/hook/feishu-test-token"
""",
        encoding="utf-8",
    )
    (workspace.paths.credentials_root() / "telegram-options.toml").write_text(
        """[credential]
id = "telegram-options"
provider = "telegram"

[credential.values]
bot_token = "123456:test-token"
""",
        encoding="utf-8",
    )
    return workspace


def _config() -> dict[str, object]:
    return {
        "enabled": True,
        "required": True,
        "default_routes": ["signals"],
        "queue_capacity": 16,
        "shutdown_grace_seconds": 1,
        "routes": {
            "signals": ["feishu-options", "telegram-personal"],
        },
    }


def test_real_notification_test_is_unambiguously_labeled(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = _workspace(tmp_path)
    published: dict[str, object] = {}

    class Runtime:
        def __init__(self, **_values) -> None:
            pass

        async def start(self) -> None:
            pass

        async def flush(self, *, timeout: float) -> None:
            assert timeout == 10

        async def close(self) -> None:
            pass

    class Application:
        def __init__(self, _runtime) -> None:
            pass

        def publish(self, **values):
            published.update(values)
            return type(
                "Receipt", (), {"notification_id": "test", "status": "queued"}
            )()

        def health(self):
            return {"status": "ready"}

    monkeypatch.setattr(
        "kairospy.strategy.apps.notification.application.admin.NotificationDeliveryRuntime",
        Runtime,
    )
    monkeypatch.setattr(
        "kairospy.strategy.apps.notification.application.admin.NotificationApplication",
        Application,
    )
    monkeypatch.setattr(
        "kairospy.strategy.apps.notification.application.admin.AppriseSender",
        lambda _destination: object(),
    )

    asyncio.run(
        NotificationAdminApplication(workspace).test_destination("telegram-personal")
    )

    assert published["title"] == "Kairos 测试通知"
    assert "Kairos 测试" in str(published["body"])
    assert "Workspace ws" in str(published["body"])
    assert "发送时间" in str(published["body"])


def test_workspace_resources_resolve_without_exposing_secrets(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    composition = compose_notifications(
        workspace=workspace,
        instance=workspace.instance("paper", "launch", "instance"),
        identity=StrategyIdentity("strategy", "launch", "instance"),
        mode="paper",
        config=_config(),
        logger=StrategyLogger(),
    )

    assert composition.config_hash
    assert composition.issues == ()
    assert composition.application.health()["state"] == "healthy"
    assert "test-token" not in repr(composition)


def test_required_and_degraded_resource_behavior(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace.paths.credentials_root() / "telegram-options.toml").unlink()
    with pytest.raises(NotificationConfigError, match="credential not found"):
        compose_notifications(
            workspace=workspace,
            instance=workspace.instance("paper", "launch", "required"),
            identity=StrategyIdentity("strategy", "launch", "required"),
            mode="paper",
            config=_config(),
            logger=StrategyLogger(),
        )

    config = _config() | {"required": False}
    composition = compose_notifications(
        workspace=workspace,
        instance=workspace.instance("paper", "launch", "degraded"),
        identity=StrategyIdentity("strategy", "launch", "degraded"),
        mode="paper",
        config=config,
        logger=StrategyLogger(),
    )
    assert composition.issues
    assert composition.application.health()["state"] == "degraded"


def test_feishu_signing_is_rejected_until_the_component_supports_it(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    credential = workspace.paths.credentials_root() / "feishu-options.toml"
    credential.write_text(
        credential.read_text(encoding="utf-8") + 'signing_secret = "secret"\n',
        encoding="utf-8",
    )
    with pytest.raises(
        NotificationConfigError, match="Apprise adapter does not support"
    ):
        compose_notifications(
            workspace=workspace,
            instance=workspace.instance("paper", "launch", "signed"),
            identity=StrategyIdentity("strategy", "launch", "signed"),
            mode="paper",
            config=_config(),
            logger=StrategyLogger(),
        )


def test_backtest_records_without_resolving_credentials(tmp_path: Path) -> None:
    async def scenario() -> tuple[dict[str, object], Path]:
        workspace = _workspace(tmp_path)
        for credential in workspace.paths.credentials_root().glob("*.toml"):
            credential.unlink()
        instance = workspace.instance("backtest", "launch", "instance")
        composition = compose_notifications(
            workspace=workspace,
            instance=instance,
            identity=StrategyIdentity("strategy", "launch", "instance"),
            mode="backtest",
            config=_config(),
            logger=StrategyLogger(),
        )
        await composition.runtime.start()
        composition.application.publish(title="signal", body="body")
        await composition.runtime.flush(timeout=1)
        health = composition.application.health()
        await composition.runtime.close()
        return health, instance.artifact("notifications.jsonl")

    health, artifact = asyncio.run(scenario())
    assert health["delivered_total"] == 2
    records = [json.loads(line) for line in artifact.read_text().splitlines()]
    assert {record["destination_id"] for record in records} == {
        "feishu-options",
        "telegram-personal",
    }


def test_launch_normalizes_notifications_and_validates_workspace(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    notifications = NotificationAdminApplication(workspace)
    notifications.record_test("feishu-options", succeeded=True)
    notifications.record_test("telegram-personal", succeeded=True)
    launch = workspace.paths.config / "launches" / "signals.toml"
    launch.write_text(
        """[launch]
id = "signals"
mode = "paper"
strategy = "strategies.signals:Strategy"

[execution]
enabled = false

[notifications]
enabled = true
required = true
default_routes = ["signals"]
queue_capacity = 32

[notifications.routes]
signals = ["feishu-options", "telegram-personal"]
""",
        encoding="utf-8",
    )
    application = LaunchConfigurationApplication()
    report = application.validate(launch, workspace_root=workspace.paths.root)
    assert report == {
        "path": str(launch.resolve()),
        "valid": True,
        "issues": [],
        "warnings": [],
        "diagnostics": [],
    }

    environment = application.environment(
        launch, workspace_root=workspace.paths.root, instance_id="one"
    )
    normalized = json.loads(environment.normalized_config_path.read_text())
    assert normalized["notifications"] == {
        "enabled": True,
        "required": True,
        "default_routes": ["signals"],
        "queue_capacity": 32,
        "shutdown_grace_seconds": 5,
        "routes": {
            "signals": ["feishu-options", "telegram-personal"],
        },
        "workspace_config_hash": notification_config_hash(workspace),
    }
    assert "test-token" not in environment.normalized_config_path.read_text()


def test_optional_notification_failure_is_warning_with_explicit_degradation(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="n")
    launch = workspace.paths.launch_config("optional-notification")
    launch.parent.mkdir(parents=True, exist_ok=True)
    launch.write_text(
        '[launch]\nid = "optional-notification"\nmode = "paper"\n'
        'strategy = "builtin:interactive"\n\n'
        "[execution]\nenabled = false\n\n"
        "[notifications]\nenabled = true\nrequired = false\n"
        'default_routes = ["signals"]\n\n'
        '[notifications.routes]\nsignals = ["missing-destination"]\n',
        encoding="utf-8",
    )
    application = LaunchConfigurationApplication()

    report = application.validate(launch, workspace_root=workspace.paths.root)

    assert report["valid"] is True
    assert report["issues"] == []
    assert any("missing-destination" in warning for warning in report["warnings"])
    assert report["diagnostics"][0]["severity"] == "warning"
    assert "no delivery" in report["diagnostics"][0]["action"]
    environment = application.environment(
        launch, workspace_root=workspace.paths.root, instance_id="run-1"
    )
    normalized = json.loads(
        environment.normalized_config_path.read_text(encoding="utf-8")
    )
    assert normalized["resource_snapshots"]["notifications"] == {}
    assert normalized["readiness_diagnostics"][0]["severity"] == "warning"


def test_launch_rejects_inline_notification_secret(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    launch = workspace.paths.config / "launches" / "bad.toml"
    launch.write_text(
        """[launch]
id = "bad"
mode = "paper"
strategy = "strategies.signals:Strategy"

[execution]
enabled = false

[notifications]
enabled = true
webhook_url = "https://secret.example"

[notifications.routes]
signals = ["feishu-options"]
""",
        encoding="utf-8",
    )
    report = LaunchConfigurationApplication().validate(
        launch, workspace_root=workspace.paths.root
    )
    assert not report["valid"]
    assert "forbidden" in " ".join(report["issues"])


def test_notification_fanout_has_a_configuration_bound(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    config = _config() | {
        "routes": {"signals": [f"destination-{index}" for index in range(65)]}
    }
    with pytest.raises(NotificationConfigError, match="at most 64"):
        compose_notifications(
            workspace=workspace,
            instance=workspace.instance("backtest", "launch", "bounded"),
            identity=StrategyIdentity("strategy", "launch", "bounded"),
            mode="backtest",
            config=config,
            logger=StrategyLogger(),
        )


def test_static_resource_validation_does_not_require_backtest_secrets(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    for credential in workspace.paths.credentials_root().glob("*.toml"):
        credential.unlink()
    assert (
        validate_notification_resources(
            workspace, _config(), mode="backtest", resolve_secrets=False
        )
        == ()
    )
    issues = validate_notification_resources(
        workspace, _config(), mode="paper", resolve_secrets=False
    )
    assert "notification credential not found" in " ".join(issues)


def test_workspace_validation_rejects_provider_configuration_apprise_cannot_use(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    credential = workspace.paths.credentials_root() / "feishu-options.toml"
    credential.write_text(
        """[credential]
id = "feishu-options"
provider = "feishu"

[credential.values]
webhook_url = "https://example.test/not-a-feishu-hook"
""",
        encoding="utf-8",
    )
    issues = validate_notification_resources(
        workspace, _config(), mode="paper", resolve_secrets=True
    )
    assert "official custom-bot webhook" in " ".join(issues)


def test_strategy_context_records_start_and_end_notifications_without_execution(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[list[dict[str, object]], object]:
        workspace = _workspace(tmp_path)
        (workspace.paths.root / "signal_strategy.py").write_text(
            "from kairospy.strategy import Strategy\n"
            "class SignalStrategy(Strategy):\n"
            "    strategy_id = 'signal-strategy'\n"
            "    def on_start(self, ctx):\n"
            "        ctx.notifications.publish(title='started', body='ready')\n"
            "    def on_end(self, ctx):\n"
            "        ctx.notifications.publish(title='stopped', body='done')\n",
            encoding="utf-8",
        )
        launch = workspace.paths.config / "launches" / "signal-backtest.toml"
        launch.write_text(
            """[launch]
id = "signal-backtest"
mode = "backtest"
strategy = "signal_strategy:SignalStrategy"

[execution]
enabled = false

[backtest]
storage_format = "jsonl"

[backtest.market]
start = "2026-08-18T00:00:00Z"
end = "2026-08-18T00:01:00Z"
scope = "instance"

[notifications]
enabled = true
required = true
default_routes = ["signals"]

[notifications.routes]
signals = ["feishu-options", "telegram-personal"]
""",
            encoding="utf-8",
        )
        environment = LaunchConfigurationApplication().environment(
            launch, workspace_root=workspace.paths.root, instance_id="one"
        )
        instance = workspace.instance("backtest", "signal-backtest", "one")
        instance_route = ensure_instance_event_route(instance)
        instance.component_manifest().write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "workspace_id": workspace.workspace_id,
                    "launch_id": "signal-backtest",
                    "instance_id": "one",
                    "mode": "backtest",
                    "event_routes": {
                        instance_route.route_id: instance_route.as_manifest(),
                    },
                    "components": {
                        "market": {
                            "socket": str(instance.socket("market")),
                            "event_route": instance_route.route_id,
                        }
                    },
                    "accounts": {},
                }
            ),
            encoding="utf-8",
        )
        composition = compose_strategy_process(
            workspace,
            strategy_ref="signal_strategy:SignalStrategy",
            launch_id="signal-backtest",
            instance_id="one",
            mode="backtest",
        )
        await composition.notifications.runtime.start()
        try:
            composition.application.start()
            composition.application.enable()
            try:
                composition.application.stop()
            except (FileNotFoundError, MarketControlUnavailableError):
                # This focused composition has no running Market control socket;
                # on_end has already run before owner-release cleanup is attempted.
                pass
            await composition.notifications.runtime.flush(timeout=1)
        finally:
            await composition.notifications.runtime.close()
        records = [
            json.loads(line)
            for line in instance.artifact("notifications.jsonl")
            .read_text()
            .splitlines()
        ]
        return records, composition.application.context.execution

    records, execution = asyncio.run(scenario())
    assert [record["title"] for record in records] == [
        "started",
        "started",
        "stopped",
        "stopped",
    ]
    assert {record["occurred_at"] for record in records} == {
        "2026-08-18T00:00:00+00:00"
    }
    rejected = execution.target_position(
        InstrumentId("instrument:test:SPY"),
        Decimal("1"),
        account="main",
        algorithm=ImmediateAlgorithm(),
    )
    assert rejected.status == "rejected"
    assert rejected.error == "execution is disabled for this launch"
