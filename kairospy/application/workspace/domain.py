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
class WorkspacePaths:
    root: Path
    manifest: Path
    config: Path
    state: Path
    run: Path
    logs: Path
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

    def reference_database(self) -> Path:
        return self.child("reference", "reference.sqlite")

    def data_root(self) -> Path:
        return self.child("data")

    def reference_root(self) -> Path:
        return self.child("reference")

    def market_connections_root(self) -> Path:
        return self.child("market", "connections")

    def operations_journal(self) -> Path:
        return self.child("state", "operations.jsonl")

    def launch_index(self) -> Path:
        return self.child("state", "launch-index.json")

    def orders_root(self) -> Path:
        return self.child("orders", "journals")

    def process_dir(self, name: str) -> Path:
        return self.child("run", name)

    def control_socket(self, name: str) -> Path:
        return self.process_socket(name)

    def health_file(self, name: str) -> Path:
        return self.child("run", name, "health.json")

    def process_socket(self, process: str) -> Path:
        return self.child("run", process, f"{process}.sock")

    def account_config(self) -> Path:
        return self.child("accounts", "accounts.toml")

    def credential_config(self) -> Path:
        return self.child("credentials", "credentials.toml")

    def account_state(self) -> Path:
        return self.child("state", "account", "account-state.json")

    def account_snapshot(self) -> Path:
        return self.child("state", "account", "account.snapshot")

    def account_log(self) -> Path:
        return self.child("logs", "account", "account.log")

    def account_leases(self) -> Path:
        return self.child("state", "account-locks")

    def reference_socket(self) -> Path:
        return self.child("run", "reference", "reference.sock")

    def reference_health(self) -> Path:
        return self.child("run", "reference", "health.json")

    def reference_snapshot(self, view: str = "catalog") -> Path:
        if view not in {"catalog", "markets", "lifecycle"}:
            raise ValueError(f"unsupported reference snapshot view: {view}")
        return self.child("snapshots", "reference", f"{view}.snapshot")

    def risk_socket(self) -> Path:
        return self.child("run", "risk", "risk.sock")

    def risk_health(self) -> Path:
        return self.child("run", "risk", "health.json")

    def launch_socket(self, mode: str, launch_id: str, instance_id: str) -> Path:
        candidate = self.child("launches", mode, launch_id, "instances", instance_id, "strategy.sock")
        # macOS limits AF_UNIX socket addresses to a small fixed byte length.
        # Keep the logical workspace path for normal roots, but use a stable
        # short alias when temporary or deeply nested roots would not bind.
        if len(str(candidate).encode()) <= 100:
            return candidate
        digest = hashlib.sha256(
            f"{self.root}:{mode}:{launch_id}:{instance_id}".encode()
        ).hexdigest()[:20]
        return Path("/tmp") / f"kairos-strategy-{digest}.sock"

    def launch_root(self, mode: str, launch_id: str) -> Path:
        return self.child("launches", mode, launch_id)

    def launch_instance_root(self, mode: str, launch_id: str, instance_id: str) -> Path:
        return self.child("launches", mode, launch_id, "instances", instance_id)

    def instance_socket(self, mode: str, launch_id: str, instance_id: str, name: str) -> Path:
        candidate = self.launch_instance_root(mode, launch_id, instance_id) / "sockets" / f"{name}.sock"
        # macOS limits AF_UNIX socket addresses to a small fixed byte length.
        # Instance paths contain a generated UUID and can exceed that limit
        # even when the workspace itself is valid.  Keep the logical path
        # where possible and use a stable alias for long paths.
        if len(str(candidate).encode()) <= 100:
            return candidate
        digest = hashlib.sha256(
            f"{self.root}:{mode}:{launch_id}:{instance_id}:{name}".encode()
        ).hexdigest()[:20]
        return Path("/tmp") / f"kairos-instance-{digest}-{name}.sock"

    def instance_health(self, mode: str, launch_id: str, instance_id: str, name: str) -> Path:
        return self.launch_instance_root(mode, launch_id, instance_id) / "health" / f"{name}.json"

    def instance_state(self, mode: str, launch_id: str, instance_id: str, *parts: str) -> Path:
        return self.launch_instance_root(mode, launch_id, instance_id).joinpath("state", *parts)

    def instance_snapshot(self, mode: str, launch_id: str, instance_id: str, *parts: str) -> Path:
        return self.launch_instance_root(mode, launch_id, instance_id).joinpath("snapshots", *parts)

    def instance_log(self, mode: str, launch_id: str, instance_id: str, *parts: str) -> Path:
        return self.launch_instance_root(mode, launch_id, instance_id).joinpath("logs", *parts)

    def instance_manifest(self, mode: str, launch_id: str, instance_id: str) -> Path:
        return self.instance_state(mode, launch_id, instance_id, "component-endpoints.json")

    def launch_config(self, launch_id: str) -> Path:
        return self.child("config", "launches", f"{launch_id}.toml")


@dataclass(frozen=True, slots=True)
class Workspace:
    identity: WorkspaceIdentity
    paths: WorkspacePaths
    cli_format: str = "json"

    @property
    def workspace_id(self) -> str:
        return self.identity.workspace_id

    def instance(self, mode: str, launch_id: str, instance_id: str = "default") -> "InstanceWorkspace":
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
        for name, value in (("mode", self.mode), ("launch id", self.launch_id), ("instance id", self.instance_id)):
            if not value.strip() or "/" in value or "\\" in value or value in {".", ".."}:
                raise ValueError(f"{name} must be a single path component")

    @property
    def paths(self) -> WorkspacePaths:
        return self.workspace.paths

    @property
    def root(self) -> Path:
        return self.paths.launch_instance_root(self.mode, self.launch_id, self.instance_id)

    @staticmethod
    def _parts(parts: tuple[str, ...]) -> tuple[str, ...]:
        if any(not part or part in {".", ".."} or "/" in part or "\\" in part for part in parts):
            raise ValueError("instance resource path must contain single path components")
        return parts

    def socket(self, name: str) -> Path:
        return self.paths.instance_socket(self.mode, self.launch_id, self.instance_id, self._parts((name,))[0])

    def health(self, name: str) -> Path:
        return self.paths.instance_health(self.mode, self.launch_id, self.instance_id, self._parts((name,))[0])

    def state(self, *parts: str) -> Path:
        return self.paths.instance_state(self.mode, self.launch_id, self.instance_id, *self._parts(parts))

    def snapshot(self, *parts: str) -> Path:
        return self.paths.instance_snapshot(self.mode, self.launch_id, self.instance_id, *self._parts(parts))

    def log(self, *parts: str) -> Path:
        return self.paths.instance_log(self.mode, self.launch_id, self.instance_id, *self._parts(parts))

    def component_manifest(self) -> Path:
        return self.paths.instance_manifest(self.mode, self.launch_id, self.instance_id)

    def market_state(self, name: str) -> Path:
        return self.state("market", name)

    def prepare(self) -> None:
        for directory in (self.root, self.root / "sockets", self.root / "health", self.root / "state", self.root / "snapshots", self.root / "logs", self.root / "checkpoints"):
            directory.mkdir(parents=True, exist_ok=True)
