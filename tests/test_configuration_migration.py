from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from kairospy.application.config import ConfigurationMigrationApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli import execute_argv
from kairospy.surface.cli.interactive.models import InteractiveContext
from kairospy.surface.cli.interactive.sections.getting_started import home


def test_migration_preview_finds_legacy_formats_without_exposing_values(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="m")
    credential = workspace.paths.credential_config().parent / "legacy-openai.toml"
    credential.parent.mkdir(parents=True, exist_ok=True)
    credential.write_text(
        '[credential]\nid = "legacy-openai"\nprovider = "openai"\n'
        'api_key = "old-secret-must-not-render"\n',
        encoding="utf-8",
    )
    notifications = workspace.paths.notification_config()
    notifications.write_text(
        'version = 1\n[destinations.ops]\nsender = "telegram"\n'
        'bot_token = "old-token-must-not-render"\n',
        encoding="utf-8",
    )
    profile = workspace.paths.agent_profiles_root() / "legacy.toml"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        '[profile]\nid = "legacy"\ngoal = "review"\nversion = "1"\n'
        'rubric = ["bounded"]\ninvalidation_rules = ["missing"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MASSIVE_API_KEY", "massive-secret-must-not-render")

    preview = ConfigurationMigrationApplication(workspace).preview()

    assert preview["migration_count"] == 4
    assert preview["preview_only"] is True
    assert preview["writes_performed"] is False
    kinds = {item["kind"] for item in preview["items"]}
    assert kinds == {
        "plaintext_credential",
        "inline_notification_secret",
        "workspace_agent_profiles",
        "legacy_massive_environment",
    }
    encoded = json.dumps(preview, ensure_ascii=False)
    assert "old-secret-must-not-render" not in encoded
    assert "old-token-must-not-render" not in encoded
    assert "massive-secret-must-not-render" not in encoded


def test_migrate_command_and_home_banner_expose_preview_only(
    tmp_path: Path, capsys
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="m")
    credential = workspace.paths.credential_config().parent / "legacy.toml"
    credential.parent.mkdir(parents=True, exist_ok=True)
    credential.write_text(
        '[credential]\nid = "legacy"\nprovider = "openai"\napi_key = "hidden"\n',
        encoding="utf-8",
    )
    output = StringIO()

    assert (
        execute_argv(
            [
                "config",
                "migrate",
                "--workspace",
                str(workspace.paths.root),
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )
    assert json.loads(output.getvalue())["preview_only"] is True
    assert "hidden" not in output.getvalue()

    context = InteractiveContext(owner=workspace, snapshot=None, workspace_arg=None)
    home.print_menu(context)
    text = capsys.readouterr().out
    assert "├─ 提示" in text
    assert "│  有 1 项配置可升级，不影响当前使用。输入 migrate 查看" in text
    command = home.handle(context, ("migrate",))
    assert command is not None
    assert command.argv == ("config", "migrate")
