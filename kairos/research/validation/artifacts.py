from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import ResearchValidationResult


class ValidationArtifactWriter:
    """Writes the governed, machine-readable artifact set for a study version."""

    def __init__(self, data_root: str | Path = "data") -> None:
        self.data_root = Path(data_root)

    def write(self, result: ResearchValidationResult, *, report: str,
              extra_artifacts: dict[str, Any] | None = None,
              extra_audit: dict[str, Any] | None = None) -> Path:
        output = self.data_root / "studies" / result.registration.study_id / result.registration.version
        output.mkdir(parents=True, exist_ok=True)
        result_payload = result.to_dict()
        files = {
            "study_spec.json": {**result_payload["registration"], "spec_hash": result.spec_hash},
            "data_capabilities.json": result_payload["data_capabilities"],
            "data_quality.json": {"status": result_payload["state"]["data_status"],
                "dataset_ids": result_payload["data_capabilities"]["dataset_ids"],
                "maximum_validation_level": result_payload["data_capabilities"]["maximum_validation_level"],
                "limitations": result_payload["limitations"]},
            "sample_sufficiency.json": result_payload["sample_sufficiency"],
            "data_gap_plan.json": result_payload["data_gap_plan"],
            "capital_spec.json": result_payload["registration"].get("capital_spec"),
            "results.json": {
                "study_id": result.registration.study_id,
                "version": result.registration.version,
                "spec_hash": result.spec_hash,
                "state": result_payload["state"],
                "out_of_sample": result_payload["out_of_sample"],
                "metrics": result_payload["metrics"],
                "limitations": result_payload["limitations"],
                "generated_at": result.generated_at,
            },
        }
        for name, payload in (extra_artifacts or {}).items():
            if not name.endswith(".json") or "/" in name or name in files:
                raise ValueError(f"invalid or duplicate governed artifact name: {name}")
            files[name] = payload
        written_hashes = {}
        for name, payload in files.items():
            if payload is None:
                continue
            path = output / name
            _write_json(path, payload)
            written_hashes[name] = _sha256(path)
        (output / "REPORT.md").write_text(report.rstrip() + "\n", encoding="utf-8")
        written_hashes["REPORT.md"] = _sha256(output / "REPORT.md")
        audit = {
            "spec_hash": result.spec_hash,
            "artifact_hashes": written_hashes,
            "extra": extra_audit or {},
        }
        _write_json(output / "audit.json", audit)
        return output


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
