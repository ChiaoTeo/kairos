from __future__ import annotations

import csv
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import subprocess
from typing import Mapping

from kairospy.integrations.acquisition.planning import DataProductTaskPlan, TaskRangePlan
from kairospy.integrations.acquisition.primitives import AcquisitionEstimate
from kairospy.data.contracts import DataProductContract, DatasetRelease


@dataclass(frozen=True, slots=True)
class ExternalProcessProductBinding:
    product: DataProductContract
    fields: tuple[str, ...]
    provider: str
    venue: str | None
    command: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    timeout_seconds: int = 300
    transform_id: str = "external_process.dataset"
    transform_version: str = "1"
    cost_class: str = "external-process"
    estimate_requests: int | None = None

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.command:
            raise ValueError("external process binding requires provider and command")
        if not self.fields:
            raise ValueError("external process binding requires fields")
        if self.timeout_seconds <= 0:
            raise ValueError("external process binding timeout must be positive")


class ExternalProcessDataProductBuilder:
    """Run an external command that returns a file-backed dataset artifact manifest."""

    def __init__(self, root: str | Path, bindings: tuple[ExternalProcessProductBinding, ...]) -> None:
        if not bindings:
            raise ValueError("external process builder requires at least one binding")
        providers = {binding.provider for binding in bindings}
        if len(providers) != 1:
            raise ValueError("external process builder bindings must use one provider")
        self.root = Path(root)
        self.provider = next(iter(providers))
        self._bindings = {str(binding.product.key): binding for binding in bindings}

    def supports(self, logical_key: str) -> bool:
        return logical_key in self._bindings

    def estimate(self, request: object) -> AcquisitionEstimate:
        binding = self._binding(str(getattr(request, "logical_key")))
        if binding.estimate_requests is not None:
            return AcquisitionEstimate(binding.estimate_requests, cost_class=binding.cost_class, instruments=len(getattr(request, "instruments", ())))
        ranges = max(1, len(tuple(getattr(request, "missing", ()))))
        instruments = max(1, len(tuple(getattr(request, "instruments", ()))))
        return AcquisitionEstimate(ranges * instruments, cost_class=binding.cost_class, instruments=instruments)

    def acquire(self, request: object) -> DatasetRelease:
        raise RuntimeError("external process release publishing has been removed; use Data.add/user protocol or DatasetWriter")

    def task_plan(self, request: object) -> dict[str, object]:
        binding = self._binding(str(getattr(request, "logical_key")))
        return DataProductTaskPlan(
            binding.provider,
            "external-process",
            tuple(TaskRangePlan(item.start, item.end, 1, 0) for item in tuple(getattr(request, "missing", ()))),
            metadata={
                "command": list(binding.command),
                "cwd": str(binding.cwd),
                "timeout_seconds": binding.timeout_seconds,
                "products": list(self._bindings),
            },
        ).to_primitive()

    def _binding(self, logical_key: str) -> ExternalProcessProductBinding:
        try:
            return self._bindings[logical_key]
        except KeyError as error:
            raise ValueError(f"external process builder does not support Data Product {logical_key!r}") from error

    def _run(self, binding: ExternalProcessProductBinding, request: object) -> dict[str, object]:
        payload = {
            "product": str(binding.product.key),
            "root": str(self.root),
            "missing": [
                {"start": item.start.isoformat(), "end": item.end.isoformat(), "boundary": "[start,end)"}
                for item in tuple(getattr(request, "missing", ()))
            ],
            "instruments": list(getattr(request, "instruments", ())),
            "fields": list(getattr(request, "fields", ()) or binding.fields),
            "source": {
                "provider": getattr(getattr(request, "source"), "provider", None),
                "venue": getattr(getattr(request, "source"), "venue", None),
            },
            "base_release_id": getattr(request, "base_release_id", None),
        }
        completed = subprocess.run(
            list(binding.command),
            cwd=binding.cwd,
            input=json.dumps(payload, sort_keys=True),
            text=True,
            capture_output=True,
            timeout=binding.timeout_seconds,
            env={**os.environ, **dict(binding.env)} if binding.env else None,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"external process provider failed with exit code {completed.returncode}: {detail}")
        stdout = completed.stdout.strip()
        if not stdout:
            raise ValueError("external process provider must write a JSON artifact manifest to stdout")
        try:
            manifest = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise ValueError("external process provider stdout must be JSON") from error
        if not isinstance(manifest, dict):
            raise ValueError("external process provider manifest must be an object")
        kind = str(manifest.get("artifact_kind") or manifest.get("kind") or "dataset")
        if kind not in {"dataset", "source"}:
            raise ValueError("external process provider manifest artifact_kind must be dataset or source")
        return manifest

    def _artifact_path(self, binding: ExternalProcessProductBinding, manifest: Mapping[str, object]) -> Path:
        rows = manifest.get("rows")
        if rows is not None:
            path = self.root / "tmp" / "external-process" / str(binding.product.key).replace(".", "/") / "rows.csv"
            _write_inline_rows(path, rows, tuple(str(item) for item in manifest.get("fields") or binding.fields))
            return path
        value = manifest.get("path") or manifest.get("file") or manifest.get("rows_file") or manifest.get("artifact")
        if not value and isinstance(manifest.get("files"), list) and manifest["files"]:
            first = manifest["files"][0]
            if isinstance(first, Mapping):
                value = first.get("path") or first.get("file")
        if not value:
            raise ValueError("external process provider manifest must declare path, file, rows_file, artifact, files[].path, or rows")
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            path = binding.cwd / path
        return path.resolve()


def publish_external_process_file(
    root: str | Path,
    product: DataProductContract,
    source: str | Path,
    *,
    fields: tuple[str, ...],
    provider: str,
    venue: str | None,
    transform_id: str,
    transform_version: str,
    source_manifest: Mapping[str, object],
) -> DatasetRelease:
    raise RuntimeError("external process release publishing has been removed; use Data.add/user protocol or DatasetWriter")


def command_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(shlex.split(value))
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    raise ValueError("external process extension command must be a string or list")


def _contract_payload(product: DataProductContract, fields: tuple[str, ...]) -> dict[str, object]:
    return {
        "dataset_id": str(product.key),
        "title": product.product.title,
        "primary_time": product.product.primary_time,
        "fields": list(fields),
        "schema_id": product.schema_id,
        "quality_profile": product.quality_profile,
        "minimum_publication_level": product.minimum_publication_level.value,
    }


def _validate_csv_fields(path: Path, fields: tuple[str, ...]) -> None:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as error:
            raise ValueError("external process provider CSV artifact is empty") from error
    missing = sorted(set(fields) - set(header))
    if missing:
        raise ValueError(f"external process provider CSV artifact is missing fields: {', '.join(missing)}")


def _csv_row_count(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def _write_inline_rows(path: Path, rows: object, fields: tuple[str, ...]) -> None:
    if not isinstance(rows, list):
        raise ValueError("external process provider inline rows must be a list")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("external process provider inline rows must contain objects")
            writer.writerow({field: row.get(field) for field in fields})
