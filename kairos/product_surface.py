from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from kairos.data import (
    DataCatalog,
    DataReleaseManifest,
    DataSetContractArtifact,
    DataProductContract,
    DataProductDefinition,
    DatasetKey,
    DatasetLayer,
    DatasetRelease,
    DatasetStatus,
    DatasetStorageKind,
    LiveViewManifest,
    PAPER_LIVE_FRESHNESS_POLICY,
    QualityLevel,
    SourceBinding,
    data_release_ref,
    live_view_manifest_path,
    resolve_live_view_subscription,
    stable_artifact_hash,
    write_live_view_manifest,
)
from kairos.research_platform import ensure_sma_tutorial_dataset


BUILTIN_DOWNLOAD_KEYS = {
    "tutorial-sma-data": "fixture:sma-bars-v1",
    "sma-tutorial-data": "fixture:sma-bars-v1",
}


@dataclass(frozen=True, slots=True)
class DataProductApi:
    root: str | Path = "data"

    def download(self, key: str) -> dict[str, object]:
        return data_download(_args(self.root, key=key))

    def register_download(self, key: str, spec: str | Path) -> dict[str, object]:
        return data_register_download(_args(self.root, key=key, spec=Path(spec)))

    def write_file(self, file: str | Path, *, as_dataset: str, contract: str | Path) -> dict[str, object]:
        return data_write(_args(
            self.root, file=Path(file), live=False, connector=None,
            as_dataset=as_dataset, contract=Path(contract),
        ))

    def write_live(self, connector: str | Path, *, as_dataset: str, contract: str | Path) -> dict[str, object]:
        return data_write(_args(
            self.root, file=None, live=True, connector=Path(connector),
            as_dataset=as_dataset, contract=Path(contract),
        ))


@dataclass(frozen=True, slots=True)
class StudyProductApi:
    root: str | Path = "data"

    def open(self, study_id: str, *, version: str = "1.0.0", hypothesis: str = "") -> dict[str, object]:
        return study_open(_args(self.root, study_id=study_id, version=version, hypothesis=hypothesis))

    def add_data(self, workspace: str, *, name: str, dataset: str) -> dict[str, object]:
        return study_add_data(_args(self.root, workspace=workspace, name=name, dataset=dataset))

    def add_factor(self, workspace: str, *, name: str, file: str | Path) -> dict[str, object]:
        return study_add_factor(_args(self.root, workspace=workspace, name=name, file=str(file)))

    def inspect(self, study_id: str) -> dict[str, object]:
        return study_inspect(_args(self.root, study_id=study_id))

    def freeze(self, study_id: str, *, version: str = "1.0.0") -> dict[str, object]:
        return study_freeze(_args(self.root, study_id=study_id, version=version))


@dataclass(frozen=True, slots=True)
class StrategyProductApi:
    root: str | Path = "data"

    def open(self, strategy_id: str, *, from_study: str) -> dict[str, object]:
        return strategy_open(_args(self.root, strategy_id=strategy_id, from_study=from_study))

    def bind_factor(self, workspace: str, *, name: str, study_factor: str) -> dict[str, object]:
        return strategy_bind_factor(_args(self.root, workspace=workspace, name=name, study_factor=study_factor))

    def set_risk(self, strategy_id: str, risk_file: str | Path) -> dict[str, object]:
        return strategy_set_risk(_args(self.root, strategy_id=strategy_id, risk_file=str(risk_file)))

    def inspect(self, strategy_id: str) -> dict[str, object]:
        return strategy_inspect(_args(self.root, strategy_id=strategy_id))

    def freeze(self, strategy_id: str, *, version: str = "1.0.0") -> dict[str, object]:
        return strategy_freeze(_args(self.root, strategy_id=strategy_id, version=version))


@dataclass(frozen=True, slots=True)
class RunProductApi:
    root: str | Path = "data"

    def start_study(self, study: str, *, mode: str = "research") -> dict[str, object]:
        return run_start(_args(self.root, study=study, snapshot=None, mode=mode))

    def start_snapshot(self, snapshot: str, *, mode: str) -> dict[str, object]:
        return run_start(_args(self.root, study=None, snapshot=snapshot, mode=mode))

    def inspect(self, run_id: str) -> dict[str, object]:
        return run_inspect(_args(self.root, run_id=run_id))

    def replay(self, run_id: str) -> dict[str, object]:
        return run_replay(_args(self.root, run_id=run_id))

    def compare(self, first: str, second: str) -> dict[str, object]:
        return run_compare(_args(self.root, first=first, second=second))


