from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Mapping

from kairospy.storage.data_lake import write_json

from .acquisition_primitives import AcquisitionEstimate
from .builders.planning import DataProductTaskPlan, TaskRangePlan
from .contracts import (
    DataProductContract,
    DataReleaseManifest,
    DatasetRelease,
    DatasetStatus,
    DatasetStorageKind,
    QualityLevel,
    stable_artifact_hash,
)
from .publishing import DatasetPublisher


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
        logical_key = str(getattr(request, "logical_key"))
        binding = self._binding(logical_key)
        source = getattr(request, "source")
        if getattr(source, "provider") != self.provider:
            raise ValueError("external process builder received an acquisition request for a different provider")
        manifest = self._run(binding, request)
        artifact = self._artifact_path(binding, manifest)
        release = publish_external_process_file(
            self.root,
            binding.product,
            artifact,
            fields=tuple(str(item) for item in manifest.get("fields") or binding.fields),
            provider=binding.provider,
            venue=binding.venue,
            transform_id=str(manifest.get("transform_id") or binding.transform_id),
            transform_version=str(manifest.get("transform_version") or binding.transform_version),
            source_manifest=manifest,
        )
        return release

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
    lake = Path(root)
    source_path = Path(source)
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(source_path)
    if source_path.suffix.lower() != ".csv":
        raise ValueError("external process provider artifacts currently support CSV files")
    _validate_csv_fields(source_path, fields)
    content = source_path.read_bytes()
    contract = _contract_payload(product, fields)
    content_hash = sha256(content + json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    release_id = f"ds_{sha256(str(product.key).encode() + b'\\0' + content_hash.encode()).hexdigest()[:24]}"
    publisher = DatasetPublisher(lake)
    directory = publisher.path(product, release_id)
    readable = directory / "event_year=all" / "event_month=all" / "part-000.csv"
    if not directory.exists():
        readable.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, readable)
        release_manifest = DataReleaseManifest(
            str(product.key),
            release_id,
            stable_artifact_hash(contract),
            content_hash,
            product.product.primary_time,
            fields,
            QualityLevel.WORKSPACE,
            {"provider": provider, "venue": venue, "kind": "external_process", "source": dict(source_manifest)},
            _now(),
        )
        write_json(directory / "manifest.json", {
            "manifest_version": 1,
            "dataset_id": release_id,
            "generated_at": _now(),
            "schema_id": product.schema_id,
            "fields": list(fields),
            "files": [{
                "path": readable.relative_to(directory).as_posix(),
                "bytes": len(content),
                "sha256": sha256(content).hexdigest(),
            }],
            "rows": _csv_row_count(source_path),
            "dataset_sha256": content_hash,
            "source": {"kind": "external_process", "provider": provider, "venue": venue, **dict(source_manifest)},
        })
        write_json(directory / "data_release_manifest.json", release_manifest.to_primitive())
        write_json(directory / "schema.json", {
            "schema_id": product.schema_id,
            "schema_version": 1,
            "primary_time": product.product.primary_time,
            "fields": list(fields),
        })
        write_json(directory / "lineage.json", {
            "lineage_version": 1,
            "dataset_id": release_id,
            "producer": {"name": "ExternalProcessDataProductBuilder", "transform": transform_id, "version": transform_version},
            "source": {"provider": provider, "venue": venue, "kind": "external_process", "manifest": dict(source_manifest)},
        })
        row_count = _csv_row_count(source_path)
        write_json(directory / "coverage.json", {
            "dataset_id": release_id,
            "time_basis": product.product.primary_time,
            "coverage": {"rows": row_count},
        })
        write_json(directory / "quality.json", {
            "quality_schema_version": 1,
            "dataset_id": release_id,
            "generated_at": _now(),
            "passed": row_count > 0,
            "checks": [{"name": "non_empty", "passed": row_count > 0, "value": row_count, "minimum": 1}],
            "metrics": {"rows": row_count},
        })
        write_json(directory / "capabilities.json", {"dataset_id": release_id, **dict(product.capabilities)})
        write_json(directory / "usage.json", {
            "usage_schema_version": 1,
            "logical_key": str(product.key),
            "primary_time": product.product.primary_time,
            "default_view": product.product.default_view.value,
            "dimensions": dict(product.product.dimensions),
            "known_limitations": ["external process artifact quality is limited to file-level checks"],
        })
    release = DatasetRelease(
        release_id,
        product.key,
        f"content.{content_hash[:16]}",
        product.schema_id,
        "1",
        transform_id,
        transform_version,
        str(directory.relative_to(lake)),
        "csv",
        content_hash,
        provider,
        venue,
        (f"{product.key}@latest-workspace",),
        DatasetStatus.APPROVED_FOR_WORKSPACE,
        QualityLevel.WORKSPACE,
        _now(),
        DatasetStorageKind.TABULAR,
        product.layout_version,
    )
    write_json(directory / "release.json", {
        "release_schema_version": 1,
        "release_id": release.release_id,
        "logical_key": str(release.product_key),
        "release_version": release.release_version,
        "schema_id": release.schema_id,
        "schema_version": release.schema_version,
        "transform_id": release.transform_id,
        "transform_version": release.transform_version,
        "content_hash": release.content_hash,
        "provider": release.provider,
        "venue": release.venue,
        "status": release.status.value,
        "quality_level": release.quality_level.value,
        "published_at": release.published_at,
    })
    return publisher.register(product, release)


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
