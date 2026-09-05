"""Workspace resource readiness and drift diagnostics for Launch."""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from kairospy.strategy.apps.agent.application import AgentLaunchConfig

if TYPE_CHECKING:
    from .configuration import LaunchConfig


def _workspace_resource_diagnostics(
    config: LaunchConfig, workspace_root: Path
) -> list[dict[str, Any]]:
    """Classify owner-specific readiness without turning optional failures into blockers."""

    from .configuration import _workspace_agent_resource_issues

    diagnostics: list[dict[str, Any]] = []

    def extend(
        owner: str,
        resource: str,
        reasons: tuple[str, ...],
        *,
        severity: str,
        action: str,
    ) -> None:
        diagnostics.extend(
            {
                "owner": owner,
                "resource": resource,
                "severity": severity,
                "reason": reason,
                "action": action,
            }
            for reason in reasons
        )

    extend(
        "Account",
        "accounts",
        _workspace_account_issues(config, workspace_root),
        severity="blocker",
        action="configure and manually test the referenced Account",
    )
    extend(
        "Reference/Market",
        "data_provider",
        _workspace_data_provider_issues(config, workspace_root),
        severity="blocker",
        action="configure and manually test the selected data connection",
    )
    diagnostics.extend(_workspace_reference_diagnostics(config, workspace_root))
    notifications = config.notifications
    extend(
        "Notification",
        "destinations",
        _workspace_notification_issues(config, workspace_root),
        severity="blocker" if notifications.get("required", False) else "warning",
        action=(
            "test the destination or disable Notification; optional failure degrades to no delivery"
        ),
    )
    agent = config.agent
    extend(
        "Agent",
        "model_connection",
        tuple(_workspace_agent_resource_issues(config, workspace_root)),
        severity="blocker" if agent.get("required", False) else "warning",
        action=(
            "test the model connection or disable Agent; optional failure degrades to no Agent review"
        ),
    )
    for credential_id, required, reason in _workspace_mcp_credential_issues(
        config, workspace_root
    ):
        extend(
            "Agent",
            f"mcp_credential:{credential_id}",
            (reason,),
            severity="blocker" if required else "warning",
            action=(
                "configure the MCP credential values; optional failure disables that MCP server"
            ),
        )
    return diagnostics


@dataclass(frozen=True, slots=True)
class _ReferenceRequirement:
    kind: str
    identifier: str
    setup_goal: Mapping[str, object] | None = None


def _workspace_reference_diagnostics(
    config: LaunchConfig, workspace_root: Path
) -> list[dict[str, Any]]:
    """Validate selected canonical identities in one Reference read transaction."""

    requirements = _reference_requirements(config)
    if not requirements:
        return []

    from kairospy.contracts.reference import ReferenceClient
    from kairospy.system.apps.workspace.application import WorkspaceApplication

    try:
        workspace = WorkspaceApplication().open(workspace_root)
        client = ReferenceClient(
            database_path=workspace.paths.reference_database(),
            socket_path=workspace.paths.process_socket("reference"),
        )
        with client.read_session() as reference:
            missing = [
                requirement
                for requirement in requirements
                if not _reference_requirement_exists(reference, requirement)
            ]
            watermark = {
                "generation": reference.generation,
                "event_sequence": reference.event_sequence,
            }
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        missing = list(requirements)
        watermark = None
        unavailable = str(error)
    else:
        unavailable = None

    diagnostics: list[dict[str, Any]] = []
    for requirement in missing:
        setup_plan: Mapping[str, object] | None = None
        if requirement.setup_goal is not None and unavailable is None:
            try:
                setup_plan = client.plan_catalog_setup(requirement.setup_goal)
            except (OSError, RuntimeError, ValueError):
                # The owner-provided goal remains executable after Reference starts;
                # launch readiness must not invent a replacement recommendation.
                setup_plan = None
        reason = (
            f"Reference catalog is unavailable for {requirement.kind} "
            f"{requirement.identifier}: {unavailable}"
            if unavailable is not None
            else f"Reference does not contain active {requirement.kind}: {requirement.identifier}"
        )
        diagnostic: dict[str, Any] = {
            "owner": "Reference/Launch",
            "resource": f"{requirement.kind}:{requirement.identifier}",
            "severity": "blocker",
            "reason": reason,
            "action": (
                "prepare the attached Reference catalog setup goal and retry Launch"
                if requirement.setup_goal is not None
                else "select a current canonical identity from Reference and retry Launch"
            ),
        }
        if watermark is not None:
            diagnostic["reference_watermark"] = watermark
        if requirement.setup_goal is not None:
            diagnostic["setup_goal"] = dict(requirement.setup_goal)
        if setup_plan is not None:
            diagnostic["setup_plan"] = dict(setup_plan)
        diagnostics.append(diagnostic)
    return diagnostics