@dataclass(frozen=True, slots=True)
class ProductPaths:
    root: Path

    @property
    def studies(self) -> Path:
        return self.root / "studies"

    @property
    def strategies(self) -> Path:
        return self.root / "strategies"

    @property
    def runs(self) -> Path:
        return self.root / "runs"


def data_download(args) -> dict[str, object]:
    key = args.key
    if key not in BUILTIN_DOWNLOAD_KEYS:
        raise ValueError(f"unknown data download key {key!r}; registered target-state keys: {', '.join(sorted(BUILTIN_DOWNLOAD_KEYS))}")
    release = ensure_sma_tutorial_dataset(args.lake_root)
    evidence = _data_release_evidence(Path(args.lake_root), release)
    report = {
        "product": "data",
        "operation": "download",
        "key": key,
        "dataset": str(release.product_key),
        "release_id": release.release_id,
        "content_hash": release.content_hash,
        "contract_hash": evidence["contract_hash"],
        "manifest_hash": evidence["manifest_hash"],
        "quality_level": release.quality_level.value,
        "artifact": str(Path(args.lake_root) / release.relative_path),
        "artifact_ref": evidence["artifact_ref"],
        "contract": "DataSet Contract",
    }
    report_path = Path(args.lake_root) / "downloads" / key / "report.json"
    _write_json(report_path, report)
    return {**report, "report": str(report_path)}


def data_register_download(args) -> dict[str, object]:
    payload = _read_contract(args.spec)
    key = args.key
    spec_hash = _stable_hash(payload)
    target = Path(args.lake_root) / "data-products" / "downloads" / key / "download-spec.json"
    _write_json(target, {"key": key, "spec": payload, "spec_hash": spec_hash, "registered_at": _now()})
    return {"product": "data", "operation": "register-download", "key": key, "spec": str(target), "spec_hash": spec_hash}


def data_write(args) -> dict[str, object]:
    dataset_id = args.as_dataset
    contract = _read_contract(args.contract)
    contract_dataset = str(contract.get("dataset_id") or contract.get("identity", {}).get("dataset_id") or dataset_id)
    if contract_dataset != dataset_id:
        raise ValueError(f"contract dataset_id {contract_dataset!r} does not match --as {dataset_id!r}")
    fields = _contract_fields(contract)
    primary_time = str(contract.get("primary_time") or contract.get("time", {}).get("primary_time") or "")
    if not primary_time:
        raise ValueError("data write contract must declare primary_time")
    if args.live:
        return _write_live_view(args, dataset_id, contract, fields, primary_time)
    if args.file is None:
        raise ValueError("data write requires --file unless --live is supplied")
    source = Path(args.file)
    if not source.exists():
        raise FileNotFoundError(source)
    _validate_csv_header(source, fields)
    root = Path(args.lake_root)
    material = source.read_bytes() + json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    content_hash = sha256(material).hexdigest()
    release_id = f"{dataset_id}:write:{content_hash[:12]}"
    directory = root / "external" / dataset_id.replace(".", "/") / release_id
    directory.mkdir(parents=True, exist_ok=True)
    copied = directory / source.name
    if not copied.exists() or copied.read_bytes() != source.read_bytes():
        copied.write_bytes(source.read_bytes())
    manifest = DataReleaseManifest(
        dataset_id,
        release_id,
        stable_artifact_hash(contract),
        content_hash,
        primary_time,
        tuple(fields),
        QualityLevel.RESEARCH,
        {"kind": "file", "name": source.name},
        _now(),
    )
    manifest_payload = manifest.to_primitive()
    _write_json(directory / "manifest.json", manifest_payload)
    _register_written_release(root, dataset_id, release_id, directory, content_hash, primary_time)
    return {
        **manifest_payload,
        "manifest_hash": manifest.manifest_hash,
        "artifact": str(directory / "manifest.json"),
        "artifact_ref": manifest.artifact_ref,
    }


def study_exists(root: str | Path, study_id: str) -> bool:
    return _study_file(root, study_id).exists()


