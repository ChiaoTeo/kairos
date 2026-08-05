"""Local simulated-account provisioning use case."""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Sequence

from kairospy.domain.account import AccountModel
from kairospy.application.usecases.account.application.schemas import AccountBrokerSchema
from kairospy.application.usecases.account.application.schemas import ACCOUNT_SCHEMAS, PROVIDER_ALIASES
from kairospy.application.usecases.account.application.results import AccountConfigurationPathResult
from kairospy.application.usecases.account.services.configuration import AccountConfigurationWriter
from kairospy.application.usecases.workspace.application.context import workspace as resolve_workspace


class AccountSimulationApplication:
    """Create a local account definition for paper, backtest, or simulation."""

    def __init__(self) -> None:
        self._configuration = AccountConfigurationWriter()

    def provision(
        self,
        *,
        account_id: str,
        broker: str,
        environment: str,
        venue: str | None,
        product_family: str | None,
        account_model: str | None = None,
        initial_balances: Sequence[str] | None,
        fee_rate: str,
        credential_kind: str | None,
        credential: str | None,
        credential_role: str | None,
        api_key: str | None,
        api_secret: str | None,
        passphrase: str | None,
        wallet_address: str | None,
        private_key: str | None,
        vault_address: str | None,
        field_values: Sequence[str] | None,
        credential_values: Sequence[str] | None,
        force: bool,
    ) -> AccountConfigurationPathResult:
        if environment.strip().lower() == "live":
            raise ValueError("live accounts are discovered through account connect; only simulated accounts may be provisioned locally")
        workspace = resolve_workspace()
        broker_schema = _broker_schema(broker)
        normalized_model = None if account_model is None else AccountModel(account_model.strip().lower())
        environment_value = environment.strip().lower()
        parsed_fee_rate = _non_negative_decimal(fee_rate, "fee_rate")
        parsed_balances = _asset_amounts(initial_balances)
        account_values = _pairs(field_values)
        credential_field_values = {
            **_provided_credential_values(api_key=api_key, api_secret=api_secret, passphrase=passphrase, wallet_address=wallet_address, private_key=private_key, vault_address=vault_address),
            **_pairs(credential_values),
        }
        if credential is not None and credential.strip().startswith("env:"):
            raise ValueError("credential must be a credential id, not an env: reference")
        credential_id = _created_credential_id(account_id, credential, credential_field_values)
        credential_role_value = _credential_role(credential_role)
        credential_ref = credential_id or credential
        resolved_credential_kind = _credential_kind(environment=environment_value, explicit=credential_kind, default=broker_schema.credential_kind, credential_values=credential_field_values)
        path = workspace.accounts_root / f"{account_id}.toml"
        if path.exists() and not force:
            raise ValueError(f"account already exists: {path}")
        credential_path = None if credential_id is None else workspace.credentials_root / f"{credential_id}.toml"
        if credential_path is not None and credential_path.exists() and not force:
            raise ValueError(f"credential already exists: {credential_path}")
        if credential_path is not None:
            self._configuration.write_credential(credential_path, {"id": credential_id, "broker": broker_schema.broker, "kind": resolved_credential_kind, **credential_field_values})
        include_fee_rate = environment_value != "live" or parsed_fee_rate != 0
        self._configuration.write_account(
            path,
            _account_template(
                account_id,
                broker=broker_schema.broker,
                environment=environment,
                venue=venue or broker_schema.venue,
                product_family=product_family or broker_schema.default_market,
                account_model=normalized_model,
                default_product_family=broker_schema.default_market,
                initial_balances=parsed_balances,
                fee_rate=parsed_fee_rate if include_fee_rate else None,
                credential=None,
                named_credential_ref=credential_ref,
                named_credential_role=credential_role_value if credential_ref is not None else None,
                credential_kind=None,
                credential_fields=broker_schema.credential_fields,
                credential_values={},
                account_values=account_values,
            ),
        )
        workspace.operations.append("account.simulate", target={"account": account_id}, payload={"path": path, "broker": broker_schema.broker, "environment": environment, "venue": venue or broker_schema.venue, "product_family": product_family or broker_schema.default_market, "account_model": None if normalized_model is None else normalized_model.value, "credential": credential_ref, "credential_role": credential_role_value if credential_ref is not None else None, "credential_path": credential_path})
        return AccountConfigurationPathResult(path)


def _broker_schema(broker: str) -> AccountBrokerSchema:
    normalized = PROVIDER_ALIASES.get(broker.strip().lower().replace("-", "_"), broker.strip().lower().replace("-", "_"))
    try:
        return ACCOUNT_SCHEMAS[normalized]
    except KeyError as error:
        raise ValueError(f"unsupported account broker: {broker}; supported: {', '.join(sorted(ACCOUNT_SCHEMAS))}") from error


