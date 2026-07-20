from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path

from kairospy.configuration import DEFAULT_LAKE_ROOT


class StudyWorkspaceStatus(StrEnum):
    SANDBOX = "sandbox"
    FROZEN_CANDIDATE = "frozen_candidate"


@dataclass(frozen=True, slots=True)
class StudyWorkspace:
    study_id: str
    version: str
    hypothesis: str
    input_release_id: str
    input_content_hash: str
    primary_time: str
    start: str
    end: str
    status: StudyWorkspaceStatus = StudyWorkspaceStatus.SANDBOX
    created_at: str = ""
    frozen_at: str | None = None

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (
            self.study_id, self.version, self.hypothesis, self.input_release_id,
            self.input_content_hash, self.primary_time, self.start, self.end,
        )):
            raise ValueError("study workspace identity, hypothesis, input, time semantics and range are required")
        if len(self.input_content_hash) != 64:
            raise ValueError("study input content hash must be SHA-256")
        if self.start >= self.end:
            raise ValueError("study range must use increasing [start, end) values")

    @property
    def candidate_hash(self) -> str:
        payload = asdict(self)
        payload.pop("status", None); payload.pop("created_at", None); payload.pop("frozen_at", None)
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class StudyWorkspaceRepository:
    """Separates flexible sandbox metadata from immutable governed validation artifacts."""

    def __init__(self, root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.root = Path(root)

    def create(self, workspace: StudyWorkspace) -> Path:
        if workspace.status is not StudyWorkspaceStatus.SANDBOX:
            raise ValueError("new study workspace must start in sandbox")
        directory = self.root/"study-workspaces"/workspace.study_id/workspace.version
        directory.mkdir(parents=True, exist_ok=True)
        path = directory/"workspace.json"
        payload = _payload(replace(
            workspace, created_at=workspace.created_at or datetime.now(timezone.utc).isoformat(),
        ))
        if path.exists() and json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError("study workspace version already exists with different semantics")
        if not path.exists(): _write(path, payload)
        return path

    def load(self, study_id: str, version: str) -> StudyWorkspace:
        path = self.root/"study-workspaces"/study_id/version/"workspace.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = StudyWorkspaceStatus(payload["status"])
        payload.pop("candidate_hash", None)
        return StudyWorkspace(**payload)

    def freeze(self, study_id: str, version: str) -> Path:
        workspace = self.load(study_id, version)
        frozen = replace(
            workspace, status=StudyWorkspaceStatus.FROZEN_CANDIDATE,
            frozen_at=workspace.frozen_at or datetime.now(timezone.utc).isoformat(),
        )
        directory = self.root/"study-candidates"/study_id/version
        directory.mkdir(parents=True, exist_ok=True)
        target = directory/"study_candidate.json"
        payload = _payload(frozen)
        if target.exists() and json.loads(target.read_text(encoding="utf-8")) != payload:
            raise ValueError("frozen study candidate is immutable")
        if not target.exists(): _write(target, payload)
        manifest = {
            "schema_version": 1, "study_id": study_id, "version": version,
            "candidate_hash": frozen.candidate_hash,
            "files": {"study_candidate.json": sha256(target.read_bytes()).hexdigest()},
            "promotion_boundary": "requires StudyValidationResult before Strategy evidence",
        }
        _write(directory/"manifest.json", manifest)
        return directory

    def migrate_sandbox_input(
        self, study_id: str, version: str, *, expected_content_hash: str,
        input_release_id: str, input_content_hash: str, primary_time: str, start: str, end: str,
        reason: str,
    ) -> StudyWorkspace:
        """Auditably migrate a known Sandbox input contract; frozen candidates are never rewritten."""
        workspace = self.load(study_id, version)
        if workspace.status is not StudyWorkspaceStatus.SANDBOX:
            raise ValueError("only a Sandbox workspace can migrate its input contract")
        if workspace.input_content_hash != expected_content_hash:
            raise ValueError("workspace input does not match the expected migration source")
        candidate = self.root/"study-candidates"/study_id/version/"manifest.json"
        if candidate.exists():
            raise ValueError("frozen Study Candidate cannot be migrated; create a new Study version")
        path = self.root/"study-workspaces"/study_id/version/"workspace.json"
        backup = path.with_name("workspace.pre-input-migration.json")
        original = json.loads(path.read_text(encoding="utf-8"))
        if backup.exists() and json.loads(backup.read_text(encoding="utf-8")) != original:
            raise ValueError("workspace migration backup conflicts with current input")
        if not backup.exists():
            _write(backup, original)
        migrated = replace(
            workspace, input_release_id=input_release_id, input_content_hash=input_content_hash,
            primary_time=primary_time, start=start, end=end,
        )
        _write(path, _payload(migrated))
        _write(path.with_name("input_migration.json"), {
            "schema_version": 1, "study_id": study_id, "version": version,
            "from_content_hash": expected_content_hash, "to_content_hash": input_content_hash,
            "reason": reason, "migrated_at": datetime.now(timezone.utc).isoformat(),
        })
        return migrated


def _payload(workspace: StudyWorkspace) -> dict[str, object]:
    return {**asdict(workspace), "status": workspace.status.value, "candidate_hash": workspace.candidate_hash}


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True)+"\n", encoding="utf-8")
