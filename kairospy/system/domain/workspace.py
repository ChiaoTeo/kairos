from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkspaceIdentity:
    workspace_id: str

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace id is required")
        if "/" in self.workspace_id or "\\" in self.workspace_id:
            raise ValueError("workspace id must be a single path component")


@dataclass(frozen=True, slots=True)
class ResourceScopePaths:
    """The common resource layout used by Workspace and instance scopes."""

    root: Path

    def child(self, *parts: str) -> Path:
        if not parts or any(
            not part or part in {".", ".."} or "/" in part or "\\" in part
            for part in parts
        ):
            raise ValueError("scope resource path must contain named components")
        return self.root.joinpath(*parts)

    @property
    def config(self) -> Path:
        return self.child("config")

    @property
    def data(self) -> Path:
        return self.child("data")

    @property
    def state(self) -> Path:
        return self.child("state")

    @property
    def snapshots(self) -> Path:
        return self.child("snapshots")

    @property
    def run(self) -> Path:
        return self.child("run")

    @property
    def logs(self) -> Path:
        return self.child("logs")

    def process_dir(self, component: str) -> Path:
        return self.child("run", component)

    def process_socket(self, component: str) -> Path:
        candidate = self.process_dir(component) / "control.sock"
        if len(str(candidate).encode()) <= 100:
            return candidate
        digest = hashlib.sha256(f"{self.root}:{component}".encode()).hexdigest()[:20]
        return Path("/tmp") / f"kairos-process-{digest}-{component}.sock"

    def process_lock(self, component: str) -> Path:
        return self.process_dir(component) / "process.lock"

    def health_file(self, component: str) -> Path:
        return self.process_dir(component) / "health.json"

    def component_state(self, component: str, *parts: str) -> Path:
        return self.child("state", component, *parts)

    def component_snapshot(self, component: str, *parts: str) -> Path:
        return self.child("snapshots", component, *parts)

    def component_log(self, component: str, *parts: str) -> Path:
        return self.child("logs", component, *parts)


@dataclass(frozen=True, slots=True)
class WorkspacePaths(ResourceScopePaths):
    manifest: Path
    launches: Path

    @property
    def project_root(self) -> Path:
        """Filesystem root from which user strategy modules are imported."""
        return self.root.parent if self.root.name == ".kairos" else self.root

    def child(self, *parts: str) -> Path:
        if not parts or any(not part or part in {".", ".."} for part in parts):
            raise ValueError("workspace child path must contain named components")
        candidate = (self.root.joinpath(*parts)).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise ValueError("workspace resource escapes workspace root") from error
        return candidate

    @property
    def scope(self) -> ResourceScopePaths:
        return ResourceScopePaths(self.root)

    def reference_database(self) -> Path:
        return self.reference_root() / "reference.sqlite"

    def data_root(self) -> Path:
        return self.child("data")

    def reference_root(self) -> Path:
        return self.child("state", "reference")

    def provider_connections_root(self) -> Path:
        """Integration-owned external provider connection profiles."""

        return self.child("config", "integration", "provider-connections")

    def operations_journal(self) -> Path:
        return self.child("state", "operations.jsonl")

    def launch_index(self) -> Path:
        return self.child("state", "launch-index.json")

    def orders_root(self) -> Path:
        return self.child("state", "execution", "orders")

    def process_dir(self, name: str) -> Path:
        return self.scope.process_dir(name)

    def aeron_dir(self) -> Path:
        """Workspace-owned Aeron Media Driver directory."""

        return self.child("run", "aeron", "media")

    def control_socket(self, name: str) -> Path:
        return self.process_socket(name)

    def health_file(self, name: str) -> Path:
        return self.child("run", name, "health.json")

    def process_socket(self, process: str) -> Path:
        return self.scope.process_socket(process)

    def process_lock(self, process: str) -> Path:
        return self.scope.process_lock(process)

    def account_config(self) -> Path:
        return self.child("config", "accounts", "accounts.toml")

    def credentials_root(self) -> Path:
        return self.child("config", "credentials")

    def notification_config(self) -> Path:
        return self.child("config", "notifications", "notifications.toml")

    def agent_profiles_root(self) -> Path:
        return self.child("config", "agents", "profiles")

    def agent_mcp_config(self) -> Path:
        return self.child("config", "agents", "mcp.toml")

    def model_connections_root(self) -> Path:
        """Legacy Model Connection root retained during the AI-resource migration."""

        return self.child("config", "model-connections")

    def model_endpoints_root(self) -> Path:
        return self.child("config", "ai", "endpoints")

    def available_models_root(self) -> Path:
        return self.child("config", "ai", "models")

    def account_state(self) -> Path:
        return self.child("state", "account", "account-state.json")

    def account_snapshot(self) -> Path:
        return self.child("state", "account", "account.snapshot")

    def account_log(self) -> Path:
        return self.child("logs", "account", "account.log")

    def account_leases(self) -> Path:
        return self.child("state", "account-locks")

    def reference_socket(self) -> Path:
        return self.process_socket("reference")

    def reference_health(self) -> Path:
        return self.health_file("reference")

    def risk_socket(self) -> Path:
        return self.process_socket("risk")

    def risk_health(self) -> Path:
        return self.health_file("risk")

    def launch_socket(self, mode: str, launch_id: str, instance_id: str) -> Path:
        return ResourceScopePaths(
            self.launch_instance_root(mode, launch_id, instance_id)
        ).process_socket("strategy")

    def launch_root(self, mode: str, launch_id: str) -> Path:
        return self.child("launches", mode, launch_id)

    def launch_instance_root(self, mode: str, launch_id: str, instance_id: str) -> Path:
        return self.child("launches", mode, launch_id, "instances", instance_id)

    def launch_config(self, launch_id: str) -> Path:
        return self.child("config", "launches", f"{launch_id}.toml")