def _provided_credential_values(*, api_key: str | None, api_secret: str | None, passphrase: str | None, wallet_address: str | None, private_key: str | None, vault_address: str | None) -> dict[str, str]:
    values = {"api_key": api_key, "api_secret": api_secret, "passphrase": passphrase, "wallet_address": wallet_address, "private_key": private_key, "vault_address": vault_address}
    return {key: value for key, value in values.items() if value is not None}


def _created_credential_id(account_id: str, credential: str | None, credential_values: Mapping[str, str]) -> str | None:
    if not credential_values:
        return None
    credential_id = (credential or account_id).strip()
    if not credential_id:
        raise ValueError("credential id is required when credential values are provided")
    if ":" in credential_id:
        raise ValueError("credential must be a credential id, not an env: reference")
    return credential_id


def _credential_role(role: str | None) -> str:
    value = (role or "readonly").strip().lower().replace("-", "_")
    if value not in {"readonly", "read_only", "trade"}:
        raise ValueError("credential role must be readonly or trade")
    return "readonly" if value in {"readonly", "read_only"} else "trade"


def _credential_kind(*, environment: str, explicit: str | None, default: str, credential_values: Mapping[str, str]) -> str | None:
    if explicit is not None:
        return explicit
    if environment in {"live", "testnet"} or credential_values:
        return default
    return None


def _pairs(values: Sequence[str] | None) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in values or ():
        if "=" not in item:
            raise ValueError(f"field must be key=value: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"field key is empty: {item}")
        pairs[key] = value
    return pairs


def _asset_amounts(values: Sequence[str] | None) -> dict[str, Decimal]:
    amounts: dict[str, Decimal] = {}
    for item in values or ():
        if "=" not in item:
            raise ValueError(f"balance must be ASSET=QUANTITY: {item}")
        asset, raw_quantity = item.split("=", 1)
        asset = asset.strip().upper()
        if not asset:
            raise ValueError(f"balance asset is empty: {item}")
        if asset in amounts:
            raise ValueError(f"duplicate initial balance asset: {asset}")
        amounts[asset] = _non_negative_decimal(raw_quantity, f"initial balance {asset}")
    return amounts


def _account_template(account_id: str, *, broker: str, environment: str, venue: str, product_family: str | None, account_model: AccountModel | None, default_product_family: str, initial_balances: Mapping[str, Decimal], fee_rate: Decimal | None, credential: str | None, named_credential_ref: str | None, named_credential_role: str | None, credential_kind: str | None, credential_fields: Sequence[str], credential_values: Mapping[str, str], account_values: Mapping[str, str]) -> str:
    lines = ["[account]", f'id = "{_toml_escape(account_id)}"', f'broker = "{_toml_escape(broker)}"', f'environment = "{_toml_escape(environment)}"']
    if venue != broker:
        lines.append(f'venue = "{_toml_escape(venue)}"')
    if fee_rate is not None:
        lines.append(f'fee_rate = "{fee_rate}"')
    if credential is not None:
        lines.append(f'credential = "{_toml_escape(credential)}"')
    for key, value in sorted(account_values.items()):
        lines.append(f'{_toml_key(key)} = "{_toml_escape(value)}"')
    segment_key = product_family or default_product_family
    lines.extend(["", f"[segments.{_toml_key(segment_key)}]"])
    if account_model is not None:
        lines.append(f'model = "{account_model.value}"')
    lines.append(f'product_family = "{_toml_escape(segment_key)}"')
    if initial_balances:
        lines.extend(["", "[initial_balances]"])
        for asset, quantity in sorted(initial_balances.items()):
            lines.append(f'{_toml_key(asset)} = "{quantity}"')
    if credential_kind is not None:
        lines.extend(["", "[credential]", f'kind = "{_toml_escape(credential_kind)}"', "ip_bound = true"])
        for field in credential_fields:
            field_value = credential_values.get(field, "")
            lines.append(f'{_toml_key(field)} = "{_toml_escape(field_value)}"')
        for key, value in sorted(credential_values.items()):
            if key not in credential_fields:
                lines.append(f'{_toml_key(key)} = "{_toml_escape(value)}"')
    if named_credential_ref is not None and named_credential_role is not None:
        lines.extend(["", _credential_template(named_credential_role, ref=named_credential_ref).rstrip()])
    return "\n".join(lines) + "\n"


def _credential_template(name: str, *, ref: str) -> str:
    return "\n".join([f"[credentials.{_toml_key(name)}]", f'ref = "{_toml_escape(ref)}"'])


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _toml_key(value: str) -> str:
    if value and all(character.isalnum() or character in "_-" for character in value):
        return value
    return f'"{_toml_escape(value)}"'


def _non_negative_decimal(value: object, source: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{source} must be decimal-compatible") from error
    if parsed < 0:
        raise ValueError(f"{source} cannot be negative")
    return parsed


__all__ = ["AccountSimulationApplication"]
