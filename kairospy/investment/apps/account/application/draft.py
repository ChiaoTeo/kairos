"""Disposable-Workspace draft lifecycle for Account configuration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import tempfile
import tomllib
from typing import Any

from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
    PreparedCredential,
)
from kairospy.system.apps.workspace.application import (
    Workspace,
    WorkspaceApplication,
    WorkspaceConfigurationTransaction,
)

from . import AccountConfigurationApplication
from .configuration_models import AccountVerification


AccountProbe = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(slots=True)
class AccountConfigurationDraft:
    workspace: Workspace
    draft_workspace: Workspace = field(repr=False)
    temporary_directory: tempfile.TemporaryDirectory[str] = field(repr=False)
    account: Mapping[str, Any]
    account_document: str = field(repr=False)
    account_file_name: str
    prepared_credential: PreparedCredential | None
    existing_verification_status: str | None
    probe_result: AccountVerification | None = None
    evidence_document: str | None = field(default=None, repr=False)
    committed: bool = False

    def redacted_summary(self) -> dict[str, object]:
        return {
            key: self.account.get(key)
            for key in (
                "account_id",
                "broker",
                "integration_provider",
                "environment",
                "segments",
                "permissions",
                "credential_id",
                "credential_role",
                "status",
            )
        } | {
            "probe": (
                self.probe_result.to_json_dict()
                if self.probe_result is not None
                else None
            ),
            "existing_verification_status": self.existing_verification_status,
        }

    def discard(self) -> None:
        if not self.committed and self.prepared_credential is not None:
            self.prepared_credential.discard()
        self.temporary_directory.cleanup()


@dataclass(frozen=True, slots=True)
class AccountConfigurationDraftApplication:
    workspace: Workspace

    def prepare_live(
        self,
        account_id: str,
        *,
        broker: str,
        integration_provider: str | None = None,
        segment: str = "spot",
        environment: str = "live",
        credential_id: str,
        credential_provider: str | None = None,
        credential_values: Mapping[str, str] | None = None,
        credential_role: str = "readonly",
        alias: str | None = None,
        account_model: str | None = None,
    ) -> AccountConfigurationDraft:
        provider = (credential_provider or integration_provider or broker).lower()
        credentials = CredentialConfigurationApplication(self.workspace)
        prepared_credential: PreparedCredential | None = None
        if credential_values is not None:
            prepared_credential = credentials.prepare(
                credential_id,
                provider=provider,
                role=credential_role,
                values=credential_values,
            )
        else:
            credentials.show(credential_id)
        temporary, draft_workspace = self._draft_workspace(prepared_credential)
        try:
            owner = AccountConfigurationApplication(draft_workspace)
            account = owner.connect(
                account_id,
                broker=broker,
                integration_provider=integration_provider or provider,
                segment=segment,
                environment=environment,
                credential=credential_id,
                credential_role=credential_role,
                alias=alias,
                account_model=account_model,
            )
            return self._draft(temporary, draft_workspace, account, prepared_credential)
        except BaseException:
            temporary.cleanup()
            if prepared_credential is not None:
                prepared_credential.discard()
            raise

    def prepare_simulated(
        self,
        account_id: str,
        *,
        broker: str = "paper",
        segment: str = "spot",
        environment: str = "paper",
        account_model: str | None = None,
        initial_balances: tuple[str, ...] = (),
        fee_rate: str = "0",
    ) -> AccountConfigurationDraft:
        temporary, draft_workspace = self._draft_workspace(None)
        try:
            account = AccountConfigurationApplication(draft_workspace).simulate(
                account_id,
                broker=broker,
                segment=segment,
                environment=environment,
                account_model=account_model,
                initial_balances=initial_balances,
                fee_rate=fee_rate,
            )
            return self._draft(temporary, draft_workspace, account, None)
        except BaseException:
            temporary.cleanup()
            raise

    def test(
        self,
        draft: AccountConfigurationDraft,
        *,
        probe: AccountProbe | None = None,
    ) -> dict[str, object]:
        result = AccountConfigurationApplication(draft.draft_workspace).test_connection(
            str(draft.account["account_id"]), probe=probe
        )
        draft.probe_result = result
        evidence = draft.draft_workspace.paths.child(
            "state",
            "configuration",
            "accounts",
            f"{draft.account['account_id']}.json",
        )
        draft.evidence_document = evidence.read_text(encoding="utf-8")
        return result.to_json_dict()

    def commit(
        self,
        draft: AccountConfigurationDraft,
        *,
        overwrite: bool = False,
        allow_unverified: bool = False,
    ) -> dict[str, object]:
        if draft.committed:
            raise RuntimeError("account draft is already committed")
        account_id = str(draft.account["account_id"])
        exists = draft.existing_verification_status is not None
        if exists and not overwrite:
            raise FileExistsError(account_id)
        succeeded = (
            draft.probe_result is not None and draft.probe_result.status == "verified"
        )
        if (
            exists
            and draft.existing_verification_status == "verified"
            and draft.probe_result is not None
            and not succeeded
        ):
            raise ValueError("failed draft test cannot replace an available account")
        if not succeeded and not allow_unverified:
            raise ValueError(
                "untested or failed draft requires explicit allow_unverified"
            )
        transaction = WorkspaceConfigurationTransaction(
            self.workspace, f"account:{account_id}"
        )
        if draft.prepared_credential is not None:
            draft.prepared_credential.stage(transaction)
        transaction.stage_text(
            self.workspace.paths.account_config().parent / draft.account_file_name,
            draft.account_document,
        )
        if succeeded and draft.evidence_document is not None:
            transaction.stage_text(
                self.workspace.paths.child(
                    "state", "configuration", "accounts", f"{account_id}.json"
                ),
                draft.evidence_document,
            )
        try:
            transaction.commit()
        except BaseException:
            draft.discard()
            raise
        draft.committed = True
        draft.temporary_directory.cleanup()
        return AccountConfigurationApplication(self.workspace).show(account_id)

    def _draft_workspace(
        self, prepared_credential: PreparedCredential | None
    ) -> tuple[tempfile.TemporaryDirectory[str], Workspace]:
        temporary = tempfile.TemporaryDirectory(prefix="kairos-account-draft-")
        draft_workspace = WorkspaceApplication().init(
            Path(temporary.name) / "workspace",
            workspace_id=f"{self.workspace.identity.workspace_id}-account-draft",
        )
        for source, target in (
            (
                self.workspace.paths.account_config().parent,
                draft_workspace.paths.account_config().parent,
            ),
            (
                self.workspace.paths.credentials_root(),
                draft_workspace.paths.credentials_root(),
            ),
        ):
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
        if prepared_credential is not None:
            target = draft_workspace.paths.credentials_root() / (
                f"{prepared_credential.credential_id}.toml"
            )
            target.write_text(prepared_credential.document, encoding="utf-8")
            target.chmod(0o600)
        return temporary, draft_workspace

    def _draft(
        self,
        temporary: tempfile.TemporaryDirectory[str],
        draft_workspace: Workspace,
        account: Mapping[str, Any],
        prepared_credential: PreparedCredential | None,
    ) -> AccountConfigurationDraft:
        account_id = str(account["account_id"])
        account_path = _find_account_file(draft_workspace, account_id)
        try:
            current = AccountConfigurationApplication(self.workspace).show(account_id)
        except (KeyError, RuntimeError):
            existing_status = None
        else:
            existing_status = str(current.get("verification_status") or "pending")
        return AccountConfigurationDraft(
            self.workspace,
            draft_workspace,
            temporary,
            dict(account),
            account_path.read_text(encoding="utf-8"),
            account_path.name,
            prepared_credential,
            existing_status,
        )


def _find_account_file(workspace: Workspace, account_id: str) -> Path:
    for path in workspace.paths.account_config().parent.glob("*.toml"):
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        account = value.get("account")
        if isinstance(account, Mapping) and account.get("id") == account_id:
            return path
    raise RuntimeError(f"Account application did not write account {account_id}")


__all__ = [
    "AccountConfigurationDraft",
    "AccountConfigurationDraftApplication",
    "AccountProbe",
]
