"""Workspace-level resource lifecycle with cross-owner reference safety."""

from __future__ import annotations

from dataclasses import dataclass

from kairospy.system.domain.workspace import Workspace
from .references import ConfigurationReferenceApplication


@dataclass(frozen=True, slots=True)
class WorkspaceResourceLifecycleApplication:
    """Coordinate destructive resource operations without bypassing owners."""

    workspace: Workspace

    def deletion_impact(self, resource_kind: str, resource_id: str) -> dict[str, object]:
        return ConfigurationReferenceApplication(self.workspace).deletion_impact(
            resource_kind, resource_id
        )

    def delete(
        self, resource_kind: str, resource_id: str, *, force: bool = False
    ) -> dict[str, object]:
        impact = self.deletion_impact(resource_kind, resource_id)
        if not impact["allowed"] and not force:
            raise ValueError(
                f"resource {resource_kind}:{resource_id} is referenced; "
                "inspect deletion_impact or explicitly force deletion"
            )
        if resource_kind == "account":
            from kairospy.investment.apps.account.application import AccountConfigurationApplication

            result = AccountConfigurationApplication(self.workspace).delete(
                resource_id, force=force
            )
        elif resource_kind == "market_data":
            from kairospy.investment.apps.reference.application import (
                ReferenceProviderConfigurationApplication,
            )

            result = ReferenceProviderConfigurationApplication(self.workspace).delete(
                resource_id
            )
        elif resource_kind == "ai_model":
            from kairospy.strategy.apps.agent.application import AgentResourceApplication

            result = AgentResourceApplication(self.workspace).delete_model_connection(
                resource_id
            )
        elif resource_kind == "notification":
            from kairospy.strategy.apps.notification.application import NotificationAdminApplication

            result = NotificationAdminApplication(self.workspace).delete(resource_id)
        else:
            raise ValueError(f"unsupported resource kind: {resource_kind}")
        return {
            **dict(result),
            "resource_kind": resource_kind,
            "forced": force and not bool(impact["allowed"]),
            "orphaned_references": impact["references"] if force else [],
        }


__all__ = ["WorkspaceResourceLifecycleApplication"]