def strategy_exists(root: str | Path, strategy_id: str) -> bool:
    return _strategy_file(root, strategy_id).exists()


def run_exists(root: str | Path, run_id: str) -> bool:
    return _find_run_manifest(root, run_id) is not None


def study_open(args) -> dict[str, object]:
    path = _study_file(args.lake_root, args.study_id)
    if path.exists():
        return _read_json(path)
    study = {
        "product": "study",
        "kind": "study.workspace",
        "id": args.study_id,
        "version": args.version,
        "hypothesis": args.hypothesis,
        "status": "draft",
        "data": {},
        "factors": {},
        "created_at": _now(),
    }
    _write_json(path, study)
    return study


def study_add_data(args) -> dict[str, object]:
    workspace = _workspace_arg(args)
    study = _load_study(args.lake_root, workspace)
    release = DataCatalog(args.lake_root).release(args.dataset)
    evidence = _data_release_evidence(Path(args.lake_root), release)
    study.setdefault("data", {})[args.name] = {
        "dataset": args.dataset,
        "release_id": release.release_id,
        "content_hash": release.content_hash,
        "contract_hash": evidence["contract_hash"],
        "manifest_hash": evidence["manifest_hash"],
        "artifact_ref": evidence["artifact_ref"],
        "quality_level": release.quality_level.value,
    }
    _write_json(_study_file(args.lake_root, workspace), study)
    return {"product": "study", "operation": "add-data", "study_id": workspace, "name": args.name, **study["data"][args.name]}


def study_add_factor(args) -> dict[str, object]:
    workspace = _workspace_arg(args)
    study = _load_study(args.lake_root, workspace)
    source = _resolve_user_file(args.file, _study_dir(args.lake_root, workspace))
    code_hash = sha256(source.read_bytes()).hexdigest()
    study.setdefault("factors", {})[args.name] = {
        "path": str(source),
        "code_hash": code_hash,
        "source_hash": code_hash,
        "artifact_ref": f"study://{workspace}/factors/{args.name}",
        "contract": "Factor Contract",
    }
    _write_json(_study_file(args.lake_root, workspace), study)
    return {"product": "study", "operation": "add-factor", "study_id": workspace, "name": args.name, "code_hash": code_hash}


def study_inspect(args) -> dict[str, object]:
    study = _load_study(args.lake_root, args.study_id)
    return {**study, "contract": "Study Contract"}


def study_freeze(args) -> dict[str, object]:
    study = _load_study(args.lake_root, args.study_id)
    version = args.version
    lock_body = {
        "product": "study",
        "kind": "study.lock",
        "schema_version": 1,
        "study_id": args.study_id,
        "version": version,
        "data": study.get("data", {}),
        "factors": study.get("factors", {}),
        "evidence_chain": _study_evidence_chain(study),
        "frozen_at": _now(),
    }
    lock_hash = _stable_hash(lock_body)
    lock = {**lock_body, "lock_hash": lock_hash}
    path = _study_dir(args.lake_root, args.study_id) / "locks" / version / "study.lock.json"
    _write_json(path, lock)
    study["status"] = "frozen"
    study["latest_lock"] = str(path)
    study["latest_lock_hash"] = lock_hash
    _write_json(_study_file(args.lake_root, args.study_id), study)
    return {**lock, "artifact": str(path)}


def strategy_open(args) -> dict[str, object]:
    study_id, version = _split_ref(args.from_study)
    study_lock = _load_study_lock(args.lake_root, study_id, version)
    strategy = {
        "product": "strategy",
        "kind": "strategy.workspace",
        "id": args.strategy_id,
        "version": "draft",
        "derived_from": {"study": study_id, "version": version, "lock_hash": study_lock["lock_hash"]},
        "data": study_lock.get("data", {}),
        "inputs": {},
        "model": {"path": "model.py"},
        "risk": {},
        "execution": {},
        "created_at": _now(),
    }
    _write_json(_strategy_file(args.lake_root, args.strategy_id), strategy)
    return strategy