@dataclass(frozen=True, slots=True)
class Workspace:
    identity: WorkspaceIdentity
    paths: WorkspacePaths
    cli_format: str = "text"

    @property
    def workspace_id(self) -> str:
        return self.identity.workspace_id

    def instance(
        self, mode: str, launch_id: str, instance_id: str = "default"
    ) -> "InstanceWorkspace":
        return InstanceWorkspace(self, mode, launch_id, instance_id)


@dataclass(frozen=True, slots=True)
class InstanceWorkspace:
    """Resource boundary for one launch instance.

    This object owns no business state and exposes no business operations. It
    only makes all runtime resources resolve below one instance directory.
    """

    workspace: Workspace
    mode: str
    launch_id: str
    instance_id: str

    def __post_init__(self) -> None:
        for name, value in (
            ("mode", self.mode),
            ("launch id", self.launch_id),
            ("instance id", self.instance_id),
        ):
            if (
                not value.strip()
                or "/" in value
                or "\\" in value
                or value in {".", ".."}
            ):
                raise ValueError(f"{name} must be a single path component")

    @property
    def paths(self) -> ResourceScopePaths:
        return ResourceScopePaths(self.root)

    @property
    def root(self) -> Path:
        return self.workspace.paths.launch_instance_root(
            self.mode, self.launch_id, self.instance_id
        )

    @staticmethod
    def _parts(parts: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not part or part in {".", ".."} or "/" in part or "\\" in part
            for part in parts
        ):
            raise ValueError(
                "instance resource path must contain single path components"
            )
        return parts

    def socket(self, name: str) -> Path:
        return self.paths.process_socket(self._parts((name,))[0])

    def health(self, name: str) -> Path:
        return self.paths.health_file(self._parts((name,))[0])

    def lock(self, name: str) -> Path:
        return self.paths.process_lock(self._parts((name,))[0])

    def state(self, *parts: str) -> Path:
        return self.paths.child("state", *self._parts(parts))

    def snapshot(self, *parts: str) -> Path:
        return self.paths.child("snapshots", *self._parts(parts))

    def log(self, *parts: str) -> Path:
        return self.paths.child("logs", *self._parts(parts))

    def component_manifest(self) -> Path:
        return self.root / "manifest.json"

    def market_state(self, name: str) -> Path:
        return self.state("market", name)

    def normalized_config(self) -> Path:
        return self.paths.child("config", "normalized.json")

    def artifact(self, *parts: str) -> Path:
        return self.paths.child("artifacts", *self._parts(parts))

    def launch_status(self) -> Path:
        return self.state("launch", "status.json")

    def launch_command(self) -> Path:
        return self.state("launch", "command.json")

    def launch_database(self) -> Path:
        return self.state("launch", "run.sqlite")

    def lifecycle_journal(self) -> Path:
        return self.state("launch", "lifecycle.jsonl")

    def checkpoint(self, component: str, *parts: str) -> Path:
        return self.state(
            self._parts((component,))[0], "checkpoints", *self._parts(parts)
        )

    def prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