def _reference_requirement_exists(reference: Any, requirement: _ReferenceRequirement) -> bool:
    if requirement.kind == "market_id":
        response = reference.resolve_market(
            market_id=requirement.identifier, active_only=True
        )
        return getattr(response, "market", None) is not None
    if requirement.kind == "instrument_id":
        return bool(
            reference.instruments(
                instrument_ids=(requirement.identifier,), active_only=True, limit=2
            )
        )
    raise ValueError(f"unsupported Reference launch requirement: {requirement.kind}")


def _reference_requirements(config: LaunchConfig) -> tuple[_ReferenceRequirement, ...]:
    """Collect only owner-selected identities; this is not a generic registry."""

    mode_value = config.values.get(config.mode)
    mode_market = mode_value.get("market") if isinstance(mode_value, Mapping) else None
    roots: tuple[object, ...] = (
        config.strategy_params,
        mode_market,
        config.execution.get("routes", ()),
    )
    found: dict[tuple[str, str], _ReferenceRequirement] = {}

    def visit(value: object, inherited_goal: Mapping[str, object] | None = None) -> None:
        if isinstance(value, Mapping):
            raw_goal = value.get("catalog_setup_goal")
            goal = (
                {str(key): item for key, item in raw_goal.items()}
                if isinstance(raw_goal, Mapping)
                else inherited_goal
            )
            for kind in ("market_id", "instrument_id"):
                raw = value.get(kind)
                if isinstance(raw, str) and raw.strip():
                    identifier = raw.strip()
                    found[(kind, identifier)] = _ReferenceRequirement(
                        kind, identifier, goal
                    )
            for kind in ("market_ids", "instrument_ids"):
                raw = value.get(kind)
                if isinstance(raw, (list, tuple)):
                    singular = kind.removesuffix("s")
                    for item in raw:
                        if isinstance(item, str) and item.strip():
                            identifier = item.strip()
                            found[(singular, identifier)] = _ReferenceRequirement(
                                singular, identifier, goal
                            )
            for key, item in value.items():
                if key not in {
                    "catalog_setup_goal",
                    "market_id",
                    "instrument_id",
                    "market_ids",
                    "instrument_ids",
                }:
                    visit(item, goal)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item, inherited_goal)

    for root in roots:
        visit(root)
    return tuple(found[key] for key in sorted(found))


def _structural_diagnostic(reason: str) -> dict[str, str]:
    text = reason.lower()
    if "agent" in text or "mcp" in text:
        owner, resource, action = (
            "Agent/Launch",
            "agent",
            "edit the Launch Agent step or configure and test its model connection",
        )
    elif "notification" in text:
        owner, resource, action = (
            "Notification/Launch",
            "notifications",
            "edit the Launch notification step or test the selected Destination",
        )
    elif "risk" in text or "live.safety" in text:
        owner, resource, action = (
            "Risk/Launch",
            "risk",
            "edit the live risk profile and explicit side-effect bounds",
        )
    elif "account" in text or "execution" in text:
        owner, resource, action = (
            "Account/Execution/Launch",
            "accounts",
            "edit Account selection, segment/trade scope, and Execution routes",
        )
    elif "backtest" in text or "market" in text or "data" in text:
        owner, resource, action = (
            "Market/Launch",
            "market",
            "edit the Launch market/data step",
        )
    else:
        owner, resource, action = (
            "Launch",
            "launch",
            "edit the Launch working draft",
        )
    return {
        "owner": owner,
        "resource": resource,
        "severity": "blocker",
        "reason": reason,
        "action": action,
    }


