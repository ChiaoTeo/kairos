from __future__ import annotations

import tomllib
import os
from pathlib import Path
from typing import Any, Mapping

from .domain import Workspace, WorkspaceIdentity, WorkspacePaths


class WorkspaceApplication:
    """Open and initialize the one explicit workspace used by a runtime."""

    MANIFEST_NAME = "workspace.toml"

    def resolve(self, root: str | Path | None = None) -> Workspace:
        """Open an explicit workspace or discover one for CLI use.

        Explicit CLI arguments win, followed by ``KAIROS_WORKSPACE`` and the
        current directory ancestry. Discovery never creates a workspace.
        """
        candidate = root or os.environ.get("KAIROS_WORKSPACE")
        if candidate is not None:
            return self.open(candidate)
        current = Path.cwd().resolve()
        for directory in (current, *current.parents):
            if (directory / self.MANIFEST_NAME).is_file() or (directory / ".kairos" / "kairos.toml").is_file():
                return self.open(directory)
        raise FileNotFoundError(
            "workspace not found; run 'kairos project init' "
            "or pass --workspace"
        )

    def init(self, root: str | Path, *, workspace_id: str | None = None) -> Workspace:
        root_path = Path(root).expanduser().resolve()
        root_path.mkdir(parents=True, exist_ok=True)
        manifest = root_path / self.MANIFEST_NAME
        if manifest.exists():
            raise FileExistsError(f"workspace already exists: {manifest}")
        identity = WorkspaceIdentity(workspace_id or root_path.name)
        manifest.write_text(
            f'version = 1\nworkspace_id = "{identity.workspace_id}"\n\n[cli]\nformat = "json"\n',
            encoding="utf-8",
        )
        workspace = self.open(root_path)
        for directory in (workspace.paths.config, workspace.paths.config / "launches", workspace.paths.state, workspace.paths.run,
                          workspace.paths.logs, workspace.paths.launches,
                          workspace.paths.data_root(), workspace.paths.reference_root(),
                          workspace.paths.market_connections_root(),
                          workspace.paths.orders_root(),
                          workspace.paths.operations_journal().parent,
                          workspace.paths.launch_index().parent,
                          workspace.paths.account_config().parent,
                          workspace.paths.credential_config().parent,
                          workspace.paths.account_state().parent,
                          workspace.paths.account_log().parent,
                          workspace.paths.account_leases()):
            directory.mkdir(parents=True, exist_ok=True)
        return workspace

    def init_project(self, project_root: str | Path, *, workspace_id: str | None = None) -> Workspace:
        """Initialize the recommended project/.kairos workspace layout."""
        project = Path(project_root).expanduser().resolve()
        legacy_manifest = project / self.MANIFEST_NAME
        if legacy_manifest.is_file():
            raise FileExistsError(
                f"legacy workspace manifest already exists: {legacy_manifest}; "
                "remove it or use a different project directory"
            )
        storage = project / ".kairos"
        storage.mkdir(parents=True, exist_ok=True)
        manifest = storage / "kairos.toml"
        if manifest.exists():
            raise FileExistsError(f"workspace manifest already exists: {manifest}")
        identity = WorkspaceIdentity(workspace_id or project.name)
        manifest.write_text(
            f'version = 1\nworkspace_id = "{identity.workspace_id}"\n\n[cli]\nformat = "json"\n',
            encoding="utf-8",
        )
        # The legacy root-level manifest was rejected above, so open() will
        # discover and return the project/.kairos storage root.
        workspace = self.open(project)
        for directory in (workspace.paths.config, workspace.paths.config / "launches", workspace.paths.state, workspace.paths.run,
                          workspace.paths.logs, workspace.paths.launches,
                          workspace.paths.data_root(), workspace.paths.reference_root(),
                          workspace.paths.market_connections_root(),
                          workspace.paths.orders_root(), workspace.paths.account_config().parent,
                          workspace.paths.credential_config().parent,
                          workspace.paths.account_state().parent, workspace.paths.account_log().parent,
                          workspace.paths.account_leases()):
            directory.mkdir(parents=True, exist_ok=True)
        return workspace

    def open(self, root: str | Path | None) -> Workspace:
        if root is None:
            return self.resolve()
        root_path = Path(root).expanduser().resolve()
        manifest = root_path / self.MANIFEST_NAME
        if not manifest.is_file() and (root_path / "kairos.toml").is_file():
            manifest = root_path / "kairos.toml"
        if not manifest.is_file() and (root_path / ".kairos" / "kairos.toml").is_file():
            root_path = root_path / ".kairos"
            manifest = root_path / "kairos.toml"
        if not root_path.is_dir():
            raise FileNotFoundError(f"workspace directory does not exist: {root_path}")
        try:
            values = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise FileNotFoundError(f"workspace manifest is required: {manifest}") from error
        if values.get("version") != 1:
            raise ValueError("workspace.toml version must be 1")
        identity = WorkspaceIdentity(values.get("workspace_id", ""))
        cli = values.get("cli", {})
        cli_format = cli.get("format", "json") if isinstance(cli, dict) else "json"
        if cli_format not in {"text", "json"}:
            raise ValueError("workspace cli.format must be text or json")
        paths = WorkspacePaths(
            root=root_path,
            manifest=manifest,
            config=root_path / "config",
            state=root_path / "state",
            run=root_path / "run",
            logs=root_path / "logs",
            launches=root_path / "launches",
        )
        return Workspace(identity, paths, cli_format=cli_format)

    def market_connection(self, workspace: Workspace, connection_id: str) -> dict[str, Any]:
        """Resolve a Workspace-owned Market connection profile."""
        connection_id = connection_id.strip()
        if not connection_id or any(
            part in {"", ".", ".."}
            for part in connection_id.replace("\\", "/").split("/")
        ):
            raise ValueError("market connection id must be a path-safe name")
        values = tomllib.loads(workspace.paths.manifest.read_text(encoding="utf-8"))
        market = values.get("market", {})
        connections = market.get("connections", {}) if isinstance(market, Mapping) else {}
        if isinstance(connections, Mapping):
            configured = connections.get(connection_id)
            if isinstance(configured, Mapping):
                return dict(configured)

        profile = workspace.paths.market_connections_root() / f"{connection_id}.toml"
        if profile.is_file():
            value = tomllib.loads(profile.read_text(encoding="utf-8"))
            configured = value.get("connection", value)
            if isinstance(configured, Mapping):
                return dict(configured)
        raise KeyError(f"market connection does not exist: {connection_id}")
