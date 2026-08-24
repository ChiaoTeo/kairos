from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
import stat
import tomllib

import pytest

from kairospy.application.launch.application import (
    LaunchConfigurationApplication,
    LaunchNotificationConfigurationApplication,
)
from kairospy.application.notification import (
    NotificationAdminApplication,
    NotificationSecretRef,
)
from kairospy.application.notification.composition import (
    NotificationConfigError,
    compose_notifications,
    notification_config_hash,
    validate_notification_resources,
)
from kairospy.application.notification.services.setup import TelegramSetupClient
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli.notification_setup import run_notification_setup
from kairospy.surface.cli import execute_argv
from kairospy.surface.cli.options import OutputFormat
from kairospy.strategy import StrategyIdentity, StrategyLogger


def test_admin_persists_secret_ref_without_secret(tmp_path: Path, monkeypatch) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="n"
    )
    monkeypatch.setenv(
        "KAIROS_CREDENTIAL_FEISHU_ALERTS_WEBHOOK_URL",
        "https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
    )
    application = NotificationAdminApplication(workspace)

    result = application.configure(
        "feishu-alerts",
        provider="feishu",
        credential_id="feishu-alerts",
        secret_ref=NotificationSecretRef(
            "env", "KAIROS_CREDENTIAL_FEISHU_ALERTS_WEBHOOK_URL"
        ),
    )

    credential_path = workspace.paths.credential_config().parent / "feishu-alerts.toml"
    raw = credential_path.read_text(encoding="utf-8")
    credential = tomllib.loads(raw)["credential"]
    assert credential["fields"]["webhook_url"] == {
        "source": "env",
        "id": "KAIROS_CREDENTIAL_FEISHU_ALERTS_WEBHOOK_URL",
    }
    assert "test-token" not in raw
    assert stat.S_IMODE(credential_path.stat().st_mode) == 0o600
    assert result["configured"] is True
    assert result["verification_status"] == "pending"
    assert result["secret_available"] is True


def test_file_secret_ref_and_disabled_destination_validation(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="n")
    secret_path = workspace.paths.root / "telegram-token"
    secret_path.write_text("123456:test-token\n", encoding="utf-8")
    application = NotificationAdminApplication(workspace)
    application.configure(
        "telegram-ops",
        provider="telegram",
        secret_ref=NotificationSecretRef("file", "telegram-token"),
        chat_id="-10042",
    )
    assert application.show("telegram-ops")["configured"] is True

    application.set_enabled("telegram-ops", False)
    issues = validate_notification_resources(
        workspace,
        {
            "enabled": True,
            "required": True,
            "default_routes": ["signals"],
            "routes": {"signals": ["telegram-ops"]},
        },
        mode="paper",
    )
    assert issues == ("notification destination is disabled: telegram-ops",)


def test_structured_secret_ref_is_authoritative(tmp_path: Path, monkeypatch) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="n")
    application = NotificationAdminApplication(workspace)
    application.configure(
        "feishu-alerts",
        provider="feishu",
        secret_ref=NotificationSecretRef("env", "CUSTOM_FEISHU_WEBHOOK"),
    )
    monkeypatch.setenv(
        "KAIROS_CREDENTIAL_FEISHU_ALERTS_WEBHOOK_URL",
        "https://open.feishu.cn/open-apis/bot/v2/hook/derived-must-not-win",
    )
    issues = validate_notification_resources(
        workspace,
        {
            "enabled": True,
            "required": True,
            "default_routes": ["signals"],
            "routes": {"signals": ["feishu-alerts"]},
        },
        mode="paper",
        resolve_secrets=True,
    )
    assert issues == ("notification credential feishu-alerts is missing webhook_url",)


def test_destination_write_failure_rolls_back_credential(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="n")
    credential = workspace.paths.credential_config().parent / "feishu-alerts.toml"
    credential.write_text(
        '[credential]\nid = "feishu-alerts"\nprovider = "feishu"\nrole = "old"\n',
        encoding="utf-8",
    )
    previous = credential.read_text(encoding="utf-8")
    application = NotificationAdminApplication(workspace)

    def fail(_self, _records):
        raise OSError("simulated destination commit failure")

    monkeypatch.setattr(NotificationAdminApplication, "_write_destinations", fail)
    with pytest.raises(OSError, match="simulated"):
        application.configure(
            "feishu-alerts",
            provider="feishu",
            secret_ref=NotificationSecretRef("env", "FEISHU_WEBHOOK"),
        )
    assert credential.read_text(encoding="utf-8") == previous


