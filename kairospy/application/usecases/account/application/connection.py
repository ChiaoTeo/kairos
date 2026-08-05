"""Account connection and remote-account discovery use cases."""

from __future__ import annotations

from typing import Sequence

from kairospy.application.usecases.account.application.configuration import AccountConfigurationError, AccountRecord, AccountStore
from kairospy.application.usecases.account.application.ports import AccountCommandResources, AccountCredentialProfile
from kairospy.application.usecases.account.application.results import AccountBindingResult, AccountConfigurationPathResult, AccountInspectionResult
from kairospy.application.usecases.account.application.schemas import ACCOUNT_SCHEMAS, PROVIDER_ALIASES
from kairospy.application.usecases.account.services.configuration import AccountConfigurationWriter
from kairospy.application.usecases.market.application.commands import DriverName
from kairospy.application.usecases.workspace.application.context import workspace as resolve_workspace
from kairospy.domain.account import AccountModel, AccountSegment, ExternalAccountIdentity, ProductFamily, account_segment_from_name


class AccountConnectionApplication:
    """Discover an externally owned account and maintain its local binding."""

    def __init__(self, resources: AccountCommandResources) -> None:
        self._resources = resources
        self._configuration = AccountConfigurationWriter()

    def inspect(self, account_id: str) -> AccountInspectionResult:
        account = _account(account_id)
        credential = _read_credential_ref(account)
        profile = self._resources.credential_profile(_account_segment_ref(account), DriverName.ccxt, credential=credential)
        requested = account.default_segment or ProductFamily.SPOT.value
        return AccountInspectionResult(
            account_id=account.account_id,
            broker=account.broker,
            environment=account.environment,
            credential=credential,
            remote_identity=account.remote_identity,
            account_type=profile.account_type,
            observed_model=_profile_account_model(profile),
            permissions=tuple(sorted(profile.permissions)),
            configured_segments=tuple(segment.key for segment in account.segments),
            discovered_segments=_discovered_segment_names(profile, requested=requested),
            profile=profile,
        )

    def connect(
        self,
        *,
        broker: str,
        environment: str,
        credential: str,
        credential_role: str,
        alias: str | None,
        product_family: str | None,
        account_model: str | None,
        force: bool,
    ) -> AccountBindingResult:
        broker_name = broker.strip().lower()
        credential_ref = credential.strip()
        environment_name = environment.strip().lower()
        if not broker_name:
            raise ValueError("broker is required")
        if not credential_ref:
            raise ValueError("credential is required")
        if not environment_name:
            raise ValueError("environment is required")
        role = _credential_role(credential_role)
        segment_name = (product_family or ProductFamily.SPOT.value).strip().lower().replace("-", "_")
        probe_segment = account_segment_from_name(ExternalAccountIdentity(broker_name, credential_ref), segment_name)
        profile = self._resources.credential_profile(probe_segment, DriverName.ccxt, credential=credential_ref)
        _require_credential_role(credential_ref, profile, role)
        remote_identity = profile.remote_identity
        discovered_id = remote_identity or credential_ref
        existing = next(
            (
                item for item in AccountStore.load(resolve_workspace().accounts_root).list()
                if item.broker.lower() == broker_name
                and (item.remote_identity or item.account_id) == discovered_id
            ),
            None,
        )
        local_id = (alias or (existing.account_id if existing is not None else discovered_id)).strip()
        if not local_id:
            raise ValueError("local account alias cannot be empty")
        normalized_model = None if account_model is None else AccountModel(account_model.strip().lower())
        segment_names = _discovered_segment_names(profile, requested=segment_name)
        identity = ExternalAccountIdentity(broker_name, discovered_id)
        segments = tuple(account_segment_from_name(identity, name) for name in segment_names)
        path = existing.source_path if existing is not None and existing.source_path is not None else resolve_workspace().accounts_root / f"{local_id}.toml"
        if existing is not None:
            if alias is not None and alias.strip() != existing.account_id:
                raise ValueError("credential belongs to an existing remote account; reuse its binding alias")
            credentials: list[tuple[str, str]] = [(item.name, item.ref) for item in existing.credentials if item.ref]
            if not any(ref == credential_ref for _, ref in credentials):
                credentials.append((role, credential_ref))
            self._configuration.write_account(path, _account_binding_template(local_id, broker=broker_name, environment=existing.environment or environment_name, product_families=segment_names, account_model=normalized_model, credential=credential_ref, credential_role=role, remote_identity=discovered_id, credentials=tuple(credentials)))
            resolve_workspace().operations.append("account.connect.reconcile", target={"account": local_id, "credential": credential_ref}, payload={"path": path, "remote_identity": remote_identity, "segments": list(segment_names)})
            return AccountBindingResult(local_id, identity, segments, credential_ref, remote_identity, path)
        if path.exists() and not force:
            raise ValueError(f"account binding already exists: {path}")
        self._configuration.write_account(path, _account_binding_template(local_id, broker=broker_name, environment=environment_name, product_families=segment_names, account_model=normalized_model, credential=credential_ref, credential_role=role, remote_identity=discovered_id))
        resolve_workspace().operations.append("account.connect", target={"account": local_id, "credential": credential_ref}, payload={"path": path, "remote_identity": remote_identity, "segments": list(segment_names)})
        return AccountBindingResult(local_id, identity, segments, credential_ref, remote_identity, path)

    def add_credential(self, account_id: str, *, name: str, ref: str, check: bool, force: bool) -> AccountConfigurationPathResult:
        workspace = resolve_workspace()
        account = _account(account_id)
        if ref.strip().startswith("env:"):
            raise ValueError("credential ref is not an env: reference")
        credential_name = name.strip()
        if not credential_name:
            raise ValueError("credential name is required")
        credential_role = _credential_role(credential_name)
        existing = {credential.name for credential in account.credentials}
        if credential_name in existing and not force:
            raise ValueError(f"credential already exists: {credential_name}")
        if check:
            self._check_credential(account, ref=ref, role=credential_role)
        path = account.source_path or workspace.accounts_root / f"{account.account_id}.toml"
        if credential_name in existing:
            raise ValueError("replacing credentials is not implemented yet; delete or edit the account file")
        self._configuration.append_credential(path, _credential_template(credential_name, ref=ref))
        workspace.operations.append("account.credential.add", target={"account": account.account_id, "credential": credential_name}, payload={"path": path, "ref": ref, "role": credential_role})
        return AccountConfigurationPathResult(path)

    def _check_credential(self, account: AccountRecord, *, ref: str, role: str) -> None:
        profile = self._resources.credential_profile(_account_segment_ref(account), DriverName.ccxt, credential=ref)
        _require_credential_role(ref, profile, role)
        identities = [
            self._resources.credential_profile(_account_segment_ref(account), DriverName.ccxt, credential=credential.ref).remote_identity
            for credential in account.credentials
            if credential.ref
        ]
        if account.credential:
            identities.append(self._resources.credential_profile(_account_segment_ref(account), DriverName.ccxt, credential=account.credential).remote_identity)
        existing_identities = [identity for identity in identities if identity is not None]
        if existing_identities:
            new_identity = profile.remote_identity
            if new_identity is None:
                raise ValueError(f"credential {ref} account identity could not be verified")
            expected = existing_identities[0]
            if any(identity != expected for identity in existing_identities) or new_identity != expected:
                raise ValueError(f"credential {ref} belongs to a different account")


