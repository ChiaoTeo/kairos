"""Workbench-safe draft lifecycle for shared market-data connections."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..workspace.credentials import (
    CredentialConfigurationApplication,
    PreparedCredential,
    SecretRef,
)
from ..workspace import Workspace, WorkspaceConfigurationTransaction
from .configuration import (
    PreparedReferenceProvider,
    ReferenceProviderConfigurationApplication,
)


@dataclass(slots=True)
class ReferenceProviderDraft:
    prepared_provider: PreparedReferenceProvider
    prepared_credential: PreparedCredential | None
    existing_verification_status: str | None
    _secret: str | None = field(default=None, repr=False)
    probe_result: Mapping[str, object] | None = None
    committed: bool = False

    @property
    def connection(self) -> Mapping[str, object]:
        return self.prepared_provider.connection

    def redacted_summary(self) -> dict[str, object]:
        return {
            **dict(self.connection),
            "credential_configured": True,
            "probe": dict(self.probe_result) if self.probe_result is not None else None,
            "existing_verification_status": self.existing_verification_status,
        }

    def discard(self) -> None:
        if not self.committed and self.prepared_credential is not None:
            self.prepared_credential.discard()
        self._secret = None


@dataclass(frozen=True, slots=True)
class ReferenceProviderDraftApplication:
    workspace: Workspace

    def prepare_massive(
        self,
        *,
        credential_id: str,
        credential_values: Mapping[str, str] | None = None,
        credential_refs: Mapping[str, SecretRef] | None = None,
        endpoint: str = "https://api.massive.com",
        capabilities: Sequence[str] = ("reference", "equity_market"),
    ) -> ReferenceProviderDraft:
        if credential_values is not None and credential_refs is not None:
            raise ValueError("credential values and SecretRefs are mutually exclusive")
        credentials = CredentialConfigurationApplication(self.workspace)
        prepared_credential: PreparedCredential | None = None
        if credential_values is not None:
            prepared_credential = credentials.prepare_secret_values(
                credential_id,
                provider="massive",
                role="readonly",
                values=credential_values,
            )
            secret = credential_values.get("api_key")
        elif credential_refs is not None:
            prepared_credential = credentials.prepare(
                credential_id,
                provider="massive",
                role="readonly",
                fields=credential_refs,
            )
            reference = credential_refs.get("api_key")
            secret = credentials.resolve(reference) if reference is not None else None
        else:
            secret = credentials.resolve_field(credential_id, "api_key")
        try:
            prepared_provider = ReferenceProviderConfigurationApplication(
                self.workspace
            ).prepare_massive(
                credential_id=credential_id,
                endpoint=endpoint,
                capabilities=capabilities,
                credential_provider=(
                    "massive" if prepared_credential is not None else None
                ),
            )
        except BaseException:
            if prepared_credential is not None:
                prepared_credential.discard()
            raise
        try:
            current = ReferenceProviderConfigurationApplication(self.workspace).show(
                "massive"
            )
        except KeyError:
            existing_status = None
        else:
            existing_status = str(current.get("verification_status") or "pending")
        return ReferenceProviderDraft(
            prepared_provider,
            prepared_credential,
            existing_status,
            _secret=secret,
        )

    def test(
        self,
        draft: ReferenceProviderDraft,
        *,
        probe: Callable[[str, str], Mapping[str, Any]] | None = None,
    ) -> dict[str, object]:
        result = ReferenceProviderConfigurationApplication(self.workspace).probe(
            draft.connection,
            secret=draft._secret or "",
            probe=probe,
        )
        draft.probe_result = result
        return dict(result)

    def commit(
        self,
        draft: ReferenceProviderDraft,
        *,
        overwrite: bool = False,
        allow_unverified: bool = False,
    ) -> dict[str, object]:
        if draft.committed:
            raise RuntimeError("market-data draft is already committed")
        exists = draft.existing_verification_status is not None
        if exists and not overwrite:
            raise FileExistsError("massive")
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
                "failed draft test cannot replace an available market-data connection"
            )
        if not succeeded and not allow_unverified:
            raise ValueError(
                "untested or failed draft requires explicit allow_unverified"
            )
        transaction = WorkspaceConfigurationTransaction(
            self.workspace, "market-data:massive"
        )
        if draft.prepared_credential is not None:
            draft.prepared_credential.stage(transaction)
        draft.prepared_provider.stage(transaction)
        try:
            transaction.commit()
        except BaseException:
            draft.discard()
            raise
        draft.committed = True
        draft._secret = None
        owner = ReferenceProviderConfigurationApplication(self.workspace)
        if draft.probe_result is not None:
            owner.record_probe("massive", draft.probe_result or {})
        return owner.show("massive")


__all__ = ["ReferenceProviderDraft", "ReferenceProviderDraftApplication"]
