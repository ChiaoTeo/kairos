from __future__ import annotations

from pathlib import Path
import shutil
import tomllib

import pytest

from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication


def _application(tmp_path: Path) -> CredentialConfigurationApplication:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="demo")
    return CredentialConfigurationApplication(workspace)


def test_credential_values_are_stored_in_one_private_toml(tmp_path: Path) -> None:
    application = _application(tmp_path)

    summary = application.configure(
        "okx-trade",
        provider="okx",
        role="trade",
        values={
            "api_key": "key-one",
            "api_secret": "secret-one",
            "passphrase": "phrase-one",
        },
    )

    path = application.root / "okx-trade.toml"
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))["credential"]
    assert parsed["values"] == {
        "api_key": "key-one",
        "api_secret": "secret-one",
        "passphrase": "phrase-one",
    }
    assert summary["fields"] == ["api_key", "api_secret", "passphrase"]
    assert "key-one" not in repr(summary)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_replacing_values_atomically_updates_the_same_file(tmp_path: Path) -> None:
    application = _application(tmp_path)
    application.configure("openai-main", provider="openai", values={"api_key": "first"})

    summary = application.configure(
        "openai-main",
        provider="openai",
        values={"api_key": "second"},
        overwrite=True,
    )

    assert summary["configured"] is True
    assert application.resolve_field("openai-main", "api_key") == "second"
    assert not (application.workspace.paths.root / "secrets").exists()


def test_missing_required_value_is_rejected_before_write(tmp_path: Path) -> None:
    application = _application(tmp_path)

    with pytest.raises(ValueError, match="api_secret"):
        application.configure(
            "binance-main", provider="binance", values={"api_key": "key"}
        )

    assert not (application.root / "binance-main.toml").exists()


@pytest.mark.parametrize(
    "document",
    [
        '[credential]\nid = "legacy"\nprovider = "openai"\napi_key = "old"\n',
        '[credential]\nid = "legacy"\nprovider = "openai"\n'
        '[credential.fields.api_key]\nsource = "env"\nid = "OPENAI_API_KEY"\n',
    ],
)
def test_runtime_rejects_non_values_credential_formats(
    tmp_path: Path, document: str
) -> None:
    application = _application(tmp_path)
    application.root.mkdir(parents=True, exist_ok=True)
    (application.root / "legacy.toml").write_text(document, encoding="utf-8")

    summary = application.show("legacy")

    assert summary["configured"] is False
    assert summary["fields"] == []
    assert application.resolve_field("legacy", "api_key") is None


def test_parse_errors_do_not_echo_secret_contents(tmp_path: Path) -> None:
    application = _application(tmp_path)
    path = application.root / "broken.toml"
    path.write_text(
        '[credential.values]\napi_secret = "do-not-echo\n', encoding="utf-8"
    )

    with pytest.raises(ValueError) as captured:
        application.show("broken")

    assert "do-not-echo" not in str(captured.value)


def test_credential_id_must_match_its_file_name(tmp_path: Path) -> None:
    application = _application(tmp_path)
    (application.root / "wrong.toml").write_text(
        '[credential]\nid = "right"\nprovider = "paper"\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="must match its file name"):
        application.show("wrong")


def test_control_characters_are_escaped_in_toml_values(tmp_path: Path) -> None:
    application = _application(tmp_path)
    application.configure(
        "multiline",
        provider="custom",
        values={"token": "first\nsecond"},
    )

    document = (application.root / "multiline.toml").read_text(encoding="utf-8")
    assert 'token = "first\\nsecond"' in document
    assert application.resolve_field("multiline", "token") == "first\nsecond"


def test_delete_removes_only_the_credential_file(tmp_path: Path) -> None:
    application = _application(tmp_path)
    application.configure(
        "telegram-main", provider="telegram", values={"bot_token": "123:test"}
    )

    assert application.delete("telegram-main") == {
        "credential_id": "telegram-main",
        "status": "deleted",
    }
    assert not (application.root / "telegram-main.toml").exists()


def test_reads_the_cross_language_credential_fixture(tmp_path: Path) -> None:
    application = _application(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "credentials" / "shared-okx.toml"
    target = application.root / fixture.name
    shutil.copy2(fixture, target)

    summary = application.show("shared-okx")

    assert summary["provider"] == "okx"
    assert summary["role"] == "trade"
    assert summary["configured"] is True
    assert application.resolve_field("shared-okx", "account_label") == "fixture-account"

    target.unlink()
    application.configure(
        "shared-okx",
        provider="okx",
        role="trade",
        values={
            "account_label": "fixture-account",
            "api_key": "fixture-key",
            "api_secret": "fixture-secret",
            "passphrase": "fixture-passphrase",
        },
        overwrite=True,
    )
    assert (application.root / "shared-okx.toml").read_text(encoding="utf-8") == (
        Path(__file__).parent / "fixtures" / "credentials" / "shared-okx.toml"
    ).read_text(encoding="utf-8")