def _account(account_id: str) -> AccountRecord:
    try:
        return AccountStore.load(resolve_workspace().accounts_root).get(account_id)
    except AccountConfigurationError as error:
        raise ValueError(str(error)) from error


def _account_segment_ref(account: AccountRecord, segment: str | None = None) -> AccountSegment:
    selected = segment or account.default_segment or ProductFamily.SPOT.value
    return account_segment_from_name(ExternalAccountIdentity(account.broker, account.account_id), selected)


def _read_credential_ref(account: AccountRecord) -> str | None:
    for credential in account.credentials:
        if credential.role == "readonly" and credential.ref:
            return credential.ref
    for credential in account.credentials:
        if credential.ref:
            return credential.ref
    return account.credential


def _credential_role(role: str | None) -> str:
    value = (role or "readonly").strip().lower().replace("-", "_")
    if value not in {"readonly", "read_only", "trade"}:
        raise ValueError("credential role must be readonly or trade")
    return "readonly" if value in {"readonly", "read_only"} else "trade"


def _discovered_segment_names(profile: AccountCredentialProfile, *, requested: str) -> tuple[str, ...]:
    candidates = [requested, *profile.segments]
    normalized: list[str] = []
    for candidate in candidates:
        value = candidate.strip().lower().replace("-", "_").replace(" ", "_")
        if not value:
            continue
        try:
            segment = account_segment_from_name(ExternalAccountIdentity("discovery", "discovery"), value)
        except (TypeError, ValueError):
            continue
        name = segment.product_family.value if segment.product_family is not None else segment.segment_id
        if name not in normalized:
            normalized.append(name)
    return tuple(normalized) or (ProductFamily.SPOT.value,)


def _profile_account_model(profile: AccountCredentialProfile) -> str | None:
    value = str(profile.account_type or "").strip().lower()
    return {
        "spot": AccountModel.NO_MARGIN.value,
        "unified": AccountModel.UNIFIED.value,
        "multi_assets": AccountModel.UNIFIED.value,
        "portfolio_margin": AccountModel.PORTFOLIO_MARGIN.value,
        "contract": AccountModel.CONTRACT.value,
        "futures": AccountModel.CONTRACT.value,
    }.get(value) or (value or None)


def _require_credential_role(ref: str, profile: AccountCredentialProfile, role: str) -> None:
    if "read" not in profile.permissions:
        raise ValueError(f"credential {ref} cannot read private account data")
    if role == "trade" and "trade" not in profile.permissions:
        raise ValueError(f"credential {ref} is not a trade credential")


def _credential_template(name: str, *, ref: str) -> str:
    return "\n".join([f"[credentials.{_toml_key(name)}]", f'ref = "{_toml_escape(ref)}"'])


def _account_binding_template(binding_id: str, *, broker: str, environment: str, product_families: Sequence[str], account_model: AccountModel | None, credential: str, credential_role: str, remote_identity: str | None, credentials: Sequence[tuple[str, str]] = ()) -> str:
    lines = ["[account]", f'id = "{_toml_escape(binding_id)}"', f'broker = "{_toml_escape(broker)}"', f'environment = "{_toml_escape(environment)}"', f'credential = "{_toml_escape(credential)}"', ""]
    for product_family in product_families:
        lines.extend([f"[segments.{_toml_key(product_family)}]", *([] if account_model is None else [f'model = "{account_model.value}"']), f'product_family = "{_toml_escape(product_family)}"', ""])
    lines.extend(["[discovery]", 'source = "credential"'])
    if remote_identity is not None:
        lines.append(f'remote_identity = "{_toml_escape(remote_identity)}"')
    if account_model is not None:
        lines.append(f'configured_model = "{account_model.value}"')
    lines.append("")
    for name, ref in tuple(credentials) or ((credential_role, credential),):
        lines.extend([_credential_template(name, ref=ref), ""])
    return "\n".join(lines) + "\n"


__all__ = ["AccountConnectionApplication"]


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _toml_key(value: str) -> str:
    if value and all(character.isalnum() or character in "_-" for character in value):
        return value
    return f'"{_toml_escape(value)}"'
