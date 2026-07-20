from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID

from kairos.domain.event import EventEnvelope
from kairos.domain.market_data import OptionChain
from kairos.study_platform.option_snapshot_analysis import OptionSnapshotAnalysis
from kairos.study_platform.report import write_csv
from kairos.study_platform.snapshot import ResearchSnapshot
from kairos.study_platform.spec import OptionChainCaptureSpec

from .codec import event_from_primitive, event_to_primitive, snapshot_from_primitive, snapshot_to_primitive, to_primitive


class RunStatus(StrEnum):
    CREATED = "created"
    CONNECTING = "connecting"
    DISCOVERING = "discovering"
    COLLECTING = "collecting"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(slots=True)
class RunManifest:
    schema_version: int
    run_id: UUID
    created_at: datetime
    updated_at: datetime
    status: RunStatus
    spec: OptionChainCaptureSpec
    code_version: str
    collected_event_count: int = 0
    selected_contract_count: int = 0
    quality_issue_count: int = 0
    error_type: str | None = None
    error_message: str | None = None
    error_stage: str | None = None
    ibkr_error_code: int | None = None
    offline_analyzable: bool = False


class FileResearchRepository:
    def __init__(self, root: Path | str = "data/snapshots") -> None:
        self.root = Path(root)

    def run_dir(self, run_id: UUID | str, created_at: datetime | None = None) -> Path:
        run_string = str(run_id)
        if created_at is not None:
            return self.root / created_at.date().isoformat() / run_string
        matches = list(self.root.glob(f"*/{run_string}"))
        if not matches:
            raise FileNotFoundError(f"run not found: {run_string}")
        if len(matches) > 1:
            raise RuntimeError(f"duplicate run id: {run_string}")
        return matches[0]

    def create(self, manifest: RunManifest) -> Path:
        directory = self.run_dir(manifest.run_id, manifest.created_at)
        directory.mkdir(parents=True, exist_ok=False)
        self.save_manifest(manifest, directory)
        return directory

    def save_manifest(self, manifest: RunManifest, directory: Path | None = None) -> Path:
        target = (directory or self.run_dir(manifest.run_id)) / "manifest.json"
        self._write_json(target, to_primitive(manifest))
        return target

    def load_manifest(self, run_id: UUID | str) -> dict[str, Any]:
        return self._read_json(self.run_dir(run_id) / "manifest.json")

    def save_chain(self, run_id: UUID | str, chain: OptionChain) -> Path:
        target = self.run_dir(run_id) / "option_chain.json"
        self._write_json(target, {"schema_version": 1, "chain": to_primitive(chain)})
        return target

    def save_events(self, run_id: UUID | str, events: Iterable[EventEnvelope[Any]]) -> Path:
        target = self.run_dir(run_id) / "market_events.jsonl"
        with target.open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event_to_primitive(event), ensure_ascii=False, sort_keys=True) + "\n")
        return target

    def load_events(self, run_id: UUID | str) -> list[EventEnvelope[Any]]:
        target = self.run_dir(run_id) / "market_events.jsonl"
        with target.open(encoding="utf-8") as handle:
            return [event_from_primitive(json.loads(line)) for line in handle if line.strip()]

    def save_snapshot(self, snapshot: ResearchSnapshot) -> Path:
        target = self.run_dir(snapshot.run_id) / "snapshot.json"
        self._write_json(target, snapshot_to_primitive(snapshot))
        return target

    def load_snapshot(self, run_id: UUID | str) -> ResearchSnapshot:
        return snapshot_from_primitive(self._read_json(self.run_dir(run_id) / "snapshot.json"))

    def save_report(self, run_id: UUID | str, result: OptionSnapshotAnalysis) -> Path:
        return write_csv(result, self.run_dir(run_id) / "report.csv")

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _read_json(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))


def new_manifest(run_id: UUID, spec: OptionChainCaptureSpec, code_version: str) -> RunManifest:
    now = datetime.now(timezone.utc)
    return RunManifest(1, run_id, now, now, RunStatus.CREATED, spec, code_version)