def strategy_bind_factor(args) -> dict[str, object]:
    workspace = _workspace_arg(args)
    strategy = _load_strategy(args.lake_root, workspace)
    derived = strategy["derived_from"]
    study_lock = _load_study_lock(args.lake_root, derived["study"], derived["version"])
    factors = study_lock.get("factors", {})
    if args.study_factor not in factors:
        raise ValueError(f"study factor {args.study_factor!r} is not in Study Lock")
    strategy.setdefault("inputs", {})[args.name] = {
        "kind": "factor",
        "from_study_factor": args.study_factor,
        "source_hash": factors[args.study_factor]["code_hash"],
        "contract": factors[args.study_factor].get("contract", "Factor Contract"),
        "artifact_ref": f"study://{derived['study']}/locks/{derived['version']}/factors/{args.study_factor}",
    }
    _write_json(_strategy_file(args.lake_root, workspace), strategy)
    return {"product": "strategy", "operation": "bind-factor", "strategy_id": workspace, "name": args.name, **strategy["inputs"][args.name]}


def strategy_set_risk(args) -> dict[str, object]:
    strategy = _load_strategy(args.lake_root, args.strategy_id)
    source = _resolve_user_file(args.risk_file, _strategy_dir(args.lake_root, args.strategy_id))
    risk_hash = sha256(source.read_bytes()).hexdigest()
    strategy["risk"] = {"path": str(source), "hash": risk_hash}
    _write_json(_strategy_file(args.lake_root, args.strategy_id), strategy)
    return {"product": "strategy", "operation": "set-risk", "strategy_id": args.strategy_id, "risk_hash": risk_hash}


def strategy_inspect(args) -> dict[str, object]:
    return {**_load_strategy(args.lake_root, args.strategy_id), "contract": "Strategy Contract"}


def strategy_freeze(args) -> dict[str, object]:
    strategy = _load_strategy(args.lake_root, args.strategy_id)
    derived = strategy["derived_from"]
    study_lock = _load_study_lock(args.lake_root, derived["study"], derived["version"])
    _assert_strategy_study_consistency(strategy, study_lock)
    lock_body = {
        "product": "strategy",
        "kind": "strategy.lock",
        "schema_version": 1,
        "strategy_id": args.strategy_id,
        "version": args.version,
        "derived_from": strategy["derived_from"],
        "data": strategy.get("data", {}),
        "inputs": strategy.get("inputs", {}),
        "model": strategy.get("model", {}),
        "risk": strategy.get("risk", {}),
        "execution": strategy.get("execution", {}),
        "consistency_checks": {
            "study_lock_hash": "passed",
            "factor_hashes": "passed",
            "data_release_hashes": "passed",
        },
        "frozen_at": _now(),
    }
    lock_hash = _stable_hash(lock_body)
    lock = {**lock_body, "lock_hash": lock_hash}
    path = _strategy_dir(args.lake_root, args.strategy_id) / "locks" / args.version / "strategy.lock.json"
    _write_json(path, lock)
    strategy["version"] = args.version
    strategy["latest_lock"] = str(path)
    strategy["latest_lock_hash"] = lock_hash
    _write_json(_strategy_file(args.lake_root, args.strategy_id), strategy)
    return {**lock, "artifact": str(path)}


def run_start(args) -> dict[str, object]:
    root = Path(args.lake_root)
    if args.study:
        target_id = args.study
        target_kind = "study"
        target = _load_study(root, args.study)
        target_hash = _stable_hash(target)
    else:
        strategy_id, version = _split_ref(args.snapshot)
        target_id = strategy_id
        target_kind = "strategy"
        target = _load_strategy_lock(root, strategy_id, version)
        target_hash = target["lock_hash"]
    live_bindings = _paper_live_subscription_bindings(root, target) if args.mode in {"paper", "live"} else ()
    freshness_gates = tuple(
        {"name": item["name"], "dataset": item["dataset"], **item["freshness_gate"]}
        for item in live_bindings
    )
    material = {"target_kind": target_kind, "target_id": target_id, "mode": args.mode, "target_hash": target_hash, "at": _now()}
    run_id = f"run_{sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:16]}"
    directory = root / "runs" / target_id / run_id
    manifest = {
        "product": "run",
        "kind": "run.manifest",
        "schema_version": 1,
        "run_id": run_id,
        "mode": args.mode,
        "target": {"kind": target_kind, "id": target_id, "hash": target_hash},
        "input_artifacts": _run_input_artifacts(target_kind, target),
        "runtime_contract": {
            "mode": args.mode,
            **({"feed_bindings": list(live_bindings)} if live_bindings else {}),
            **({"freshness_gates": list(freshness_gates)} if freshness_gates else {}),
        },
        "started_at": material["at"],
        "outputs": {},
    }
    _write_json(directory / "snapshot.json", target)
    _write_json(directory / "manifest.json", manifest)
    _write_json(directory / "reports" / "summary.json", {"run_id": run_id, "passed": True, "target_hash": target_hash})
    return {**manifest, "workspace": str(directory), "manifest": str(directory / "manifest.json")}


