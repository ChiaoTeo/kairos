"""Workbench-safe draft lifecycle for AI model connections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
    PreparedCredential,
)
from kairospy.system.apps.workspace.application import (
    Workspace,
    WorkspaceConfigurationTransaction,
)

from .model_connections import (
    CatalogProbe,
    ModelProbe,
    ModelProviderConnectionApplication,
    PreparedModelConnection,
)


@dataclass(slots=True)
class ModelConnectionDraft:
    """In-memory working copy; repr and summaries never include secret values."""

    prepared_connection: PreparedModelConnection
    prepared_credential: PreparedCredential | None
    existing_verification_status: str | None
    _secret: str | None = field(default=None, repr=False)
    probe_result: Mapping[str, object] | None = None
    probed_model: str | None = None
    committed: bool = False

    @property
    def connection(self) -> Mapping[str, object]:
        return self.prepared_connection.connection

    def redacted_summary(self) -> dict[str, object]:
        return {
            "connection_id": self.connection["connection_id"],
            "provider": self.connection["provider"],
            "api_mode": self.connection["api_mode"],
            "base_url": self.connection["base_url"],
            "credential_id": self.connection.get("credential_id"),
            "credential_configured": self.connection.get("credential_id") is not None,
            "models": list(cast(Any, self.connection.get("models") or ())),
            "probe": dict(self.probe_result) if self.probe_result is not None else None,
            "existing_verification_status": self.existing_verification_status,
        }

    def discard(self) -> None:
        if not self.committed and self.prepared_credential is not None:
            self.prepared_credential.discard()
        self._secret = None


@dataclass(frozen=True, slots=True)
class ModelConnectionDraftApplication:
    workspace: Workspace

    def prepare(
        self,
        connection_id: str,
        *,
        provider: str,
        api_mode: str | None = None,
        base_url: str | None = None,
        credential_id: str | None = None,
        credential_values: Mapping[str, str] | None = None,
        models: Sequence[str] = (),
        timeout_seconds: float = 60.0,
        enabled: bool = True,
    ) -> ModelConnectionDraft:
        connections = ModelProviderConnectionApplication(self.workspace)
        defaults = (
            connections.provider_defaults(provider)
            if any(
                value["provider"] == provider.strip().lower()
                for value in connections.provider_catalog()
            )
            else {
                "credential_provider": "custom-model",
                "auth_required": True,
            }
        )
        credential_provider = str(defaults.get("credential_provider") or "custom-model")
        auth_required = bool(defaults.get("auth_required", True))
        credentials = CredentialConfigurationApplication(self.workspace)
        prepared_credential: PreparedCredential | None = None
        secret: str | None = None
        if auth_required:
            if credential_id is None:
                raise ValueError(
                    "authenticated model connection requires credential_id"
                )
            if credential_values is not None:
                prepared_credential = credentials.prepare(
                    credential_id,
                    provider=credential_provider,
                    role="model-inference",
                    values=credential_values,
                )
                secret = credential_values.get("api_key")
            else:
                secret = credentials.resolve_field(credential_id, "api_key")
        elif credential_id is not None or credential_values:
            raise ValueError("this local model connection does not require credentials")

        try:
            prepared_connection = connections.prepare(
                connection_id,
                provider=provider,
                api_mode=api_mode,
                base_url=base_url,
                credential_id=credential_id,
                credential_provider=(
                    credential_provider if prepared_credential is not None else None
                ),
                models=models,
                timeout_seconds=timeout_seconds,
                enabled=enabled,
            )
        except BaseException:
            if prepared_credential is not None:
                prepared_credential.discard()
            raise
        try:
            existing = connections.show(connection_id)
        except KeyError:
            existing_status = None
        else:
            existing_status = str(existing.get("verification_status") or "pending")
        return ModelConnectionDraft(
            prepared_connection,
            prepared_credential,
            existing_status,
            _secret=secret,
        )

    def discover_models(
        self,
        draft: ModelConnectionDraft,
        *,
        probe: CatalogProbe | None = None,
    ) -> tuple[dict[str, object], ...]:
        return ModelProviderConnectionApplication(self.workspace).discover(
            draft.connection,
            secret=draft._secret,
            probe=probe,
        )

    def test(
        self,
        draft: ModelConnectionDraft,
        model: str,
        *,
        probe: ModelProbe | None = None,
    ) -> dict[str, object]:
        result = ModelProviderConnectionApplication(self.workspace).probe(
            draft.connection,
            model,
            secret=draft._secret,
            probe=probe,
        )
        draft.probe_result = result
        draft.probed_model = model
        return dict(result)

    def commit(
        self,
        draft: ModelConnectionDraft,
        *,
        overwrite: bool = False,
        allow_unverified: bool = False,
    ) -> dict[str, object]:
        if draft.committed:
            raise RuntimeError("model connection draft is already committed")
        connection_id = str(draft.connection["connection_id"])
        exists = draft.existing_verification_status is not None
        if exists and not overwrite:
            raise FileExistsError(connection_id)
        succeeded = (
            draft.probe_result is not None
            and draft.probe_result.get("succeeded") is True
        )
        if (
            exists
            and draft.existing_verification_status == "verified"
            and draft.probe_result is not None
            and not succeeded
        ):
            raise ValueError(
                "failed draft test cannot replace an available model connection"
            )
        if not succeeded and not allow_unverified:
            raise ValueError(
                "untested or failed draft requires explicit allow_unverified"
            )

        transaction = WorkspaceConfigurationTransaction(
            self.workspace, f"model-connection:{connection_id}"
        )
        if draft.prepared_credential is not None:
            draft.prepared_credential.stage(transaction)
        draft.prepared_connection.stage(transaction)
        try:
            transaction.commit()
        except BaseException:
            draft.discard()
            raise
        draft.committed = True
        draft._secret = None
        connections = ModelProviderConnectionApplication(self.workspace)
        if draft.probe_result is not None and draft.probed_model is not None:
            connections.record_probe(
                connection_id, draft.probed_model, draft.probe_result or {}
            )
        return connections.show(connection_id)


__all__ = ["ModelConnectionDraft", "ModelConnectionDraftApplication"]
