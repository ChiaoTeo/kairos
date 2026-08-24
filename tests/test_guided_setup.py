from __future__ import annotations

from pathlib import Path

from kairospy.application.workspace.credentials import (
    CredentialConfigurationApplication,
)
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli.guided_setup import (
    configure_credential_material,
    prompt_credential_material,
)


def test_prompt_credential_material_accepts_hidden_paste_without_writing(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="demo")
    application = CredentialConfigurationApplication(workspace)
    answers = iter(("1", "typed-key", "typed-secret", "typed-passphrase"))
    prompt_calls: list[dict[str, object]] = []

    def prompt(*_args, **kwargs):
        prompt_calls.append(kwargs)
        return next(answers)

    monkeypatch.setattr("typer.prompt", prompt)

    material = prompt_credential_material(application, "okx-main", "okx")

    assert "typed-key" not in repr(material)
    assert "typed-secret" not in repr(material)
    assert application.list() == []
    assert all(call.get("hide_input") is True for call in prompt_calls[1:])

    summary = configure_credential_material(
        application, "okx-main", "okx", material, role="trade"
    )
    assert summary["configured"] is True
    assert application.resolve_field("okx-main", "passphrase") == "typed-passphrase"


def test_prompt_credential_material_can_stage_environment_refs(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="demo")
    application = CredentialConfigurationApplication(workspace)
    answers = iter(("2", "OPENAI_CUSTOM_KEY"))
    monkeypatch.setattr("typer.prompt", lambda *_args, **_kwargs: next(answers))

    material = prompt_credential_material(application, "openai-main", "openai")

    assert material.references is not None
    assert material.references["api_key"].id == "OPENAI_CUSTOM_KEY"
    assert application.list() == []