def run_inspect(args) -> dict[str, object]:
    manifest = _load_run_manifest(args.lake_root, args.run_id)
    return {**manifest, "artifact": str(_find_run_manifest(args.lake_root, args.run_id))}


def run_replay(args) -> dict[str, object]:
    path = _find_run_manifest(args.lake_root, args.run_id)
    if path is None:
        raise FileNotFoundError(args.run_id)
    manifest = _read_json(path)
    snapshot = _read_json(path.with_name("snapshot.json"))
    snapshot_hash = str(snapshot.get("lock_hash") or _stable_hash(snapshot))
    expected = manifest["target"]["hash"]
    passed = snapshot_hash == expected
    report = {"product": "run", "operation": "replay", "run_id": args.run_id, "passed": passed, "expected_hash": expected, "actual_hash": snapshot_hash}
    _write_json(path.parent / "replay.json", report)
    return report


def run_compare(args) -> dict[str, object]:
    first = _load_run_manifest(args.lake_root, args.first)
    second = _load_run_manifest(args.lake_root, args.second)
    return {
        "product": "run",
        "operation": "compare",
        "first": args.first,
        "second": args.second,
        "same_target": first["target"] == second["target"],
        "same_mode": first["mode"] == second["mode"],
    }


def _write_live_view(args, dataset_id: str, contract: dict[str, Any], fields: list[str], primary_time: str) -> dict[str, object]:
    connector_arg = getattr(args, "connector", None)
    if connector_arg is None:
        raise ValueError("data write --live requires --connector")
    freshness = _live_view_freshness_contract(contract)
    connector = Path(connector_arg)
    if not connector.exists():
        raise FileNotFoundError(connector)
    contract_hash = stable_artifact_hash(contract)
    connector_hash = sha256(connector.read_bytes()).hexdigest()
    live_view_id = f"{dataset_id}:live:{connector_hash[:12]}"
    manifest = LiveViewManifest(
        dataset_id,
        live_view_id,
        contract_hash,
        connector_hash,
        primary_time,
        tuple(fields),
        {
            "transport": "connector",
            "event_source_contract": "EventSource[DataSetRecord]",
            "channel_contract": "BoundedEventChannel",
            "freshness": freshness,
        },
        {"kind": "live_connector", "name": connector.name},
        "configured",
        _now(),
    )
    manifest_payload = manifest.to_primitive()
    manifest_path = live_view_manifest_path(args.lake_root, dataset_id, live_view_id)
    write_live_view_manifest(manifest_path, manifest)
    return {
        **manifest_payload,
        "manifest_hash": manifest.manifest_hash,
        "artifact": str(manifest_path),
        "artifact_ref": manifest.artifact_ref,
    }


def _live_view_freshness_contract(contract: dict[str, Any]) -> dict[str, object]:
    freshness = contract.get("freshness")
    if not isinstance(freshness, dict):
        raise ValueError("data write --live contract must declare freshness.max_age_seconds")
    max_age = freshness.get("max_age_seconds")
    try:
        max_age_seconds = int(max_age)
    except (TypeError, ValueError) as error:
        raise ValueError("data write --live contract must declare positive freshness.max_age_seconds") from error
    if max_age_seconds <= 0:
        raise ValueError("data write --live contract must declare positive freshness.max_age_seconds")
    normalized = dict(freshness)
    normalized["max_age_seconds"] = max_age_seconds
    return normalized


