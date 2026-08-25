"""Workbench-safe draft lifecycle for notification destinations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
    PreparedCredential,
)
from kairospy.system.apps.workspace.application import Workspace, WorkspaceConfigurationTransaction

from .admin import (
    NotificationAdminApplication,
    NotificationProvider,
    PreparedNotificationDestination,
    _validate_provider_secret,
)


DeliveryProbe = Callable[[Mapping[str, object], str], object]


@dataclass(slots=True)
class NotificationDestinationDraft:
    prepared_destination: PreparedNotificationDestination
    prepared_credential: PreparedCredential | None
    existing_verification_status: str | None
    _secret: str | None = field(default=None, repr=False)
    probe_result: Mapping[str, object] | None = None
    committed: bool = False

    @property
    def destination(self) -> Mapping[str, object]:
        return self.prepared_destination.destination

    def redacted_summary(self) -> dict[str, object]:
        return {
            **dict(self.destination),
            "credential_configured": True,
            "probe": dict(self.probe_result) if self.probe_result is not None else None,
            "existing_verification_status": self.existing_verification_status,
        }

    def discard(self) -> None:
        if not self.committed and self.prepared_credential is not None:
            self.prepared_credential.discard()
        self._secret = None


@dataclass(frozen=True, slots=True)
class NotificationDestinationDraftApplication:
    workspace: Workspace

    def prepare(
        self,
        destination_id: str,
        *,
        provider: NotificationProvider,
        credential_id: str | None = None,
        credential_values: Mapping[str, str] | None = None,
        chat_id: str | None = None,
    ) -> NotificationDestinationDraft:
        credential_id = credential_id or destination_id
        field = "webhook_url" if provider == "feishu" else "bot_token"
        credentials = CredentialConfigurationApplication(self.workspace)
        prepared_credential: PreparedCredential | None = None
        if credential_values is not None:
            prepared_credential = credentials.prepare(
                credential_id,
                provider=provider,
                role="notification-send",
                values=credential_values,
            )
            secret = credential_values.get(field)
        else:
            secret = credentials.resolve_field(credential_id, field)

        if not secret:
            if prepared_credential is not None:
                prepared_credential.discard()
            raise ValueError(f"notification credential requires {field}")
        _validate_provider_secret(provider, secret)
        try:
            prepared_destination = NotificationAdminApplication(self.workspace).prepare(
                destination_id,
                provider=provider,
                credential_id=credential_id,
                chat_id=chat_id,
            )
        except BaseException:
            if prepared_credential is not None:
                prepared_credential.discard()
            raise
        try:
            current = NotificationAdminApplication(self.workspace).show(destination_id)
        except KeyError:
            existing_status = None
        else:
            existing_status = str(current.get("verification_status") or "pending")
        return NotificationDestinationDraft(
            prepared_destination,
            prepared_credential,
            existing_status,
            _secret=secret,
        )

    def test(
        self,
        draft: NotificationDestinationDraft,
        *,
        probe: DeliveryProbe,
    ) -> dict[str, object]:
        """Perform the explicit, externally visible delivery test."""

        try:
            probe(draft.destination, draft._secret or "")
        except Exception:
            result: dict[str, object] = {
                "succeeded": False,
                "error_category": "provider_response",
            }
        else:
            result = {"succeeded": True, "error_category": None}
        draft.probe_result = result
        return dict(result)

    def commit(
        self,
        draft: NotificationDestinationDraft,
        *,
        overwrite: bool = False,
        allow_unverified: bool = False,
    ) -> dict[str, object]:
        if draft.committed:
            raise RuntimeError("notification draft is already committed")
        destination_id = str(draft.destination["destination_id"])
        exists = draft.existing_verification_status is not None
        if exists and not overwrite:
            raise FileExistsError(destination_id)
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
                "failed draft test cannot replace an available notification destination"
            )
        if not succeeded and not allow_unverified:
            raise ValueError(
                "untested or failed draft requires explicit allow_unverified"
            )
        transaction = WorkspaceConfigurationTransaction(
            self.workspace, f"notification:{destination_id}"
        )
        if draft.prepared_credential is not None:
            draft.prepared_credential.stage(transaction)
        draft.prepared_destination.stage(transaction)
        try:
            transaction.commit()
        except BaseException:
            draft.discard()
            raise
        draft.committed = True
        draft._secret = None
        owner = NotificationAdminApplication(self.workspace)
        if draft.probe_result is not None:
            owner.record_test(destination_id, succeeded=succeeded)
        return owner.show(destination_id)


__all__ = [
    "DeliveryProbe",
    "NotificationDestinationDraft",
    "NotificationDestinationDraftApplication",
]
