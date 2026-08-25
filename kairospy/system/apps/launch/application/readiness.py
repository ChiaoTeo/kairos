"""Launch readiness view for Workspace resources."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from kairospy.investment.apps.account.application import AccountConfigurationApplication
from kairospy.strategy.apps.agent.application import AgentResourceApplication
from kairospy.system.apps.configuration.application.references import (
    ConfigurationReferenceApplication,
)
from kairospy.strategy.apps.notification.application import NotificationAdminApplication
from kairospy.system.apps.integration.application import (
    ProviderConnectionConfigurationApplication,
)
from kairospy.system.apps.workspace.application import Workspace


class ResourceKind(StrEnum):
    ACCOUNT = "account"
    MARKET_DATA = "market_data"
    AI_MODEL = "ai_model"
    NOTIFICATION = "notification"


class RuntimeCapability(StrEnum):
    REFERENCE_CATALOG = "reference-catalog"
    MARKET_QUERY = "market-query"
    MARKET_STREAM = "market-stream"
    ACCOUNT_READ = "account-read"
    ORDER_QUERY = "order-query"
    ORDER_TRADE = "order-trade"


class ResourceState(StrEnum):
    INCOMPLETE = "incomplete"
    NEEDS_TEST = "needs_test"
    AVAILABLE = "available"
    CONFIGURATION_CHANGED = "configuration_changed"
    CONNECTION_FAILED = "connection_failed"
    MISSING_CREDENTIAL = "missing_credential"
    DISABLED = "disabled"


class ProbeLevel(StrEnum):
    STATIC = "static"
    CONNECTION = "connection"
    EXTERNAL_EFFECT = "external_effect"


class OperationEffect(StrEnum):
    NONE = "none"
    LOCAL_CONFIGURATION_WRITE = "local_configuration_write"
    NETWORK_READ = "network_read"
    BILLABLE_MODEL_CALL = "billable_model_call"
    EXTERNAL_MESSAGE = "external_message"
    TRADING = "trading"


class ResourceErrorCategory(StrEnum):
    CONFIGURATION_INVALID = "configuration_invalid"
    CREDENTIAL_MISSING = "credential_missing"
    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    RESOURCE_NOT_FOUND = "resource_not_found"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    NETWORK_FAILED = "network_failed"
    PROTOCOL_INCOMPATIBLE = "protocol_incompatible"
    INVALID_RESPONSE = "invalid_response"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ResourceActionDescriptor:
    """Stable action metadata for CLI, Workbench, or another surface."""

    action_id: str
    effect: OperationEffect
    probe_level: ProbeLevel
    confirmation_required: bool
    destructive: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "effect": self.effect.value,
            "probe_level": self.probe_level.value,
            "confirmation_required": self.confirmation_required,
            "destructive": self.destructive,
        }


@dataclass(frozen=True, slots=True)
class ResourceReadiness:
    resource_id: str
    kind: ResourceKind
    state: ResourceState
    configured: bool
    enabled: bool
    selectable: bool
    optional: bool
    last_tested_at: str | None
    error_category: ResourceErrorCategory | None
    issues: tuple[str, ...]
    tested: tuple[str, ...]
    not_tested: tuple[str, ...]
    current_configuration_hash: str | None
    tested_configuration_hash: str | None
    capabilities: tuple[RuntimeCapability, ...] = ()
    references: tuple[Mapping[str, str], ...] = ()

    @property
    def next_action(self) -> str | None:
        return {
            ResourceState.INCOMPLETE: "complete_configuration",
            ResourceState.NEEDS_TEST: "test_connection",
            ResourceState.CONFIGURATION_CHANGED: "retest_connection",
            ResourceState.CONNECTION_FAILED: "inspect_failure",
            ResourceState.MISSING_CREDENTIAL: "replace_credential",
            ResourceState.DISABLED: "enable",
        }.get(self.state)

    def as_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "kind": self.kind.value,
            "state": self.state.value,
            "configured": self.configured,
            "enabled": self.enabled,
            "selectable": self.selectable,
            "optional": self.optional,
            "last_tested_at": self.last_tested_at,
            "error_category": (
                self.error_category.value if self.error_category is not None else None
            ),
            "issues": list(self.issues),
            "tested": list(self.tested),
            "not_tested": list(self.not_tested),
            "current_configuration_hash": self.current_configuration_hash,
            "tested_configuration_hash": self.tested_configuration_hash,
            "capabilities": [value.value for value in self.capabilities],
            "next_action": self.next_action,
            "actions": [action.as_dict() for action in self.actions],
            "references": [dict(reference) for reference in self.references],
            "reference_count": len(self.references),
        }

    @property
    def actions(self) -> tuple[ResourceActionDescriptor, ...]:
        actions = [
            ResourceActionDescriptor(
                "edit_draft", OperationEffect.NONE, ProbeLevel.STATIC, False
            ),
            _test_action(self.kind),
            ResourceActionDescriptor(
                "commit_draft",
                OperationEffect.LOCAL_CONFIGURATION_WRITE,
                ProbeLevel.STATIC,
                True,
            ),
        ]
        if self.state is ResourceState.DISABLED:
            actions.append(
                ResourceActionDescriptor(
                    "enable",
                    OperationEffect.LOCAL_CONFIGURATION_WRITE,
                    ProbeLevel.STATIC,
                    True,
                )
            )
        if self.selectable:
            actions.append(
                ResourceActionDescriptor(
                    "select", OperationEffect.NONE, ProbeLevel.STATIC, False
                )
            )
        actions.append(
            ResourceActionDescriptor(
                "delete",
                OperationEffect.LOCAL_CONFIGURATION_WRITE,
                ProbeLevel.STATIC,
                True,
                destructive=True,
            )
        )
        return tuple(actions)


def project_resource_readiness(
    kind: ResourceKind,
    value: Mapping[str, object],
    *,
    optional: bool = False,
    references: Sequence[Mapping[str, str]] = (),
) -> ResourceReadiness:
    resource_id = _resource_id(kind, value)
    issues = tuple(str(item) for item in cast(Any, value.get("issues") or ()))
    enabled = bool(value.get("enabled", value.get("status") != "disabled"))
    configured = _configured(kind, value, issues)
    verification = str(value.get("verification_status") or "pending")
    if not enabled or verification == "disabled":
        state = ResourceState.DISABLED
    elif not configured and _credential_issue(issues):
        state = ResourceState.MISSING_CREDENTIAL
    elif not configured:
        state = ResourceState.INCOMPLETE
    elif verification == "verified":
        state = ResourceState.AVAILABLE
    elif verification == "retest_required":
        state = ResourceState.CONFIGURATION_CHANGED
    elif verification == "failed":
        state = ResourceState.CONNECTION_FAILED
    else:
        state = ResourceState.NEEDS_TEST
    return ResourceReadiness(
        resource_id=resource_id,
        kind=kind,
        state=state,
        configured=configured,
        enabled=enabled,
        selectable=state is ResourceState.AVAILABLE,
        optional=optional,
        last_tested_at=_optional_text(value.get("last_tested_at")),
        error_category=normalize_error_category(value.get("error_category")),
        issues=issues,
        tested=tuple(str(item) for item in cast(Any, value.get("tested") or ())),
        not_tested=tuple(
            str(item) for item in cast(Any, value.get("not_tested") or ())
        ),
        current_configuration_hash=_optional_text(
            value.get("current_configuration_hash")
        ),
        tested_configuration_hash=_optional_text(
            value.get("tested_configuration_hash")
        ),
        capabilities=_resource_capabilities(kind, value, state),
        references=tuple(dict(reference) for reference in references),
    )


@dataclass(frozen=True, slots=True)
class RunReadinessApplication:
    workspace: Workspace

    def resources(self) -> tuple[ResourceReadiness, ...]:
        result: list[ResourceReadiness] = []
        references = ConfigurationReferenceApplication(self.workspace)
        result.extend(
            project_resource_readiness(
                ResourceKind.ACCOUNT,
                value,
                references=references.account_references(str(value["account_id"])),
            )
            for value in AccountConfigurationApplication(self.workspace).list()
        )
        result.extend(
            project_resource_readiness(
                ResourceKind.MARKET_DATA,
                value,
                references=references.data_provider_references(
                    str(value["connection_id"])
                ),
            )
            for value in ProviderConnectionConfigurationApplication(
                self.workspace
            ).list()
        )
        result.extend(
            project_resource_readiness(
                ResourceKind.AI_MODEL,
                value,
                references=references.available_model_references(
                    str(value["model_id"])
                ),
            )
            for value in AgentResourceApplication(self.workspace).available_models()
        )
        result.extend(
            project_resource_readiness(
                ResourceKind.NOTIFICATION,
                value,
                optional=True,
                references=references.destination_references(
                    str(value["destination_id"])
                ),
            )
            for value in NotificationAdminApplication(self.workspace).list()
        )
        return tuple(result)

    def overview(
        self,
        *,
        required_kinds: Sequence[ResourceKind] = (),
        required_capabilities: Sequence[RuntimeCapability] = (),
    ) -> dict[str, object]:
        resources = self.resources()
        required_sequence = tuple(dict.fromkeys(required_kinds))
        required = frozenset(required_sequence)
        capability_sequence = tuple(dict.fromkeys(required_capabilities))
        required_capability_set = frozenset(capability_sequence)
        groups: dict[str, dict[str, object]] = {}
        for kind in ResourceKind:
            values = tuple(value for value in resources if value.kind is kind)
            available = sum(value.selectable for value in values)
            groups[kind.value] = {
                "count": len(values),
                "available": available,
                "needs_action": sum(
                    value.state is not ResourceState.AVAILABLE for value in values
                ),
                "optional": kind is ResourceKind.NOTIFICATION,
                "required": kind in required,
                "referenced": sum(bool(value.references) for value in values),
            }
        available_kinds = {value.kind for value in resources if value.selectable}
        available_capabilities = {
            capability
            for value in resources
            if value.selectable
            for capability in value.capabilities
        }
        blockers = [
            value.as_dict()
            for value in resources
            if value.kind in required
            and value.kind not in available_kinds
            and not value.selectable
        ]
        missing_required = [
            kind.value for kind in required_sequence if kind not in available_kinds
        ]
        missing_capabilities = [
            capability.value
            for capability in capability_sequence
            if capability not in available_capabilities
        ]
        capability_blockers = [
            value.as_dict()
            for value in resources
            if set(value.capabilities) & required_capability_set
            and not value.selectable
        ]
        return {
            "groups": groups,
            "resources": [value.as_dict() for value in resources],
            "needs_action": sum(
                value.state is not ResourceState.AVAILABLE and not value.optional
                for value in resources
            ),
            "blocking_resources": [*blockers, *capability_blockers],
            "missing_required_kinds": missing_required,
            "missing_required_capabilities": missing_capabilities,
            "available_capabilities": sorted(
                capability.value for capability in available_capabilities
            ),
            "ready": not blockers
            and not capability_blockers
            and not missing_required
            and not missing_capabilities,
        }


def normalize_error_category(value: object) -> ResourceErrorCategory | None:
    if value is None or not str(value).strip():
        return None
    normalized = str(value).strip().lower()
    direct = {category.value: category for category in ResourceErrorCategory}
    if normalized in direct:
        return direct[normalized]
    aliases = {
        "authentication_or_permission": ResourceErrorCategory.AUTHENTICATION_FAILED,
        "rate_limit_or_quota": ResourceErrorCategory.RATE_LIMITED,
        "network_unavailable": ResourceErrorCategory.NETWORK_FAILED,
        "network_error": ResourceErrorCategory.NETWORK_FAILED,
        "protocol_or_response_invalid": ResourceErrorCategory.PROTOCOL_INCOMPATIBLE,
        "model_or_endpoint_not_found": ResourceErrorCategory.RESOURCE_NOT_FOUND,
        "provider_http_error": ResourceErrorCategory.PROVIDER_UNAVAILABLE,
        "provider_error": ResourceErrorCategory.PROVIDER_UNAVAILABLE,
    }
    return aliases.get(normalized, ResourceErrorCategory.UNKNOWN)


def _resource_id(kind: ResourceKind, value: Mapping[str, object]) -> str:
    fields = {
        ResourceKind.ACCOUNT: ("account_id",),
        ResourceKind.MARKET_DATA: ("connection_id",),
        ResourceKind.AI_MODEL: ("model_id", "connection_id"),
        ResourceKind.NOTIFICATION: ("destination_id",),
    }[kind]
    for field in fields:
        identifier = value.get(field)
        if isinstance(identifier, str) and identifier.strip():
            return identifier.strip()
    raise ValueError(f"{kind.value} readiness value is missing its resource id")


def _credential_issue(issues: Sequence[str]) -> bool:
    return any(
        marker in issue.lower()
        for issue in issues
        for marker in ("secretref", "credential", "凭据", "认证资料")
    )


def _resource_capabilities(
    kind: ResourceKind,
    value: Mapping[str, object],
    state: ResourceState,
) -> tuple[RuntimeCapability, ...]:
    if kind is ResourceKind.MARKET_DATA:
        declared = {str(item) for item in cast(Any, value.get("purposes") or ())}
        verified = {
            str(item) for item in cast(Any, value.get("capabilities_verified") or ())
        }
        aliases = {
            "reference": RuntimeCapability.REFERENCE_CATALOG,
            "equity_market": RuntimeCapability.MARKET_QUERY,
            "options": RuntimeCapability.MARKET_QUERY,
        }
        effective = verified if state is ResourceState.AVAILABLE else declared
        capabilities = {
            capability
            for item in effective
            if (capability := _runtime_capability(item, aliases)) is not None
        }
        return tuple(sorted(capabilities, key=str))
    if kind is ResourceKind.ACCOUNT:
        environment = str(value.get("environment") or "").lower()
        if environment in {"paper", "simulated"}:
            return (
                RuntimeCapability.ACCOUNT_READ,
                RuntimeCapability.ORDER_QUERY,
                RuntimeCapability.ORDER_TRADE,
            )
        purposes = {
            str(item.get("purpose"))
            for item in cast(Any, value.get("access_bindings") or ())
            if isinstance(item, Mapping) and item.get("enabled", True)
        }
        observed = {
            str(item).lower() for item in cast(Any, value.get("capabilities") or ())
        }
        result: set[RuntimeCapability] = set()
        if "account-read" in purposes and (not observed or "read" in observed):
            result.update(
                {RuntimeCapability.ACCOUNT_READ, RuntimeCapability.ORDER_QUERY}
            )
        if "order-trade" in purposes and "trade" in observed:
            result.update(
                {RuntimeCapability.ORDER_QUERY, RuntimeCapability.ORDER_TRADE}
            )
        return tuple(sorted(result, key=str))
    return ()


def _runtime_capability(
    value: str, aliases: Mapping[str, RuntimeCapability]
) -> RuntimeCapability | None:
    if value in aliases:
        return aliases[value]
    try:
        return RuntimeCapability(value)
    except ValueError:
        return None


def _configured(
    kind: ResourceKind, value: Mapping[str, object], issues: Sequence[str]
) -> bool:
    explicit = value.get("configured")
    if isinstance(explicit, bool):
        return explicit
    if kind is ResourceKind.ACCOUNT:
        return bool(
            value.get("account_id")
            and value.get("broker")
            and value.get("environment")
            and value.get("segments")
            and str(value.get("status") or "configured")
            not in {"invalid", "incomplete"}
            and not issues
        )
    return False


def _test_action(kind: ResourceKind) -> ResourceActionDescriptor:
    effect = {
        ResourceKind.ACCOUNT: OperationEffect.NETWORK_READ,
        ResourceKind.MARKET_DATA: OperationEffect.NETWORK_READ,
        ResourceKind.AI_MODEL: OperationEffect.BILLABLE_MODEL_CALL,
        ResourceKind.NOTIFICATION: OperationEffect.EXTERNAL_MESSAGE,
    }[kind]
    level = (
        ProbeLevel.EXTERNAL_EFFECT
        if kind in {ResourceKind.AI_MODEL, ResourceKind.NOTIFICATION}
        else ProbeLevel.CONNECTION
    )
    return ResourceActionDescriptor(
        "test_connection",
        effect,
        level,
        confirmation_required=effect
        in {OperationEffect.BILLABLE_MODEL_CALL, OperationEffect.EXTERNAL_MESSAGE},
    )


def _optional_text(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


__all__ = [
    "OperationEffect",
    "ProbeLevel",
    "ResourceErrorCategory",
    "ResourceActionDescriptor",
    "ResourceKind",
    "ResourceReadiness",
    "ResourceState",
    "RunReadinessApplication",
    "RuntimeCapability",
    "normalize_error_category",
    "project_resource_readiness",
]