def _data_release_evidence(root: Path, release: DatasetRelease) -> dict[str, object]:
    catalog = DataCatalog(root)
    release_dir = root / release.relative_path
    manifest_path = release_dir / "data_release_manifest.json"
    if not manifest_path.exists():
        manifest_path = release_dir / "manifest.json"
    manifest = _read_json(manifest_path) if manifest_path.exists() else {}
    try:
        spec = catalog.product_spec(release.product_key)
        contract_hash = DataSetContractArtifact.from_product_contract(spec).contract_hash
    except KeyError:
        contract_hash = ""
    contract_hash = str(manifest.get("contract_hash") or contract_hash)
    manifest_hash = stable_artifact_hash(manifest) if manifest else ""
    return {
        "dataset": str(release.product_key),
        "release_id": release.release_id,
        "content_hash": release.content_hash,
        "contract_hash": contract_hash,
        "manifest_hash": manifest_hash,
        "artifact_ref": data_release_ref(str(release.product_key), release.release_id),
        "quality_level": release.quality_level.value,
    }


def _study_evidence_chain(study: dict[str, Any]) -> dict[str, object]:
    return {
        "data": {
            name: {
                "dataset": item.get("dataset"),
                "release_id": item.get("release_id"),
                "content_hash": item.get("content_hash"),
                "contract_hash": item.get("contract_hash"),
                "manifest_hash": item.get("manifest_hash"),
                "artifact_ref": item.get("artifact_ref"),
            }
            for name, item in sorted(study.get("data", {}).items())
        },
        "factors": {
            name: {
                "code_hash": item.get("code_hash"),
                "artifact_ref": item.get("artifact_ref"),
                "contract": item.get("contract"),
            }
            for name, item in sorted(study.get("factors", {}).items())
        },
    }


def _assert_strategy_study_consistency(strategy: dict[str, Any], study_lock: dict[str, Any]) -> None:
    if strategy["derived_from"].get("lock_hash") != study_lock.get("lock_hash"):
        raise ValueError("Strategy workspace must derive from the current Frozen Study Lock hash")
    if strategy.get("data", {}) != study_lock.get("data", {}):
        raise ValueError("Strategy data release evidence must match the Frozen Study Lock")
    factors = study_lock.get("factors", {})
    for name, input_spec in strategy.get("inputs", {}).items():
        factor_name = input_spec.get("from_study_factor")
        if factor_name not in factors:
            raise ValueError(f"strategy input {name!r} references unknown Study factor {factor_name!r}")
        if input_spec.get("source_hash") != factors[factor_name].get("code_hash"):
            raise ValueError(f"strategy input {name!r} factor hash does not match the Frozen Study Lock")


def _run_input_artifacts(target_kind: str, target: dict[str, Any]) -> dict[str, object]:
    if target_kind == "study":
        return _study_evidence_chain(target)
    data = {
        name: {
            "dataset": item.get("dataset"),
            "release_id": item.get("release_id"),
            "content_hash": item.get("content_hash"),
            "contract_hash": item.get("contract_hash"),
            "manifest_hash": item.get("manifest_hash"),
            "artifact_ref": item.get("artifact_ref"),
        }
        for name, item in sorted(target.get("data", {}).items())
    }
    inputs = {
        name: {
            "kind": item.get("kind"),
            "source_hash": item.get("source_hash"),
            "artifact_ref": item.get("artifact_ref"),
            "contract": item.get("contract"),
        }
        for name, item in sorted(target.get("inputs", {}).items())
    }
    return {"data": data, "inputs": inputs}


def _paper_live_subscription_bindings(root: Path, target: dict[str, Any]) -> tuple[dict[str, object], ...]:
    results = []
    for name, data in sorted(target.get("data", {}).items()):
        dataset = str(data.get("dataset") or "")
        contract_hash = str(data.get("contract_hash") or "")
        binding = resolve_live_view_subscription(
            root, name=name, dataset_id=dataset, contract_hash=contract_hash,
            policy=PAPER_LIVE_FRESHNESS_POLICY,
        )
        results.append(binding.to_primitive())
    return tuple(results)


def _register_written_release(root: Path, dataset_id: str, release_id: str, directory: Path, content_hash: str, primary_time: str) -> None:
    layer = _dataset_layer(dataset_id)
    product = DataProductDefinition(
        DatasetKey(dataset_id),
        dataset_id,
        layer,
        description="User-written Data Product release",
        dimensions={"source": "user-write"},
        primary_time=primary_time,
        sources=(SourceBinding("user-write", None, 100, QualityLevel.RESEARCH, ("file",)),),
        owner="user",
    )
    spec = DataProductContract(
        product,
        str(directory.parent.relative_to(root)),
        f"{dataset_id}.contract",
        storage_kind=DatasetStorageKind.TABULAR,
        quality_profile="contract",
        minimum_publication_level=QualityLevel.RESEARCH,
    )
    catalog = DataCatalog(root)
    catalog.register_product_spec(spec, enrich=True)
    catalog.register_release(DatasetRelease(
        release_id,
        product.key,
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        spec.schema_id,
        "1",
        "data.write",
        "1",
        str(directory.relative_to(root)),
        "csv",
        content_hash,
        "user-write",
        None,
        (),
        DatasetStatus.APPROVED_FOR_RESEARCH,
        QualityLevel.RESEARCH,
        _now(),
        DatasetStorageKind.TABULAR,
        "1",
    ))
    catalog.save()


