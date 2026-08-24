"""Secret-safe preview of legacy configuration migrations."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from ..agent import AgentResourceApplication
from ..credential import CredentialConfigurationApplication
from ..workspace import Workspace
from .references import ConfigurationReferenceApplication


@dataclass(frozen=True, slots=True)
class ConfigurationMigrationApplication:
    """Discover upgrade work without reading out or rewriting secret values."""

    workspace: Workspace

    def preview(self) -> dict[str, object]:
        items = [
            *self._legacy_credentials(),
            *self._legacy_notifications(),
            *self._legacy_agent_resources(),
            *self._legacy_massive_environment(),
        ]
        return {
            "migration_count": len(items),
            "status": "upgrade_available" if items else "current",
            "preview_only": True,
            "writes_performed": False,
            "items": items,
            "execution_requirements": [
                "show affected resources and references before each write",
                "collect missing SecretRefs without echoing secret values",
                "validate and manually test new resources before switching Launch references",
                "save atomically and retain a recoverable backup",
            ],
        }

    def _legacy_credentials(self) -> list[dict[str, object]]:
        references = ConfigurationReferenceApplication(self.workspace)
        return [
            {
                "migration_id": f"credential:{value['credential_id']}",
                "kind": "plaintext_credential",
                "source": f"config/credentials/{value['credential_id']}.toml",
                "target": f"credential {value['credential_id']} field SecretRefs",
                "action": "replace each plaintext field with an env/file SecretRef",
                "secret_action": "user must provide a SecretRef identity; old values are never displayed",
                "references": references.credential_references(
                    str(value["credential_id"])
                ),
                "removal_condition": "new SecretRefs pass static validation and every dependent resource is manually retested",
            }
            for value in CredentialConfigurationApplication(self.workspace).list()
            if value.get("legacy_plaintext") is True
        ]

    def _legacy_notifications(self) -> list[dict[str, object]]:
        path = self.workspace.paths.notification_config()
        value = _read(path)
        destinations = value.get("destinations")
        if not isinstance(destinations, Mapping):
            return []
        forbidden = {"webhook_url", "bot_token", "signing_secret", "secret", "token"}
        result: list[dict[str, object]] = []
        for destination_id, destination in destinations.items():
            if not isinstance(destination, Mapping):
                continue
            fields = sorted(
                forbidden.intersection(str(key).lower() for key in destination)
            )
            if not fields:
                continue
            result.append(
                {
                    "migration_id": f"notification:{destination_id}",
                    "kind": "inline_notification_secret",
                    "source": _relative(path, self.workspace.paths.root),
                    "target": f"notification destination {destination_id} + Integration credential SecretRef",
                    "action": "move inline provider secret fields to a referenced credential",
                    "secret_action": f"re-enter SecretRefs for {', '.join(fields)}; values are not shown",
                    "references": ConfigurationReferenceApplication(
                        self.workspace
                    ).destination_references(str(destination_id)),
                    "removal_condition": "a real test message succeeds and dependent Launch routes are ready",
                }
            )
        return result

    def _legacy_agent_resources(self) -> list[dict[str, object]]:
        resources = AgentResourceApplication(self.workspace)
        profiles = resources.profile_ids()
        selections = resources.mcp_selections()
        result: list[dict[str, object]] = []
        if profiles:
            result.append(
                {
                    "migration_id": "agent:workspace-profiles",
                    "kind": "workspace_agent_profiles",
                    "source": _relative(
                        self.workspace.paths.agent_profiles_root(),
                        self.workspace.paths.root,
                    ),
                    "target": "inline agent.profile in each selected Launch draft",
                    "action": "select a Launch and expand its referenced Profile into the working draft",
                    "secret_action": "none",
                    "resources": list(profiles),
                    "references": self._legacy_agent_references("profile"),
                    "removal_condition": "no published Launch or draft references a Workspace Profile id",
                }
            )
        if selections:
            result.append(
                {
                    "migration_id": "agent:workspace-mcp",
                    "kind": "workspace_agent_mcp",
                    "source": _relative(
                        self.workspace.paths.agent_mcp_config(),
                        self.workspace.paths.root,
                    ),
                    "target": "inline agent.mcp and tool policy in each selected Launch draft",
                    "action": "select a Launch and expand its MCP server/policy selection into the working draft",
                    "secret_action": "remote MCP credentials remain credential references",
                    "resources": [
                        {"server": server, "profile": profile}
                        for server, profile in selections
                    ],
                    "references": self._legacy_agent_references("mcp"),
                    "removal_condition": "no published Launch or draft uses the legacy MCP selection form",
                }
            )
        return result

    def _legacy_agent_references(self, kind: str) -> list[dict[str, str]]:
        references: list[dict[str, str]] = []
        root = self.workspace.paths.config / "launches"
        paths = [
            *sorted(root.glob("*.toml")),
            *sorted((root / ".drafts").glob("*.toml")),
        ]
        for path in paths:
            agent = _read(path).get("agent")
            if not isinstance(agent, Mapping):
                continue
            if kind == "profile" and isinstance(agent.get("profile"), str):
                references.append(
                    {
                        "source": _relative(path, self.workspace.paths.root),
                        "location": "agent.profile",
                    }
                )
            if kind == "mcp":
                selections = agent.get("mcp")
                if isinstance(selections, list) and any(
                    isinstance(item, Mapping)
                    and isinstance(item.get("server"), str)
                    and isinstance(item.get("profile"), str)
                    for item in selections
                ):
                    references.append(
                        {
                            "source": _relative(path, self.workspace.paths.root),
                            "location": "agent.mcp",
                        }
                    )
        return references

    def _legacy_massive_environment(self) -> list[dict[str, object]]:
        has_legacy_environment = bool(os.environ.get("MASSIVE_API_KEY"))
        configured = any(
            item.get("provider") == "massive"
            for item in CredentialConfigurationApplication(self.workspace).list()
        )
        if not has_legacy_environment or configured:
            return []
        return [
            {
                "migration_id": "massive:legacy-environment",
                "kind": "legacy_massive_environment",
                "source": "process environment variable identity MASSIVE_API_KEY",
                "target": "Massive Workspace data connection using an Integration credential",
                "action": "create a Massive credential whose api_key SecretRef is env:MASSIVE_API_KEY, then run the fixed read test",
                "secret_action": "none; reuse the environment variable identity without reading it into output",
                "references": [],
                "removal_condition": "Massive Reference and market sample reads are manually verified",
            }
        ]


def _read(path: Path) -> Mapping[str, Any]:
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


__all__ = ["ConfigurationMigrationApplication"]