def _workspace_mcp_credential_issues(
    config: LaunchConfig, workspace_root: Path
) -> tuple[tuple[str, bool, str], ...]:
    if config.mode == "backtest":
        return ()
    try:
        agent = AgentLaunchConfig.from_mapping(config.agent, launch_mode=config.mode)
    except ValueError:
        return ()
    if not agent.enabled:
        return ()
    from kairospy.system.apps.credentials.application import (
        CredentialConfigurationApplication,
    )
    from kairospy.system.apps.workspace.application import WorkspaceApplication

    workspace = WorkspaceApplication().open(workspace_root)
    credentials = CredentialConfigurationApplication(workspace)
    result: list[tuple[str, bool, str]] = []
    for server in agent.mcp:
        credential_id = server.get("credential")
        if not isinstance(credential_id, str) or not credential_id:
            continue
        required = bool(server.get("required", False))
        try:
            summary = credentials.show(credential_id)
        except (KeyError, OSError, ValueError):
            result.append(
                (
                    credential_id,
                    required,
                    f"MCP credential does not exist: {credential_id}",
                )
            )
            continue
        if summary.get("configured") is not True:
            result.append(
                (
                    credential_id,
                    required,
                    f"MCP credential is unavailable or incomplete: {credential_id}",
                )
            )
    return tuple(result)


def _workspace_notification_issues(
    config: LaunchConfig, workspace_root: Path
) -> tuple[str, ...]:
    from .configuration import _normalized_notifications

    notifications = config.notifications
    if not notifications.get("enabled", False):
        return ()
    from kairospy.strategy.apps.notification.application import (
        NotificationAdminApplication,
    )
    from kairospy.system.apps.workspace.application import WorkspaceApplication

    try:
        workspace = WorkspaceApplication().open(workspace_root)
    except (FileNotFoundError, ValueError) as error:
        return (f"cannot validate notification resources: {error}",)
    issues = list(
        NotificationAdminApplication(workspace).validate_resources(
            _normalized_notifications(notifications),
            mode=config.mode,
            resolve_secrets=False,
        )
    )
    if config.mode != "backtest":
        routes = notifications.get("routes", {})
        destination_ids = {
            str(destination_id)
            for destinations in (routes.values() if isinstance(routes, Mapping) else ())
            if isinstance(destinations, list)
            for destination_id in destinations
        }
        admin = NotificationAdminApplication(workspace)
        for destination_id in sorted(destination_ids):
            try:
                destination = admin.show(destination_id)
            except KeyError:
                continue
            if destination.get("verification_status") != "verified":
                issues.append(
                    f"notification destination requires a successful manual test: {destination_id}"
                )
    return tuple(issues)


def _workspace_account_issues(
    config: LaunchConfig, workspace_root: Path
) -> tuple[str, ...]:
    if config.mode == "backtest" or not config.account_refs:
        return ()
    from kairospy.investment.apps.account.application import (
        AccountConfigurationApplication,
    )
    from kairospy.system.apps.workspace.application import WorkspaceApplication

    try:
        workspace = WorkspaceApplication().open(workspace_root)
    except (FileNotFoundError, ValueError) as error:
        return (f"cannot validate Account resources: {error}",)
    application = AccountConfigurationApplication(workspace)
    issues: list[str] = []
    trade_accounts = {
        str(route.get("account_id"))
        for route in config.execution.get("routes", ())
        if isinstance(route, Mapping) and route.get("account_id")
    }
    for account_id in config.account_refs:
        try:
            account = application.show(account_id)
        except (KeyError, OSError, RuntimeError, ValueError):
            issues.append(f"Account resource does not exist: {account_id}")
            continue
        if account.get("verification_status") != "verified":
            issues.append(
                f"Account requires a successful manual connection test: {account_id}"
            )
            continue
        environment = str(account.get("environment") or "").lower()
        expected_route_environment = (
            "paper" if environment in {"paper", "simulated"} else environment
        )
        route_environments = {
            str(route.get("environment") or "").strip().lower()
            for route in config.execution.get("routes", ())
            if isinstance(route, Mapping)
            and str(route.get("account_id") or "") == account_id
        }
        for route_environment in sorted(route_environments):
            if route_environment != expected_route_environment:
                issues.append(
                    "Execution route environment does not match Account: "
                    f"{account_id} ({route_environment or 'missing'} != "
                    f"{expected_route_environment or 'unknown'})"
                )
        if config.mode == "live" and environment not in {"live", "testnet"}:
            issues.append(
                f"Account environment is not live-compatible: {account_id} "
                f"({environment or 'unknown'})"
            )
        if config.mode == "paper" and environment == "live":
            issues.append(f"Paper Launch cannot select a live Account: {account_id}")
        capabilities = {str(value) for value in account.get("capabilities") or ()}
        purposes = {
            str(value.get("purpose"))
            for value in account.get("access_bindings") or ()
            if isinstance(value, Mapping) and value.get("enabled", True)
        }
        if environment not in {"paper", "simulated"}:
            if "account-read" not in purposes or "read" not in capabilities:
                issues.append(
                    f"Account does not have verified account-read access: {account_id}"
                )
            if account_id in trade_accounts and (
                "order-trade" not in purposes or "trade" not in capabilities
            ):
                issues.append(
                    f"Account does not have verified order-trade access: {account_id}"
                )
    return tuple(issues)