def test_launch_attachment_is_owner_validated_and_instance_pins_hash(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="n")
    monkeypatch.setenv(
        "KAIROS_CREDENTIAL_FEISHU_ALERTS_WEBHOOK_URL",
        "https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
    )
    notifications = NotificationAdminApplication(workspace)
    notifications.configure(
        "feishu-alerts",
        provider="feishu",
        secret_ref=NotificationSecretRef(
            "env", "KAIROS_CREDENTIAL_FEISHU_ALERTS_WEBHOOK_URL"
        ),
    )
    notifications.record_test("feishu-alerts", succeeded=True)
    launch = workspace.paths.launch_config("signals")
    launch.write_text(
        """[launch]
id = "signals"
mode = "paper"
strategy = "builtin:interactive"

[execution]
enabled = false
""",
        encoding="utf-8",
    )
    routes = LaunchNotificationConfigurationApplication(workspace)
    routes.attach(
        "signals",
        "feishu-alerts",
        route="signals",
        default=True,
        lifecycle=True,
    )
    assert routes.references_to("feishu-alerts")[0]["routes"] == ["signals"]

    output = StringIO()
    assert (
        execute_argv(
            [
                "notifications",
                "list",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )
    listed = json.loads(output.getvalue())
    assert listed[0]["launch_references"] == [
        {
            "source": "config/launches/signals.toml",
            "location": "notifications.routes.signals",
        }
    ]

    output = StringIO()
    assert (
        execute_argv(
            [
                "notifications",
                "delete",
                "feishu-alerts",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        != 0
    )
    assert "referenced by Launch routes" in output.getvalue()

    environment = LaunchConfigurationApplication().environment(
        launch, workspace_root=workspace.paths.root, instance_id="one"
    )
    normalized = json.loads(environment.normalized_config_path.read_text())
    assert normalized["notifications"]["workspace_config_hash"] == (
        notification_config_hash(workspace, mode="paper")
    )
    assert normalized["notifications"]["lifecycle_routes"] == ["signals"]
    assert "test-token" not in environment.normalized_config_path.read_text()

    workspace.paths.notification_config().write_text(
        workspace.paths.notification_config().read_text() + "\n# changed\n"
    )
    with pytest.raises(
        NotificationConfigError, match="changed after this Launch instance"
    ):
        compose_notifications(
            workspace=workspace,
            instance=workspace.instance("paper", "signals", "one"),
            identity=StrategyIdentity("signals", "signals", "one"),
            mode="paper",
            config=normalized["notifications"],
            logger=StrategyLogger(),
        )


def test_telegram_setup_client_validates_bot_and_discovers_unique_chats(
    monkeypatch,
) -> None:
    class _Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return json.dumps(self.payload).encode()

    def open_request(request, timeout):
        del timeout
        if request.full_url.endswith("/getMe"):
            return _Response(
                {
                    "ok": True,
                    "result": {
                        "id": 7,
                        "username": "kairos_bot",
                        "first_name": "Kairos",
                    },
                }
            )
        return _Response(
            {
                "ok": True,
                "result": [
                    {
                        "message": {
                            "chat": {"id": -1001, "type": "group", "title": "Ops"}
                        }
                    },
                    {
                        "channel_post": {
                            "chat": {"id": -1001, "type": "group", "title": "Ops"}
                        }
                    },
                    {
                        "message": {
                            "chat": {"id": 42, "type": "private", "first_name": "Ada"}
                        }
                    },
                ],
            }
        )

    monkeypatch.setattr(
        "kairospy.application.notification.services.setup.urlopen", open_request
    )
    client = TelegramSetupClient("123456:test-token")
    assert client.identity().username == "kairos_bot"
    assert [(chat.chat_id, chat.title) for chat in client.chats()] == [
        ("-1001", "Ops"),
        ("42", "Ada"),
    ]


def test_guided_feishu_setup_uses_secret_ref_and_never_prompts_for_secret(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="n"
    )
    env_name = "KAIROS_CREDENTIAL_FEISHU_ALERTS_WEBHOOK_URL"
    monkeypatch.setenv(
        env_name, "https://open.feishu.cn/open-apis/bot/v2/hook/test-token"
    )
    answers = iter(("feishu-alerts", "feishu-alerts", "1", env_name))
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))
    confirmations = iter((True, False))
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: next(confirmations))

    result = run_notification_setup(
        workspace, provider="feishu", output=OutputFormat.JSON
    )

    assert result["configured"] is True
    assert result["secret_ref"] == {"source": "env", "id": env_name}
    assert result["next_action"] == ("select this Destination while editing a Launch")
    assert "launch_attachment" not in result
    assert "test-token" not in json.dumps(result)


def test_guided_notification_setup_can_cancel_before_persisting(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="n"
    )
    answers = iter(("feishu-alerts", "feishu-alerts", "1", "FEISHU_WEBHOOK"))
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr("typer.confirm", lambda *args, **kwargs: False)

    result = run_notification_setup(
        workspace, provider="feishu", output=OutputFormat.JSON
    )

    assert result["status"] == "cancelled"
    with pytest.raises(KeyError):
        NotificationAdminApplication(workspace).show("feishu-alerts")


def test_manual_delivery_evidence_is_invalidated_by_destination_change(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="n"
    )
    monkeypatch.setenv("KAIROS_CREDENTIAL_TELEGRAM_OPS_BOT_TOKEN", "12345:test-token")
    application = NotificationAdminApplication(workspace)
    reference = NotificationSecretRef("env", "KAIROS_CREDENTIAL_TELEGRAM_OPS_BOT_TOKEN")
    application.configure(
        "telegram-ops",
        provider="telegram",
        secret_ref=reference,
        chat_id="100",
    )

    verified = application.record_test("telegram-ops", succeeded=True)
    changed = application.configure(
        "telegram-ops",
        provider="telegram",
        secret_ref=reference,
        chat_id="200",
    )

    assert verified["verification_status"] == "verified"
    assert verified["last_tested_at"]
    assert changed["verification_status"] == "retest_required"