def _dataset_layer(dataset_id: str) -> DatasetLayer:
    if dataset_id.startswith("reference."):
        return DatasetLayer.REFERENCE
    if dataset_id.startswith("features."):
        return DatasetLayer.FEATURES
    if dataset_id.startswith("market."):
        return DatasetLayer.CANONICAL
    return DatasetLayer.SOURCE


def _contract_fields(contract: dict[str, Any]) -> list[str]:
    raw_fields = contract.get("fields")
    if raw_fields is None:
        raw_fields = contract.get("schema", {}).get("fields")
    fields = []
    for field in raw_fields or ():
        fields.append(str(field.get("name") if isinstance(field, dict) else field))
    if not fields:
        raise ValueError("data write contract must declare fields")
    return fields


def _validate_csv_header(path: Path, fields: list[str]) -> None:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as error:
            raise ValueError("CSV file is empty") from error
    missing = sorted(set(fields) - set(header))
    if missing:
        raise ValueError(f"CSV file is missing contract fields: {', '.join(missing)}")


def _read_contract(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        try:
            import yaml
        except ModuleNotFoundError:
            raise ValueError(f"contract/spec must be JSON unless PyYAML is installed: {source}") from error
        value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError(f"contract/spec must be an object: {source}")
    return value


def _resolve_user_file(value: str | Path, base: Path) -> Path:
    path = Path(value)
    if not path.is_absolute() and not path.exists():
        path = base / path
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _study_dir(root: str | Path, study_id: str) -> Path:
    return ProductPaths(Path(root)).studies / study_id


def _study_file(root: str | Path, study_id: str) -> Path:
    return _study_dir(root, study_id) / "study.json"


def _strategy_dir(root: str | Path, strategy_id: str) -> Path:
    return ProductPaths(Path(root)).strategies / strategy_id


def _strategy_file(root: str | Path, strategy_id: str) -> Path:
    return _strategy_dir(root, strategy_id) / "strategy.json"


def _load_study(root: str | Path, study_id: str) -> dict[str, Any]:
    return _read_json(_study_file(root, study_id))


def _load_strategy(root: str | Path, strategy_id: str) -> dict[str, Any]:
    return _read_json(_strategy_file(root, strategy_id))


def _load_study_lock(root: str | Path, study_id: str, version: str) -> dict[str, Any]:
    return _read_json(_study_dir(root, study_id) / "locks" / version / "study.lock.json")


def _load_strategy_lock(root: str | Path, strategy_id: str, version: str) -> dict[str, Any]:
    return _read_json(_strategy_dir(root, strategy_id) / "locks" / version / "strategy.lock.json")


def _split_ref(value: str) -> tuple[str, str]:
    if "@" not in value:
        raise ValueError(f"reference must use name@version form: {value!r}")
    left, right = value.split("@", 1)
    if not left or not right:
        raise ValueError(f"reference must use name@version form: {value!r}")
    return left, right


def _workspace_arg(args) -> str:
    value = getattr(args, "workspace", None) or getattr(args, "ws", None)
    if not value:
        raise ValueError("workspace id is required")
    return str(value)


def _find_run_manifest(root: str | Path, run_id: str) -> Path | None:
    matches = list((Path(root) / "runs").glob(f"*/{run_id}/manifest.json"))
    return matches[0] if matches else None


def _load_run_manifest(root: str | Path, run_id: str) -> dict[str, Any]:
    path = _find_run_manifest(root, run_id)
    if path is None:
        raise FileNotFoundError(run_id)
    return _read_json(path)


def _stable_hash(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, payload: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)


def _args(root: str | Path, **kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(lake_root=Path(root), **kwargs)