def _workspace_data_provider_issues(
    config: LaunchConfig, workspace_root: Path
) -> tuple[str, ...]:
    if config.mode == "backtest":
        return ()
    mode_value = config.values.get(config.mode)
    market = mode_value.get("market") if isinstance(mode_value, Mapping) else None
    profile = market.get("profile") if isinstance(market, Mapping) else None
    if profile is None:
        return ()
    from kairospy.system.apps.integration.application import (
        ProviderConnectionConfigurationApplication,
    )
    from kairospy.system.apps.workspace.application import WorkspaceApplication

    try:
        workspace = WorkspaceApplication().open(workspace_root)
        connection = ProviderConnectionConfigurationApplication(workspace).show(
            str(profile)
        )
    except (KeyError, FileNotFoundError, OSError, ValueError) as error:
        return (f"Workspace data connection is unavailable: {profile}: {error}",)
    if connection.get("verification_status") != "verified":
        return (f"Data connection requires a successful manual read test: {profile}",)
    capabilities = connection.get("capabilities_verified")
    verified = (
        {str(value) for value in capabilities}
        if isinstance(capabilities, list)
        else set()
    )
    if "market-query" not in verified:
        return (f"Data connection has not verified market-query: {profile}",)
    return ()


def _workspace_resource_snapshots(
    config: LaunchConfig, workspace_root: Path
) -> dict[str, dict[str, Any]]:
    """Resolve secret-free, owner-produced resource snapshots for one Instance."""

    if config.mode == "backtest":
        return {
            "accounts": {},
            "data_providers": {},
            "models": {},
            "notifications": {},
            "mcp_credentials": {},
        }
    from kairospy.investment.apps.account.application import (
        AccountConfigurationApplication,
    )
    from kairospy.strategy.apps.agent.application import AgentResourceApplication
    from kairospy.strategy.apps.notification.application import (
        NotificationAdminApplication,
    )
    from kairospy.system.apps.integration.application import (
        ProviderConnectionConfigurationApplication,
    )
    from kairospy.system.apps.workspace.application import WorkspaceApplication

    workspace = WorkspaceApplication().open(workspace_root)
    accounts = AccountConfigurationApplication(workspace)
    account_snapshots = {
        account_id: accounts.resource_snapshot(account_id).to_json_dict()
        for account_id in config.account_refs
    }

    data_snapshots: dict[str, Any] = {}
    mode_value = config.values.get(config.mode)
    market = mode_value.get("market") if isinstance(mode_value, Mapping) else None
    if isinstance(market, Mapping) and market.get("profile"):
        connection_id = str(market["profile"])
        data_snapshots[connection_id] = ProviderConnectionConfigurationApplication(
            workspace
        ).resource_snapshot(connection_id)

    model_snapshots: dict[str, Any] = {}
    agent = AgentLaunchConfig.from_mapping(config.agent, launch_mode=config.mode)
    if agent.enabled and agent.model is not None:
        try:
            resources = AgentResourceApplication(workspace)
            if agent.model.ref is not None:
                model_snapshots[agent.model.ref] = resources.available_model_snapshot(
                    agent.model.ref
                )
            else:
                model_snapshots[agent.model.connection] = resources.resource_snapshot(
                    agent.model.connection, model=agent.model.model
                )
        except (KeyError, FileNotFoundError, OSError, RuntimeError, ValueError):
            if agent.required:
                raise

    from kairospy.system.apps.credentials.application import (
        CredentialConfigurationApplication,
    )

    credential_owner = CredentialConfigurationApplication(workspace)
    mcp_credential_snapshots: dict[str, Any] = {}
    for server in agent.mcp:
        credential_id = server.get("credential")
        if not isinstance(credential_id, str) or not credential_id:
            continue
        required = bool(server.get("required", False))
        try:
            snapshot = credential_owner.resource_snapshot(credential_id)
        except (KeyError, FileNotFoundError, OSError, RuntimeError, ValueError):
            if required:
                raise
            continue
        snapshot["required"] = required
        mcp_credential_snapshots[credential_id] = snapshot

    notification_snapshots: dict[str, Any] = {}
    notifications = config.notifications
    if notifications.get("enabled", False):
        routes = notifications.get("routes", {})
        destination_ids = {
            str(destination_id)
            for destinations in (routes.values() if isinstance(routes, Mapping) else ())
            if isinstance(destinations, list)
            for destination_id in destinations
        }
        notification_owner = NotificationAdminApplication(workspace)
        for destination_id in sorted(destination_ids):
            try:
                notification_snapshots[destination_id] = (
                    notification_owner.resource_snapshot(destination_id)
                )
            except (KeyError, FileNotFoundError, OSError, RuntimeError, ValueError):
                if notifications.get("required", False):
                    raise
    return {
        "accounts": account_snapshots,
        "data_providers": data_snapshots,
        "models": model_snapshots,
        "notifications": notification_snapshots,
        "mcp_credentials": mcp_credential_snapshots,
    }


