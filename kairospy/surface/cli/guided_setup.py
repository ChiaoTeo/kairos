"""Shared terminal input mechanics for product-owned setup wizards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import click
import typer

from kairospy.application.workspace.credentials import (
    CredentialConfigurationApplication,
    SecretRef,
)


_FIELD_LABELS = {
    "api_key": "API Key",
    "api_secret": "API Secret",
    "passphrase": "Passphrase",
    "bot_token": "Bot Token",
    "webhook_url": "Webhook URL",
}


@dataclass(frozen=True, slots=True)
class CredentialMaterial:
    """Secret-safe staged credential input; values are never printable."""

    references: Mapping[str, SecretRef] | None = None
    values: Mapping[str, str] | None = None

    def __repr__(self) -> str:
        fields = tuple((self.references or self.values or {}).keys())
        mode = "references" if self.references is not None else "private-values"
        return f"CredentialMaterial(mode={mode!r}, fields={fields!r})"


def print_step(resource: str, current: int, total: int, title: str) -> None:
    typer.echo()
    typer.echo(f"── {resource} · 步骤 {current}/{total} · {title} ──")


def prompt_choice(
    title: str,
    options: Sequence[tuple[str, str]],
    *,
    default: str,
    allow_back: bool = True,
) -> str:
    typer.echo(title)
    for key, label in options:
        typer.echo(f"  {key}. {label}")
    if allow_back:
        typer.echo("  b. 返回")
    valid = {key for key, _label in options}
    while True:
        value = typer.prompt("请输入序号", default=default).strip().lower()
        if allow_back and value in {"b", "back"}:
            cancel_setup()
        if value in valid:
            return value
        typer.echo("这个选项不存在，请重新输入。")


def prompt_credential_material(
    application: CredentialConfigurationApplication,
    credential_id: str,
    provider: str,
    *,
    existing: Mapping[str, object] | None = None,
) -> CredentialMaterial:
    schema = application.schema(provider)
    fields = tuple(str(value) for value in schema["required_fields"])
    choice = prompt_choice(
        "如何提供安全凭据？",
        (
            ("1", "现在安全粘贴（推荐）"),
            ("2", "从环境变量读取"),
            ("3", "从 Secret 文件读取"),
        ),
        default="1",
    )
    if choice == "1":
        values: dict[str, str] = {}
        existing_references = (
            existing.get("secret_refs", {}) if existing is not None else {}
        )
        for field in fields:
            label = _FIELD_LABELS.get(field, field)
            while True:
                suffix = "；直接回车沿用现有值" if field in existing_references else ""
                value = typer.prompt(
                    f"{label}（输入内容不会显示{suffix}）",
                    hide_input=True,
                    default="",
                    show_default=False,
                ).strip()
                if value:
                    values[field] = value
                    break
                if field in existing_references:
                    reference = existing_references[field]
                    if isinstance(reference, Mapping):
                        current = application.resolve(
                            SecretRef(str(reference["source"]), str(reference["id"]))
                        )
                        if current is not None:
                            values[field] = current
                            break
                    typer.echo(f"现有 {label} 当前不可读取，请重新填写。")
                    continue
                typer.echo(f"{label} 不能为空。")
        return CredentialMaterial(values=values)

    references: dict[str, SecretRef] = {}
    source = "env" if choice == "2" else "file"
    for field in fields:
        label = _FIELD_LABELS.get(field, field)
        default = (
            application.default_environment(credential_id, field)
            if source == "env"
            else str(
                application.workspace.paths.root / "secrets" / credential_id / field
            )
        )
        identifier = typer.prompt(
            f"{label} {'环境变量名' if source == 'env' else 'Secret 文件路径'}",
            default=default,
        ).strip()
        references[field] = SecretRef(source, identifier)  # type: ignore[arg-type]
    return CredentialMaterial(references=references)


def configure_credential_material(
    application: CredentialConfigurationApplication,
    credential_id: str,
    provider: str,
    material: CredentialMaterial,
    *,
    role: str = "readonly",
    overwrite: bool = False,
) -> dict[str, object]:
    if material.values is not None:
        return application.configure_secret_values(
            credential_id,
            provider=provider,
            values=material.values,
            role=role,
            overwrite=overwrite,
        )
    if material.references is None:
        raise ValueError("credential material is empty")
    return application.configure(
        credential_id,
        provider=provider,
        fields=material.references,
        role=role,
        overwrite=overwrite,
    )


def staged_secret_value(
    application: CredentialConfigurationApplication,
    material: CredentialMaterial,
    field: str,
) -> str | None:
    """Resolve one staged field without persisting it or exposing it in output."""

    if material.values is not None:
        return material.values.get(field)
    if material.references is not None and field in material.references:
        return application.resolve(material.references[field])
    return None


def confirm_summary(title: str, rows: Sequence[tuple[str, str]]) -> bool:
    typer.echo()
    typer.echo(title)
    for label, value in rows:
        typer.echo(f"  {label:<12}{value}")
    return typer.confirm("确认保存？", default=True)


def cancel_setup() -> None:
    typer.echo("已取消当前配置，未保存任何修改。")
    raise click.Abort()


__all__ = [
    "CredentialMaterial",
    "cancel_setup",
    "confirm_summary",
    "configure_credential_material",
    "print_step",
    "prompt_choice",
    "prompt_credential_material",
    "staged_secret_value",
]