def _current_resource_hashes(
    snapshots: Mapping[str, Any], workspace_root: Path
) -> dict[str, str]:
    from kairospy.investment.apps.account.application import (
        AccountConfigurationApplication,
    )
    from kairospy.strategy.apps.agent.application import AgentResourceApplication
    from kairospy.strategy.apps.notification.application import (
        NotificationAdminApplication,
    )
    from kairospy.system.apps.integration.application import (
        ProviderConnectionConfigurationApplication,
    )
    from kairospy.system.apps.credentials.application import (
        CredentialConfigurationApplication,
    )
    from kairospy.system.apps.workspace.application import WorkspaceApplication

    workspace = WorkspaceApplication().open(workspace_root)
    owners = {
        "accounts": AccountConfigurationApplication(workspace),
        "data_providers": ProviderConnectionConfigurationApplication(workspace),
        "models": AgentResourceApplication(workspace),
        "notifications": NotificationAdminApplication(workspace),
        "mcp_credentials": CredentialConfigurationApplication(workspace),
    }
    result: dict[str, str] = {}
    for kind, resources in snapshots.items():
        if not isinstance(resources, Mapping) or kind not in owners:
            continue
        for resource_id, prior in resources.items():
            if not isinstance(prior, Mapping):
                continue
            try:
                if kind == "accounts":
                    current = (
                        owners[kind].resource_snapshot(str(resource_id)).to_json_dict()
                    )
                elif kind == "data_providers":
                    current = owners[kind].resource_snapshot(str(resource_id))
                elif kind == "models":
                    current = owners[kind].resource_snapshot(
                        str(resource_id), model=str(prior.get("model") or "")
                    )
                else:
                    current = owners[kind].resource_snapshot(str(resource_id))
            except (KeyError, FileNotFoundError, OSError, RuntimeError, ValueError):
                continue
            resource_hash = current.get("resource_hash")
            if isinstance(resource_hash, str):
                result[f"{kind}:{resource_id}"] = resource_hash
    return result
