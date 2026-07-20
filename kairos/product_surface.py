from __future__ import annotations

import asyncio
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from kairos.application import (
    ApplicationConfig,
    AsyncKairosRuntime,
    KairosApplication,
    RuntimePaths,
    backtest_composition,
    historical_simulation_composition,
    live_composition,
    paper_trading_composition,
    runtime_execution_plan,
    runtime_feed_plan,
    runtime_strategy_plan,
    study_composition,
)
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
    register_live_capture_release,
    resolve_live_view_subscription,
    stable_artifact_hash,
    write_live_view_manifest,
)
from kairos.orchestration.runtime_store import SQLiteRuntimeStore
from kairos.ports import Environment
from kairos.study_platform import ensure_sma_tutorial_dataset


BUILTIN_DOWNLOAD_KEYS = {
    "tutorial-sma-data": "fixture:sma-bars-v1",
    "sma-tutorial-data": "fixture:sma-bars-v1",
}


class InputTableRef(dict):
    def __init__(self, root: str | Path, evidence: dict[str, object]) -> None:
        super().__init__(evidence)
        self._root = Path(root)

    def arrow(self, *, columns: tuple[str, ...] | list[str] | None = None):
        from kairos.data import OutputFormat
        return self._query(columns).collect(OutputFormat.ARROW)

    def pandas(self, *, columns: tuple[str, ...] | list[str] | None = None):
        from kairos.data import OutputFormat
        return self._query(columns).collect(OutputFormat.PANDAS)

    def rows(self, *, columns: tuple[str, ...] | list[str] | None = None) -> list[dict[str, object]]:
        from kairos.data import OutputFormat
        return self._query(columns).collect(OutputFormat.ROWS)

    def _query(self, columns):
        from kairos.data import DatasetClient
        release_id = str(self["release_id"])
        return DatasetClient(self._root).get(release_id, fields=tuple(columns) if columns is not None else None)


class StrategyDecisionContext:
    def __init__(self, root: str | Path, strategy_lock: dict[str, Any]) -> None:
        self._root = Path(root)
        self._strategy_lock = strategy_lock

    @property
    def mode(self) -> str:
        return "strategy"

    @property
    def strategy_id(self) -> str:
        return str(self._strategy_lock.get("strategy_id") or "")

    def data(self, name: str) -> InputTableRef:
        data = self._strategy_lock.get("data", {})
        if name not in data:
            raise ValueError(f"Strategy data input {name!r} is not declared")
        return InputTableRef(self._root, {
            "product": "strategy",
            "operation": "data",
            "strategy_id": self.strategy_id,
            "name": name,
            **data[name],
        })

    def input(self, name: str) -> InputTableRef:
        data = self._strategy_lock.get("data", {})
        if name in data:
            return self.data(name)
        inputs = self._strategy_lock.get("inputs", {})
        if name not in inputs:
            raise ValueError(f"Strategy input {name!r} is not declared")
        input_spec = inputs[name]
        release_id = input_spec.get("release_id")
        dataset = input_spec.get("dataset")
        if release_id and dataset:
            return InputTableRef(self._root, {
                "product": "strategy",
                "operation": "input",
                "strategy_id": self.strategy_id,
                "name": name,
                **input_spec,
            })
        raise ValueError(
            f"Strategy input {name!r} is a factor contract without materialized InputTable; "
            "publish the factor as a Feature Data Release before executing model.py"
        )

    def factor(self, name: str) -> InputTableRef:
        return self.input(name)

    def manifest(self) -> dict[str, object]:
        return {
            "strategy_id": self.strategy_id,
            "data_inputs": sorted(self._strategy_lock.get("data", {})),
            "strategy_inputs": sorted(self._strategy_lock.get("inputs", {})),
        }


@dataclass(frozen=True, slots=True)
class DataProductApi:
    root: str | Path = "data"

    def download(self, key: str) -> dict[str, object]:
        return data_download(_args(self.root, key=key))

    def dataset(self, name: str) -> dict[str, object]:
        release = DataCatalog(self.root).release(name)
        return InputTableRef(self.root, {
            "product": "data",
            "operation": "dataset",
            **_data_release_evidence(Path(self.root), release),
        })

    def register_download(self, key: str, spec: str | Path) -> dict[str, object]:
        return data_register_download(_args(self.root, key=key, spec=Path(spec)))

    def register_provider(self, name: str, spec: str | Path) -> dict[str, object]:
        return data_register_provider(_args(self.root, name=name, spec=Path(spec)))

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

    def data(self, study_id: str, name: str, *, version: str | None = None) -> dict[str, object]:
        study = _study_material(self.root, study_id, version)
        data = study.get("data", {})
        if name not in data:
            raise ValueError(f"Study data alias {name!r} is not declared")
        return InputTableRef(self.root, {
            "product": "study",
            "operation": "data",
            "study_id": study_id,
            "version": version or study.get("version", "draft"),
            "name": name,
            **data[name],
        })

    def add_factor(
        self,
        workspace: str,
        *,
        name: str,
        file: str | Path,
        metadata: str | Path | None = None,
    ) -> dict[str, object]:
        return study_add_factor(_args(
            self.root, workspace=workspace, name=name, file=str(file),
            metadata=Path(metadata) if metadata is not None else None,
        ))

    def inspect(self, study_id: str) -> dict[str, object]:
        return study_inspect(_args(self.root, study_id=study_id))

    def factor(self, study_id: str, name: str, *, version: str | None = None) -> dict[str, object]:
        study = _study_material(self.root, study_id, version)
        factors = study.get("factors", {})
        if name not in factors:
            raise ValueError(f"Study factor {name!r} is not declared")
        factor = dict(factors[name])
        if version is not None:
            factor.pop("path", None)
            factor.pop("metadata_path", None)
        return {
            "product": "study",
            "operation": "factor",
            "study_id": study_id,
            "version": version or study.get("version", "draft"),
            "name": name,
            **factor,
        }

    def run_factor(self, study_id: str, name: str) -> dict[str, object]:
        return study_factor_run(_args(self.root, study_id=study_id, name=name))

    def publish_factor(self, study_id: str, name: str, *, as_dataset: str) -> dict[str, object]:
        return study_publish_factor(_args(
            self.root,
            study_id=study_id,
            name=name,
            as_dataset=as_dataset,
        ))

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

    def set_execution(self, strategy_id: str, execution_file: str | Path) -> dict[str, object]:
        return strategy_set_execution(_args(
            self.root,
            strategy_id=strategy_id,
            execution_file=str(execution_file),
        ))

    def set_model_code(
        self,
        strategy_id: str,
        model_file: str | Path,
        *,
        metadata: str | Path | None = None,
    ) -> dict[str, object]:
        return strategy_set_model_code(_args(
            self.root,
            strategy_id=strategy_id,
            model_file=str(model_file),
            metadata=Path(metadata) if metadata is not None else None,
        ))

    def set_model(
        self,
        strategy_id: str,
        *,
        kind: str,
        instrument_id: str | None = None,
        fast_window: int | None = None,
        slow_window: int | None = None,
        approved_capital: str | None = None,
    ) -> dict[str, object]:
        return strategy_set_model(_args(
            self.root,
            strategy_id=strategy_id,
            kind=kind,
            instrument_id=instrument_id,
            fast_window=fast_window,
            slow_window=slow_window,
            approved_capital=approved_capital,
        ))

    def inspect(self, strategy_id: str) -> dict[str, object]:
        return strategy_inspect(_args(self.root, strategy_id=strategy_id))

    def freeze(self, strategy_id: str, *, version: str = "1.0.0") -> dict[str, object]:
        return strategy_freeze(_args(self.root, strategy_id=strategy_id, version=version))


@dataclass(frozen=True, slots=True)
class RunProductApi:
    root: str | Path = "data"

    def start(
        self,
        target: str,
        *,
        mode: str,
        execute_feeds: bool = False,
        feed_runtime_seconds: float = 0.0,
        feed_runtime_factory: object | None = None,
        execute_strategy: bool = False,
        strategy_runtime_factory: object | None = None,
    ) -> dict[str, object]:
        if "@" in target:
            return self.start_snapshot(
                target,
                mode=mode,
                execute_feeds=execute_feeds,
                feed_runtime_seconds=feed_runtime_seconds,
                feed_runtime_factory=feed_runtime_factory,
                execute_strategy=execute_strategy,
                strategy_runtime_factory=strategy_runtime_factory,
            )
        return self.start_study(
            target,
            mode=mode,
            execute_feeds=execute_feeds,
            feed_runtime_seconds=feed_runtime_seconds,
            feed_runtime_factory=feed_runtime_factory,
            execute_strategy=execute_strategy,
            strategy_runtime_factory=strategy_runtime_factory,
        )

    def start_study(
        self,
        study: str,
        *,
        mode: str = "study",
        execute_feeds: bool = False,
        feed_runtime_seconds: float = 0.0,
        feed_runtime_factory: object | None = None,
        execute_strategy: bool = False,
        strategy_runtime_factory: object | None = None,
    ) -> dict[str, object]:
        return run_start(_args(
            self.root,
            study=study,
            snapshot=None,
            mode=mode,
            execute_feeds=execute_feeds,
            feed_runtime_seconds=feed_runtime_seconds,
            feed_runtime_factory=feed_runtime_factory,
            execute_strategy=execute_strategy,
            strategy_runtime_factory=strategy_runtime_factory,
        ))

    def start_snapshot(
        self,
        snapshot: str,
        *,
        mode: str,
        execute_feeds: bool = False,
        feed_runtime_seconds: float = 0.0,
        feed_runtime_factory: object | None = None,
        execute_strategy: bool = False,
        strategy_runtime_factory: object | None = None,
    ) -> dict[str, object]:
        return run_start(_args(
            self.root,
            study=None,
            snapshot=snapshot,
            mode=mode,
            execute_feeds=execute_feeds,
            feed_runtime_seconds=feed_runtime_seconds,
            feed_runtime_factory=feed_runtime_factory,
            execute_strategy=execute_strategy,
            strategy_runtime_factory=strategy_runtime_factory,
        ))

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
        return _download_registered_data_product(args)
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
        "primary_time": evidence.get("primary_time"),
        "fields": evidence.get("fields", []),
        "quality_level": release.quality_level.value,
        "artifact": str(Path(args.lake_root) / release.relative_path),
        "artifact_ref": evidence["artifact_ref"],
        "contract": "DataSet Contract",
    }
    report_path = Path(args.lake_root) / "downloads" / key / "report.json"
    _write_json(report_path, report)
    return {**report, "report": str(report_path)}


def data_register_download(args) -> dict[str, object]:
    key = _validate_download_key(args.key)
    payload = _read_contract(args.spec)
    if payload.get("kind") not in (None, "data.download"):
        raise ValueError("download spec kind must be data.download")
    if payload.get("key") not in (None, key):
        raise ValueError(f"download spec key {payload.get('key')!r} does not match --key {key!r}")
    spec_hash = _stable_hash(payload)
    target = _registered_download_spec_path(Path(args.lake_root), key)
    _write_json(target, {
        "key": key,
        "spec": payload,
        "spec_hash": spec_hash,
        "registered_at": _now(),
        "registered_from": str(Path(args.spec).resolve()),
    })
    return {"product": "data", "operation": "register-download", "key": key, "spec": str(target), "spec_hash": spec_hash}


def data_register_provider(args) -> dict[str, object]:
    name = _validate_provider_name(args.name)
    payload = _read_contract(args.spec)
    if payload.get("kind") not in (None, "data.provider"):
        raise ValueError("provider spec kind must be data.provider")
    source = payload.get("source") if isinstance(payload.get("source"), dict) else payload
    if not isinstance(source, dict):
        raise ValueError("provider spec must declare source")
    source_kind = str(source.get("kind") or source.get("type") or "")
    if source_kind not in {"python_provider", "provider", "acquire"}:
        raise ValueError(f"unsupported provider source kind {source_kind!r}; supported: python_provider")
    if not (source.get("path") or source.get("file") or source.get("module")):
        raise ValueError("provider spec python_provider source requires path")
    spec_hash = _stable_hash(payload)
    target = _registered_provider_spec_path(Path(args.lake_root), name)
    _write_json(target, {
        "name": name,
        "spec": payload,
        "spec_hash": spec_hash,
        "registered_at": _now(),
        "registered_from": str(Path(args.spec).resolve()),
    })
    return {"product": "data", "operation": "register-provider", "name": name, "spec": str(target), "spec_hash": spec_hash}


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
    root = Path(args.lake_root)
    return _write_historical_file(root, dataset_id, contract, source)


def _download_registered_data_product(args) -> dict[str, object]:
    key = _validate_download_key(args.key)
    root = Path(args.lake_root)
    registered = _read_registered_download(root, key)
    spec = registered["spec"]
    spec_base = Path(str(registered["registered_from"])).parent
    releases = [
        _write_registered_download_product(root, spec, item, spec_base)
        for item in _download_spec_products(spec)
    ]
    report = {
        "product": "data",
        "operation": "download",
        "key": key,
        "download_spec_hash": registered["spec_hash"],
        "registered_spec": str(_registered_download_spec_path(root, key)),
        "releases": releases,
    }
    if len(releases) == 1:
        report.update(releases[0])
    report_path = root / "downloads" / key / "report.json"
    _write_json(report_path, report)
    return {**report, "report": str(report_path)}


def _write_registered_download_product(
    root: Path,
    spec: dict[str, Any],
    product_entry: dict[str, Any],
    spec_base: Path,
) -> dict[str, object]:
    source_spec = product_entry.get("source") or spec.get("source")
    if not isinstance(source_spec, dict):
        raise ValueError("download spec product must declare source")
    source_spec, source_base, provider_ref = _resolve_download_source_spec(root, source_spec, spec_base)
    source_kind = str(source_spec.get("kind") or source_spec.get("type") or "")
    contract = _download_product_contract(product_entry, spec, spec_base)
    dataset_id = str(
        product_entry.get("dataset_id")
        or product_entry.get("as")
        or product_entry.get("product")
        or contract.get("dataset_id")
        or contract.get("identity", {}).get("dataset_id")
        or ""
    )
    if not dataset_id:
        raise ValueError("download spec product must declare dataset_id")
    contract_dataset = str(contract.get("dataset_id") or contract.get("identity", {}).get("dataset_id") or dataset_id)
    if contract_dataset != dataset_id:
        raise ValueError(f"download spec contract dataset_id {contract_dataset!r} does not match product {dataset_id!r}")
    existing = _existing_download_release(root, dataset_id, spec)
    if existing is not None:
        return existing
    if source_kind in {"local_csv", "csv", "file"}:
        source_value = source_spec.get("path") or source_spec.get("file")
        if not source_value:
            raise ValueError("download spec local_csv source requires path")
        source = _resolve_download_spec_file(str(source_value), source_base)
        release = _write_historical_file(root, dataset_id, contract, source)
        return {
            **release,
            "source": {"kind": "local_csv", "path": str(source)},
            "contract": "DataSet Contract",
        }
    if source_kind in {"python_provider", "provider", "acquire"}:
        source, provider_source = _acquire_registered_provider_source(
            root, spec, product_entry, source_spec, source_base, dataset_id, contract,
        )
        if provider_ref:
            provider_source["provider"] = provider_ref
        release = _write_historical_file(root, dataset_id, contract, source)
        return {
            **release,
            "source": provider_source,
            "contract": "DataSet Contract",
        }
    raise ValueError(
        f"unsupported download source kind {source_kind!r}; supported: local_csv, python_provider"
    )


def _resolve_download_source_spec(
    root: Path,
    source_spec: dict[str, Any],
    spec_base: Path,
) -> tuple[dict[str, Any], Path, str | None]:
    provider_name = source_spec.get("provider") or source_spec.get("provider_ref")
    if not provider_name:
        return source_spec, spec_base, None
    provider = _read_registered_provider(root, str(provider_name))
    provider_base = Path(provider["registered_from"]).parent
    provider_spec = provider["spec"]
    provider_source = provider_spec.get("source") if isinstance(provider_spec.get("source"), dict) else provider_spec
    if not isinstance(provider_source, dict):
        raise ValueError(f"registered provider {provider_name!r} is invalid")
    overrides = {key: value for key, value in source_spec.items() if key not in {"provider", "provider_ref"}}
    merged = {**provider_source, **overrides}
    return merged, provider_base, str(provider_name)


def _existing_download_release(root: Path, dataset_id: str, spec: dict[str, Any]) -> dict[str, object] | None:
    mode = spec.get("mode") if isinstance(spec.get("mode"), dict) else {}
    if not bool(mode.get("acquire_missing")):
        return None
    try:
        release = DataCatalog(root).release(dataset_id)
    except KeyError:
        return None
    evidence = _data_release_evidence(root, release)
    manifest_path = root / release.relative_path / "manifest.json"
    manifest = _read_json(manifest_path) if manifest_path.exists() else {}
    return {
        "dataset_id": dataset_id,
        "release_id": release.release_id,
        "content_hash": release.content_hash,
        "contract_hash": evidence["contract_hash"],
        "manifest_hash": evidence["manifest_hash"],
        "quality_level": release.quality_level.value,
        "primary_time": manifest.get("primary_time"),
        "fields": manifest.get("fields", []),
        "artifact": str(root / release.relative_path),
        "artifact_ref": evidence["artifact_ref"],
        "source": {"kind": "existing_release", "acquire_policy": "reused_existing_release"},
        "contract": "DataSet Contract",
    }


def _acquire_registered_provider_source(
    root: Path,
    spec: dict[str, Any],
    product_entry: dict[str, Any],
    source_spec: dict[str, Any],
    spec_base: Path,
    dataset_id: str,
    contract: dict[str, Any],
) -> tuple[Path, dict[str, object]]:
    source_value = source_spec.get("path") or source_spec.get("file") or source_spec.get("module")
    if not source_value:
        raise ValueError("download spec python_provider source requires path")
    provider_path = _resolve_download_spec_file(str(source_value), spec_base)
    function_name = str(source_spec.get("function") or "acquire")
    provider_hash = sha256(provider_path.read_bytes()).hexdigest()
    credentials, credential_evidence = _provider_credentials(root, source_spec)
    module = _load_user_module(provider_path, f"kairos_data_provider_{provider_hash[:12]}")
    acquire = getattr(module, function_name, None)
    if not callable(acquire):
        raise ValueError(f"download provider {provider_path} must define callable {function_name}(product, scope, context)")
    scope = {
        **(spec.get("scope") if isinstance(spec.get("scope"), dict) else {}),
        **(product_entry.get("scope") if isinstance(product_entry.get("scope"), dict) else {}),
    }
    context = {
        "dataset_id": dataset_id,
        "contract": contract,
        "source": _json_safe(source_spec),
        "credentials": credentials,
    }
    rows = _rows_from_factor_output(acquire(_json_safe(product_entry), _json_safe(scope), context))
    fields = _contract_fields(contract)
    if not rows:
        raise ValueError("download provider returned no rows")
    staging_hash = _stable_hash({
        "dataset_id": dataset_id,
        "provider_hash": provider_hash,
        "function": function_name,
        "scope": scope,
        "rows": rows,
    })
    staging = root / "downloads" / "_provider-staging" / dataset_id.replace(".", "/") / staging_hash[:12] / "rows.csv"
    _write_rows_csv(staging, rows, fields)
    return staging, {
        "kind": "python_provider",
        "path": str(provider_path),
        "function": function_name,
        "provider_code_hash": provider_hash,
        "credentials": credential_evidence,
        "row_count": len(rows),
        "staging_hash": staging_hash,
    }


def _provider_credentials(root: Path, source_spec: dict[str, Any]) -> tuple[dict[str, str], dict[str, object]]:
    required_env = _provider_required_env_names(source_spec)
    missing_env = [name for name in required_env if not os.environ.get(name)]
    if missing_env:
        raise ValueError(f"missing required provider credentials: {', '.join(missing_env)}")
    values = {name: os.environ[name] for name in required_env}
    evidence: dict[str, object] = {
        "required_env": required_env,
        "provided": {name: True for name in required_env},
    }
    required_config = _provider_required_config_keys(source_spec)
    if required_config:
        try:
            from kairos.configuration import KairosProjectConfig
            config = KairosProjectConfig.discover(root)
        except Exception as error:
            raise ValueError("provider config credentials require kairos.toml") from error
        config_sources: dict[str, str] = {}
        missing_config: list[str] = []
        for key in required_config:
            resolved = config.resolve(key)
            if resolved.resolved in (None, ""):
                missing_config.append(key)
                continue
            values[key] = str(resolved.resolved)
            config_sources[key] = resolved.source
        if missing_config:
            raise ValueError(f"missing required provider config credentials: {', '.join(missing_config)}")
        evidence["required_config"] = required_config
        evidence["config_sources"] = config_sources
        evidence["config_provided"] = {name: True for name in required_config}
    return values, evidence


def _provider_required_env_names(source_spec: dict[str, Any]) -> list[str]:
    credentials = source_spec.get("credentials")
    raw: object = source_spec.get("credential_env")
    if isinstance(credentials, dict):
        raw = credentials.get("env") or credentials.get("required_env") or raw
    if raw is None:
        return []
    if isinstance(raw, str):
        names = [raw]
    elif isinstance(raw, list):
        names = [str(item) for item in raw]
    else:
        raise ValueError("download spec provider credentials must declare env as a string or list")
    clean = sorted({name for name in names if name})
    if len(clean) != len(names):
        raise ValueError("download spec provider credentials env names must not be empty")
    return clean


def _provider_required_config_keys(source_spec: dict[str, Any]) -> list[str]:
    credentials = source_spec.get("credentials")
    raw: object = source_spec.get("credential_config")
    if isinstance(credentials, dict):
        raw = credentials.get("config") or credentials.get("required_config") or raw
    if raw is None:
        return []
    if isinstance(raw, str):
        keys = [raw]
    elif isinstance(raw, list):
        keys = [str(item) for item in raw]
    else:
        raise ValueError("download spec provider credentials must declare config as a string or list")
    clean = sorted({key for key in keys if key})
    if len(clean) != len(keys):
        raise ValueError("download spec provider credentials config keys must not be empty")
    return clean


def _download_spec_products(spec: dict[str, Any]) -> list[dict[str, Any]]:
    products = spec.get("products")
    if products is None:
        return [spec]
    if not isinstance(products, list) or not products:
        raise ValueError("download spec products must be a non-empty list")
    results: list[dict[str, Any]] = []
    for item in products:
        if isinstance(item, str):
            results.append({"dataset_id": item})
        elif isinstance(item, dict):
            results.append(item)
        else:
            raise ValueError("download spec products entries must be strings or objects")
    return results


def _download_product_contract(product_entry: dict[str, Any], spec: dict[str, Any], spec_base: Path) -> dict[str, Any]:
    contract_value = product_entry.get("contract") or spec.get("contract")
    if isinstance(contract_value, dict):
        return contract_value
    if contract_value:
        return _read_contract(_resolve_download_spec_file(str(contract_value), spec_base))
    raise ValueError("download spec product must declare contract")


def _factor_metadata_contract(
    study: dict[str, Any],
    metadata_path: str | Path | None,
    workspace_dir: Path,
) -> dict[str, object]:
    if metadata_path is None:
        return {
            "metadata_status": "missing",
            "strategy_eligible": False,
        }
    source = _resolve_user_file(metadata_path, workspace_dir)
    metadata = _read_contract(source)
    inputs = _factor_metadata_inputs(metadata)
    declared_data = set(study.get("data", {}))
    unknown_inputs = sorted(set(inputs) - declared_data)
    if unknown_inputs:
        raise ValueError(f"factor metadata references undeclared study data aliases: {', '.join(unknown_inputs)}")
    parameters = metadata.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise ValueError("factor metadata parameters must be an object")
    _assert_json_serializable(parameters, "factor metadata parameters")
    output_schema = _factor_output_schema(metadata)
    fields = output_schema["fields"]
    primary_time = str(metadata.get("primary_time") or metadata.get("time", {}).get("primary_time") or "")
    if not primary_time:
        raise ValueError("factor metadata must declare primary_time")
    if primary_time not in fields:
        raise ValueError(f"factor metadata primary_time {primary_time!r} is not in output fields")
    point_in_time = metadata.get("point_in_time")
    if not isinstance(point_in_time, bool):
        raise ValueError("factor metadata must declare boolean point_in_time")
    dependencies = metadata.get("dependencies") or []
    if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
        raise ValueError("factor metadata dependencies must be a list of strings")
    runtime = _factor_runtime_contract(metadata.get("runtime") or {})
    contract = {
        "inputs": inputs,
        "parameters": _json_safe(parameters),
        "output_schema": output_schema,
        "primary_time": primary_time,
        "point_in_time": point_in_time,
        "strategy_eligible": bool(metadata.get("strategy_eligible", True)),
        "dependencies": dependencies,
        "runtime": runtime,
    }
    _assert_json_serializable(contract, "factor metadata contract")
    parameters_hash = _stable_hash(parameters)
    factor_contract_hash = _stable_hash(contract)
    return {
        "metadata_status": "declared",
        "metadata_path": str(source),
        "metadata": contract,
        "inputs": inputs,
        "parameters": _json_safe(parameters),
        "parameters_hash": parameters_hash,
        "output_schema": output_schema,
        "primary_time": primary_time,
        "point_in_time": point_in_time,
        "strategy_eligible": contract["strategy_eligible"],
        "dependencies": dependencies,
        "runtime": runtime,
        "factor_contract_hash": factor_contract_hash,
    }


def _factor_metadata_inputs(metadata: dict[str, Any]) -> list[str]:
    raw_inputs = metadata.get("inputs")
    if isinstance(raw_inputs, dict):
        inputs = [str(key) for key in raw_inputs]
    elif isinstance(raw_inputs, list):
        inputs = [str(item) for item in raw_inputs]
    else:
        raise ValueError("factor metadata must declare inputs as an object or list")
    if not inputs:
        raise ValueError("factor metadata inputs must not be empty")
    return sorted(inputs)


def _factor_output_schema(metadata: dict[str, Any]) -> dict[str, object]:
    raw_schema = metadata.get("output_schema") or {}
    raw_fields = metadata.get("fields")
    if isinstance(raw_schema, dict) and raw_schema.get("fields") is not None:
        raw_fields = raw_schema.get("fields")
    fields: list[str] = []
    for item in raw_fields or ():
        fields.append(str(item.get("name") if isinstance(item, dict) else item))
    if not fields:
        raise ValueError("factor metadata must declare output fields")
    return {
        "fields": fields,
        **({key: value for key, value in raw_schema.items() if key != "fields"} if isinstance(raw_schema, dict) else {}),
    }


def _factor_runtime_contract(raw_runtime: object) -> dict[str, object]:
    if not isinstance(raw_runtime, dict):
        raise ValueError("factor metadata runtime must be an object")
    network = raw_runtime.get("network", raw_runtime.get("network_allowed", False))
    if bool(network):
        raise ValueError("factor metadata runtime cannot allow network access")
    filesystem = str(raw_runtime.get("filesystem") or raw_runtime.get("filesystem_access") or "study_inputs_only")
    if filesystem not in {"study_inputs_only", "none"}:
        raise ValueError("factor metadata runtime filesystem access must be study_inputs_only or none")
    paths = raw_runtime.get("paths") or raw_runtime.get("read_paths") or raw_runtime.get("allowed_paths") or []
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        raise ValueError("factor metadata runtime paths must be a list of strings")
    if paths:
        raise ValueError("factor metadata runtime cannot declare filesystem paths outside Study inputs")
    return {
        "network_allowed": False,
        "filesystem_access": filesystem,
        "paths": [],
    }


def _assert_json_serializable(value: object, label: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError as error:
        raise ValueError(f"{label} must be JSON serializable") from error


def _load_user_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load Python module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows_from_factor_output(value: object) -> list[dict[str, object]]:
    if isinstance(value, InputTableRef):
        return value.rows()
    if hasattr(value, "to_pylist") and callable(getattr(value, "to_pylist")):
        rows = value.to_pylist()
    elif hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
        rows = value.to_dict(orient="records")  # pandas-like
    elif isinstance(value, dict) and isinstance(value.get("rows"), list):
        rows = value["rows"]
    elif isinstance(value, list):
        rows = value
    else:
        raise ValueError("factor compute output must be rows, pandas/arrow table, InputTableRef, or {'rows': [...]}")
    if not all(isinstance(item, dict) for item in rows):
        raise ValueError("factor compute rows must be objects")
    return [_json_safe(dict(item)) for item in rows]


def _factor_run_profile(
    study_id: str,
    factor_name: str,
    factor: dict[str, Any],
    rows: list[dict[str, object]],
) -> dict[str, object]:
    fields = sorted({field for row in rows for field in row})
    primary_time = str(factor.get("primary_time") or "")
    missing_primary = bool(primary_time) and any(row.get(primary_time) in (None, "") for row in rows)
    missing_values = sum(value in (None, "") for row in rows for value in row.values())
    point_in_time = bool(factor.get("point_in_time"))
    declared_fields = list(factor.get("output_schema", {}).get("fields", []))
    missing_declared = sorted(set(declared_fields) - set(fields))
    pit_check = _factor_point_in_time_check(rows, primary_time, fields, point_in_time)
    passed = (
        bool(rows)
        and bool(primary_time)
        and primary_time in fields
        and not missing_primary
        and not missing_declared
        and bool(pit_check["passed"])
    )
    return {
        "kind": "factor.run.profile",
        "schema_version": 1,
        "study_id": study_id,
        "factor": factor_name,
        "row_count": len(rows),
        "fields": fields,
        "declared_fields": declared_fields,
        "missing_declared_fields": missing_declared,
        "primary_time": primary_time,
        "primary_time_present": primary_time in fields,
        "missing_primary_time_values": missing_primary,
        "missing_values": missing_values,
        "point_in_time": point_in_time,
        "point_in_time_check": pit_check["status"],
        "lookahead_rows": pit_check["lookahead_rows"],
        "factor_contract_hash": factor.get("factor_contract_hash"),
        "parameters_hash": factor.get("parameters_hash"),
        "passed": passed,
        "ran_at": _now(),
    }


def _factor_point_in_time_check(
    rows: list[dict[str, object]],
    primary_time: str,
    fields: list[str],
    point_in_time: bool,
) -> dict[str, object]:
    if not point_in_time:
        return {"passed": True, "status": "not_declared", "lookahead_rows": 0}
    if not primary_time or primary_time not in fields or "event_time" not in fields:
        return {"passed": True, "status": "declared", "lookahead_rows": 0}
    lookahead_rows = 0
    for row in rows:
        primary_value = _parse_factor_time(row.get(primary_time))
        event_value = _parse_factor_time(row.get("event_time"))
        if primary_value is not None and event_value is not None and primary_value < event_value:
            lookahead_rows += 1
    return {
        "passed": lookahead_rows == 0,
        "status": "passed" if lookahead_rows == 0 else "failed",
        "lookahead_rows": lookahead_rows,
    }


def _parse_factor_time(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _factor_output_hash(profile: dict[str, object], rows: list[dict[str, object]]) -> str:
    return _stable_hash({
        "fields": profile.get("declared_fields") or profile.get("fields", []),
        "primary_time": profile.get("primary_time"),
        "rows": rows,
    })


def _factor_run_hash(profile: dict[str, object], rows: list[dict[str, object]]) -> str:
    return _stable_hash({
        "profile": {key: value for key, value in profile.items() if key not in {"run_hash", "factor_output_hash"}},
        "rows": rows,
    })


def _study_readiness(study: dict[str, Any]) -> dict[str, object]:
    data = study.get("data", {})
    factors = study.get("factors", {})
    checks: list[dict[str, object]] = []
    blocking: list[str] = []

    has_data = bool(data)
    checks.append({
        "name": "declared_data",
        "kind": "gate",
        "passed": has_data,
        "detail": "Study must bind at least one Data Release before freeze",
    })
    if not has_data:
        blocking.append("missing_data")

    for name, item in sorted(data.items()):
        level = str(item.get("quality_level") or "")
        passed = _quality_level_at_least(level, QualityLevel.STUDY)
        checks.append({
            "name": f"data_quality:{name}",
            "kind": "gate",
            "passed": passed,
            "quality_level": level,
            "minimum": QualityLevel.STUDY.value,
            "detail": "Study data must meet the study quality gate",
        })
        if not passed:
            blocking.append(f"data_quality_too_low:{name}")

    missing_metadata = sorted(
        name for name, item in factors.items()
        if item.get("metadata_status") != "declared"
    )
    checks.append({
        "name": "factor_metadata",
        "kind": "diagnostic",
        "passed": not missing_metadata,
        "missing": missing_metadata,
        "detail": "Factor metadata is required before Strategy-grade semantic checks",
    })

    return {
        "kind": "study.readiness",
        "schema_version": 1,
        "lifecycle": "FROZEN_CANDIDATE" if not blocking else "DRAFT",
        "passed": not blocking,
        "blocking_reasons": blocking,
        "diagnostics": {
            "factor_metadata_missing": missing_metadata,
        },
        "checks": checks,
        "checked_at": _now(),
    }


def _quality_level_at_least(value: str, minimum: QualityLevel) -> bool:
    try:
        level = QualityLevel(value)
    except ValueError:
        return False
    return _quality_level_rank(level) >= _quality_level_rank(minimum)


def _quality_level_rank(level: QualityLevel) -> int:
    order = {
        QualityLevel.ARCHIVED: 0,
        QualityLevel.INTEGRITY: 1,
        QualityLevel.STUDY: 2,
        QualityLevel.BACKTEST: 3,
        QualityLevel.PRODUCTION: 4,
    }
    return order[level]


def _write_historical_file(
    root: Path,
    dataset_id: str,
    contract: dict[str, Any],
    source: Path,
) -> dict[str, object]:
    if not source.exists():
        raise FileNotFoundError(source)
    fields = _contract_fields(contract)
    primary_time = str(contract.get("primary_time") or contract.get("time", {}).get("primary_time") or "")
    if not primary_time:
        raise ValueError("data write contract must declare primary_time")
    _validate_csv_header(source, fields)
    material = source.read_bytes() + json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    content_hash = sha256(material).hexdigest()
    release_id = f"{dataset_id}:write:{content_hash[:12]}"
    directory = root / "external" / dataset_id.replace(".", "/") / release_id
    directory.mkdir(parents=True, exist_ok=True)
    copied = directory / source.name
    if not copied.exists() or copied.read_bytes() != source.read_bytes():
        copied.write_bytes(source.read_bytes())
    readable_csv = directory / "event_year=all" / "event_month=all" / "part-000.csv"
    readable_csv.parent.mkdir(parents=True, exist_ok=True)
    if not readable_csv.exists() or readable_csv.read_bytes() != source.read_bytes():
        readable_csv.write_bytes(source.read_bytes())
    manifest = DataReleaseManifest(
        dataset_id,
        release_id,
        stable_artifact_hash(contract),
        content_hash,
        primary_time,
        tuple(fields),
        QualityLevel.STUDY,
        {"kind": "file", "name": source.name},
        _now(),
    )
    manifest_payload = manifest.to_primitive()
    _write_json(directory / "manifest.json", manifest_payload)
    quality_report = _data_quality_report(source, dataset_id, contract, fields, primary_time)
    quality_report_hash = _stable_hash(quality_report)
    quality_report = {**quality_report, "quality_report_hash": quality_report_hash}
    _write_json(directory / "quality.json", quality_report)
    _register_written_release(root, dataset_id, release_id, directory, content_hash, primary_time)
    return {
        **manifest_payload,
        "manifest_hash": manifest.manifest_hash,
        "quality_report": str(directory / "quality.json"),
        "quality_report_hash": quality_report_hash,
        "artifact": str(directory / "manifest.json"),
        "artifact_ref": manifest.artifact_ref,
    }


def _data_quality_report(
    source: Path,
    dataset_id: str,
    contract: dict[str, Any],
    fields: list[str],
    primary_time: str,
) -> dict[str, object]:
    with source.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    missing_by_field = {
        field: sum(row.get(field) in (None, "") for row in rows)
        for field in fields
    }
    primary_values = [row.get(primary_time) for row in rows]
    duplicate_primary = len(primary_values) - len(set(primary_values))
    required_fields_ok = all(field in (rows[0].keys() if rows else fields) for field in fields)
    non_empty = bool(rows)
    primary_time_present = primary_time in fields
    primary_time_complete = missing_by_field.get(primary_time, 0) == 0
    gate_checks = [
        {"name": "non_empty", "passed": non_empty, "kind": "gate"},
        {"name": "required_fields", "passed": required_fields_ok, "kind": "gate", "fields": fields},
        {"name": "primary_time_present", "passed": primary_time_present, "kind": "gate", "primary_time": primary_time},
        {"name": "primary_time_complete", "passed": primary_time_complete, "kind": "gate", "missing": missing_by_field.get(primary_time, 0)},
    ]
    diagnostic_checks = [
        {"name": "missing_values", "passed": sum(missing_by_field.values()) == 0, "kind": "diagnostic", "missing_by_field": missing_by_field},
        {"name": "duplicate_primary_time", "passed": duplicate_primary == 0, "kind": "diagnostic", "duplicates": duplicate_primary},
    ]
    return {
        "kind": "data.quality_report",
        "schema_version": 1,
        "dataset_id": dataset_id,
        "primary_time": primary_time,
        "quality_level": QualityLevel.STUDY.value,
        "row_count": len(rows),
        "fields": fields,
        "contract_hash": stable_artifact_hash(contract),
        "gate_checks": gate_checks,
        "diagnostic_checks": diagnostic_checks,
        "gate_passed": all(bool(item["passed"]) for item in gate_checks),
        "diagnostic_passed": all(bool(item["passed"]) for item in diagnostic_checks),
        "checked_at": _now(),
    }


def _write_rows_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


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
    _write_study_state(args.lake_root, args.study_id, study)
    _append_study_lifecycle(args.lake_root, args.study_id, "opened", study)
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
        "primary_time": evidence.get("primary_time"),
        "fields": evidence.get("fields", []),
        "quality_level": release.quality_level.value,
    }
    _write_study_state(args.lake_root, workspace, study)
    return {"product": "study", "operation": "add-data", "study_id": workspace, "name": args.name, **study["data"][args.name]}


def study_add_factor(args) -> dict[str, object]:
    workspace = _workspace_arg(args)
    study = _load_study(args.lake_root, workspace)
    source = _resolve_user_file(args.file, _study_dir(args.lake_root, workspace))
    code_hash = sha256(source.read_bytes()).hexdigest()
    metadata_path = getattr(args, "metadata", None)
    factor_metadata = _factor_metadata_contract(study, metadata_path, _study_dir(args.lake_root, workspace))
    factor = {
        "path": str(source),
        "code_hash": code_hash,
        "source_hash": code_hash,
        "artifact_ref": f"study://{workspace}/factors/{args.name}",
        "contract": "Factor Contract",
        **factor_metadata,
    }
    study.setdefault("factors", {})[args.name] = factor
    _write_study_state(args.lake_root, workspace, study)
    return {
        "product": "study",
        "operation": "add-factor",
        "study_id": workspace,
        "name": args.name,
        "code_hash": code_hash,
        **{key: value for key, value in factor_metadata.items() if key != "metadata"},
    }


def study_inspect(args) -> dict[str, object]:
    study = _load_study(args.lake_root, args.study_id)
    return {**study, "contract": "Study Contract"}


def study_factor_run(args) -> dict[str, object]:
    study = _load_study(args.lake_root, args.study_id)
    factors = study.get("factors", {})
    if args.name not in factors:
        raise ValueError(f"Study factor {args.name!r} is not declared")
    factor = factors[args.name]
    if factor.get("metadata_status") != "declared":
        raise ValueError(f"study factor {args.name!r} requires metadata before factor-run")
    source = Path(str(factor.get("path") or ""))
    if not source.exists():
        raise FileNotFoundError(source)
    inputs = {
        alias: StudyProductApi(args.lake_root).data(args.study_id, alias)
        for alias in factor.get("inputs", [])
    }
    module = _load_user_module(source, f"kairos_user_factor_{args.study_id}_{args.name}")
    compute = getattr(module, "compute", None)
    if compute is None or not callable(compute):
        raise ValueError(f"study factor {args.name!r} must define callable compute(inputs, params, context)")
    result = compute(inputs, factor.get("parameters", {}), {
        "study_id": args.study_id,
        "factor": args.name,
        "run_mode": "study",
    })
    rows = _rows_from_factor_output(result)
    profile = _factor_run_profile(args.study_id, args.name, factor, rows)
    factor_output_hash = _factor_output_hash(profile, rows)
    run_hash = _factor_run_hash(profile, rows)
    profile = {**profile, "factor_output_hash": factor_output_hash, "run_hash": run_hash}
    run_dir = _study_dir(args.lake_root, args.study_id) / "factor-runs" / args.name / run_hash[:12]
    _write_json(run_dir / "rows.json", rows)
    _write_json(run_dir / "profile.json", profile)
    study.setdefault("factor_runs", {})[args.name] = {
        "profile": str(run_dir / "profile.json"),
        "rows": str(run_dir / "rows.json"),
        "run_hash": run_hash,
        "factor_output_hash": factor_output_hash,
        "row_count": profile["row_count"],
        "fields": profile["fields"],
        "ran_at": profile["ran_at"],
    }
    _write_study_state(args.lake_root, args.study_id, study)
    return {
        "product": "study",
        "operation": "factor-run",
        "study_id": args.study_id,
        "name": args.name,
        "profile": str(run_dir / "profile.json"),
        "rows": str(run_dir / "rows.json"),
        **profile,
    }


def study_publish_factor(args) -> dict[str, object]:
    study = _load_study(args.lake_root, args.study_id)
    factors = study.get("factors", {})
    if args.name not in factors:
        raise ValueError(f"Study factor {args.name!r} is not declared")
    factor = factors[args.name]
    run = study.get("factor_runs", {}).get(args.name)
    if not isinstance(run, dict):
        raise ValueError(f"study factor {args.name!r} has no factor-run to publish")
    profile = _read_json(run["profile"])
    if not profile.get("passed"):
        raise ValueError(f"study factor {args.name!r} latest factor-run did not pass")
    rows = _read_json(run["rows"])
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"study factor {args.name!r} factor-run rows are empty")
    expected_run_hash = str(profile.get("run_hash") or "")
    if expected_run_hash and _factor_run_hash(profile, rows) != expected_run_hash:
        raise ValueError(f"study factor {args.name!r} factor-run hash does not match rows")
    fields = list(factor.get("output_schema", {}).get("fields", [])) or list(profile.get("fields", []))
    if not fields:
        raise ValueError(f"study factor {args.name!r} has no output fields to publish")
    primary_time = str(factor.get("primary_time") or profile.get("primary_time") or "")
    if not primary_time:
        raise ValueError(f"study factor {args.name!r} has no primary_time to publish")
    factor_output_hash = str(profile.get("factor_output_hash") or _factor_output_hash(profile, rows))
    dataset_id = str(args.as_dataset)
    publish_dir = _study_dir(args.lake_root, args.study_id) / "factor-publish" / args.name / str(profile["run_hash"])[:12]
    publish_dir.mkdir(parents=True, exist_ok=True)
    csv_path = publish_dir / f"{args.name}.csv"
    _write_rows_csv(csv_path, rows, fields)
    contract = {
        "dataset_id": dataset_id,
        "primary_time": primary_time,
        "fields": fields,
        "grain": factor.get("output_schema", {}).get("grain", "factor_output"),
        "lineage": {
            "study_id": args.study_id,
            "factor": args.name,
            "factor_contract_hash": factor.get("factor_contract_hash"),
            "parameters_hash": factor.get("parameters_hash"),
            "factor_run_hash": profile.get("run_hash"),
            "factor_output_hash": factor_output_hash,
        },
    }
    release = _write_historical_file(Path(args.lake_root), dataset_id, contract, csv_path)
    result = {
        "product": "study",
        "operation": "publish-factor",
        "study_id": args.study_id,
        "name": args.name,
        "as_dataset": dataset_id,
        "factor_run_hash": profile.get("run_hash"),
        "factor_output_hash": factor_output_hash,
        **release,
    }
    study.setdefault("published_factors", {})[args.name] = {
        "dataset": dataset_id,
        "release_id": release["release_id"],
        "content_hash": release["content_hash"],
        "contract_hash": release["contract_hash"],
        "artifact_ref": release["artifact_ref"],
        "manifest_hash": release["manifest_hash"],
        "quality_level": release["quality_level"],
        "primary_time": release["primary_time"],
        "fields": release["fields"],
        "factor_run_hash": profile.get("run_hash"),
        "factor_output_hash": factor_output_hash,
        "quality_report_hash": release.get("quality_report_hash"),
        "published_at": _now(),
    }
    _write_study_state(args.lake_root, args.study_id, study)
    return result


def study_freeze(args) -> dict[str, object]:
    study = _load_study(args.lake_root, args.study_id)
    version = args.version
    readiness = _study_readiness(study)
    if not readiness["passed"]:
        raise ValueError(f"Study is not ready to freeze: {', '.join(readiness['blocking_reasons'])}")
    path = _study_dir(args.lake_root, args.study_id) / "locks" / version / "study.lock.json"
    if path.exists():
        raise ValueError(f"Study Lock version already exists: {args.study_id}@{version}")
    lock_body = {
        "product": "study",
        "kind": "study.lock",
        "schema_version": 1,
        "study_id": args.study_id,
        "version": version,
        "lifecycle": "FROZEN",
        "data": study.get("data", {}),
        "factors": study.get("factors", {}),
        "published_factors": study.get("published_factors", {}),
        "readiness": readiness,
        "evidence_chain": _study_evidence_chain(study),
        "frozen_at": _now(),
    }
    lock_hash = _stable_hash(lock_body)
    lock = {**lock_body, "lock_hash": lock_hash}
    _write_json(path, lock)
    _write_json(path.parent / "readiness.json", readiness)
    study["status"] = "frozen"
    study["lifecycle"] = "FROZEN"
    study["readiness"] = readiness
    study["latest_lock"] = str(path)
    study["latest_lock_hash"] = lock_hash
    _write_study_state(args.lake_root, args.study_id, study)
    _append_study_lifecycle(args.lake_root, args.study_id, "frozen", study, {"version": version, "lock_hash": lock_hash})
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
    study_factor = factors[args.study_factor]
    if study_factor.get("metadata_status") == "declared" and not bool(study_factor.get("strategy_eligible")):
        raise ValueError(f"study factor {args.study_factor!r} is not strategy eligible")
    published = study_lock.get("published_factors", {}).get(args.study_factor, {})
    materialized = {
        key: published.get(key)
        for key in (
            "dataset", "release_id", "content_hash", "contract_hash", "manifest_hash",
            "quality_level", "primary_time", "fields", "factor_run_hash", "factor_output_hash", "quality_report_hash",
        )
        if isinstance(published, dict) and published.get(key) is not None
    }
    if isinstance(published, dict) and published.get("artifact_ref"):
        materialized["feature_artifact_ref"] = published.get("artifact_ref")
    strategy.setdefault("inputs", {})[args.name] = {
        "kind": "factor",
        "from_study_factor": args.study_factor,
        "source_hash": study_factor["code_hash"],
        "factor_contract_hash": study_factor.get("factor_contract_hash"),
        "parameters_hash": study_factor.get("parameters_hash"),
        "primary_time": study_factor.get("primary_time"),
        "point_in_time": study_factor.get("point_in_time"),
        "output_schema": study_factor.get("output_schema"),
        "runtime": study_factor.get("runtime"),
        "metadata_status": study_factor.get("metadata_status"),
        "strategy_eligible": study_factor.get("strategy_eligible"),
        "materialization_status": "published_feature" if materialized else "contract_only",
        **materialized,
        "contract": study_factor.get("contract", "Factor Contract"),
        "artifact_ref": f"study://{derived['study']}/locks/{derived['version']}/factors/{args.study_factor}",
    }
    _write_json(_strategy_file(args.lake_root, workspace), strategy)
    return {"product": "strategy", "operation": "bind-factor", "strategy_id": workspace, "name": args.name, **strategy["inputs"][args.name]}


def strategy_set_risk(args) -> dict[str, object]:
    strategy = _load_strategy(args.lake_root, args.strategy_id)
    source = _resolve_user_file(args.risk_file, _strategy_dir(args.lake_root, args.strategy_id))
    policy = _read_policy_contract(source, "risk")
    risk_hash = _stable_hash(policy)
    strategy["risk"] = {
        "path": str(source),
        "policy": policy,
        "hash": risk_hash,
        "risk_policy_hash": risk_hash,
        "contract": "Risk Policy Contract",
    }
    _write_json(_strategy_file(args.lake_root, args.strategy_id), strategy)
    return {
        "product": "strategy",
        "operation": "set-risk",
        "strategy_id": args.strategy_id,
        "risk_hash": risk_hash,
        "risk_policy_hash": risk_hash,
        "contract": "Risk Policy Contract",
    }


def strategy_set_execution(args) -> dict[str, object]:
    strategy = _load_strategy(args.lake_root, args.strategy_id)
    source = _resolve_user_file(args.execution_file, _strategy_dir(args.lake_root, args.strategy_id))
    policy = _read_policy_contract(source, "execution")
    execution_hash = _stable_hash(policy)
    strategy["execution"] = {
        "path": str(source),
        "policy": policy,
        "hash": execution_hash,
        "execution_policy_hash": execution_hash,
        "contract": "Execution Policy Contract",
    }
    _write_json(_strategy_file(args.lake_root, args.strategy_id), strategy)
    return {
        "product": "strategy",
        "operation": "set-execution",
        "strategy_id": args.strategy_id,
        "execution_policy_hash": execution_hash,
        "contract": "Execution Policy Contract",
    }


def strategy_set_model(args) -> dict[str, object]:
    strategy = _load_strategy(args.lake_root, args.strategy_id)
    kind = str(args.kind)
    if kind not in {"sma-cross-v1", "builtin.sma-cross-v1"}:
        raise ValueError(f"unsupported Strategy runtime model: {kind!r}")
    parameters = {
        key: value for key, value in {
            "instrument_id": getattr(args, "instrument_id", None),
            "fast_window": getattr(args, "fast_window", None),
            "slow_window": getattr(args, "slow_window", None),
            "approved_capital": getattr(args, "approved_capital", None),
        }.items() if value is not None
    }
    strategy["model"] = {
        "kind": "sma-cross-v1",
        "runtime": "built-in",
        "parameters": _json_safe(parameters),
    }
    _write_json(_strategy_file(args.lake_root, args.strategy_id), strategy)
    return {
        "product": "strategy",
        "operation": "set-model",
        "strategy_id": args.strategy_id,
        **strategy["model"],
    }


def strategy_set_model_code(args) -> dict[str, object]:
    strategy = _load_strategy(args.lake_root, args.strategy_id)
    source = _resolve_user_file(args.model_file, _strategy_dir(args.lake_root, args.strategy_id))
    source_bytes = source.read_bytes()
    code_hash = sha256(source_bytes).hexdigest()
    artifact_path = _strategy_dir(args.lake_root, args.strategy_id) / "model" / code_hash[:12] / source.name
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    if not artifact_path.exists() or artifact_path.read_bytes() != source_bytes:
        artifact_path.write_bytes(source_bytes)
    metadata_path = getattr(args, "metadata", None)
    model_contract = _strategy_model_contract(strategy, metadata_path, _strategy_dir(args.lake_root, args.strategy_id))
    model = {
        "kind": "user-model-code",
        "runtime": "user",
        "path": str(source),
        "artifact_path": str(artifact_path),
        "model_code_hash": code_hash,
        "code_hash": code_hash,
        "contract": "Strategy Model Contract",
        **model_contract,
    }
    strategy["model"] = model
    _write_json(_strategy_file(args.lake_root, args.strategy_id), strategy)
    return {
        "product": "strategy",
        "operation": "set-model-code",
        "strategy_id": args.strategy_id,
        "model_code_hash": code_hash,
        "artifact_path": str(artifact_path),
        **{key: value for key, value in model_contract.items() if key != "metadata"},
    }


def strategy_inspect(args) -> dict[str, object]:
    return {**_load_strategy(args.lake_root, args.strategy_id), "contract": "Strategy Contract"}


def strategy_freeze(args) -> dict[str, object]:
    strategy = _load_strategy(args.lake_root, args.strategy_id)
    derived = strategy["derived_from"]
    study_lock = _load_study_lock(args.lake_root, derived["study"], derived["version"])
    _assert_strategy_study_consistency(strategy, study_lock)
    path = _strategy_dir(args.lake_root, args.strategy_id) / "locks" / args.version / "strategy.lock.json"
    if path.exists():
        raise ValueError(f"Strategy Lock version already exists: {args.strategy_id}@{args.version}")
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
            "risk_policy_hash": "passed" if strategy.get("risk", {}).get("risk_policy_hash") else "not_declared",
            "execution_policy_hash": "passed" if strategy.get("execution", {}).get("execution_policy_hash") else "not_declared",
            "model_contract_hash": "passed" if strategy.get("model", {}).get("model_contract_hash") else "not_declared",
        },
        "frozen_at": _now(),
    }
    lock_hash = _stable_hash(lock_body)
    lock = {**lock_body, "lock_hash": lock_hash}
    _write_json(path, lock)
    strategy["version"] = args.version
    strategy["latest_lock"] = str(path)
    strategy["latest_lock_hash"] = lock_hash
    _write_json(_strategy_file(args.lake_root, args.strategy_id), strategy)
    return {**lock, "artifact": str(path)}


def run_start(args) -> dict[str, object]:
    root = Path(args.lake_root)
    mode = args.mode
    if args.study:
        target_id = args.study
        target_kind = "study"
        target = _load_study(root, args.study)
        target_hash = _stable_hash(target)
        target_source = str(_study_file(root, args.study))
    else:
        strategy_id, version = _split_ref(args.snapshot)
        target_id = strategy_id
        target_kind = "strategy"
        target = _load_strategy_lock(root, strategy_id, version)
        target_hash = target["lock_hash"]
        target_source = str(_strategy_dir(root, strategy_id) / "locks" / version / "strategy.lock.json")
    workspace_evidence = _run_target_workspace_evidence(root, target_kind, target_id, target, target_hash)
    live_bindings = _paper_live_subscription_bindings(root, target) if mode in {"paper", "live"} else ()
    feed_plan = runtime_feed_plan(mode, live_bindings) if live_bindings else None
    if getattr(args, "execute_feeds", False) and feed_plan is None:
        raise ValueError("feed execution requires paper/live feed bindings")
    composition = _run_mode_composition(mode)
    execution_plan = runtime_execution_plan(mode, composition) if mode in {"paper", "live"} else None
    strategy_plan = runtime_strategy_plan(mode, strategy_id=target_id, target_hash=target_hash) if (
        target_kind == "strategy" and mode in {"paper", "live"}
    ) else None
    strategy_decision_supported = (
        target_kind == "strategy"
        and mode in {"backtest", "historical-simulation"}
        and target.get("model", {}).get("kind") == "user-model-code"
    )
    if getattr(args, "execute_strategy", False) and strategy_plan is None and not strategy_decision_supported:
        raise ValueError(
            "strategy execution requires paper/live runtime model or backtest/historical-simulation user-model-code"
        )
    freshness_gates = tuple(
        {"name": item["name"], "dataset": item["dataset"], **item["freshness_gate"]}
        for item in live_bindings
    )
    material = {"target_kind": target_kind, "target_id": target_id, "mode": mode, "target_hash": target_hash, "at": _now()}
    run_id = f"run_{sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:16]}"
    directory = root / "runs" / target_id / run_id
    snapshot_artifact = directory / "snapshot.json"
    feed_runtime_execution = _execute_feed_runtime(root, directory, mode, feed_plan, execution_plan, strategy_plan, target, args) if (
        (feed_plan and getattr(args, "execute_feeds", False))
        or (strategy_plan is not None and getattr(args, "execute_strategy", False))
    ) else None
    strategy_decision_execution = _execute_strategy_model_decision(root, directory, target, mode) if (
        getattr(args, "execute_strategy", False) and strategy_decision_supported
    ) else None
    manifest = {
        "product": "run",
        "kind": "run.manifest",
        "schema_version": 1,
        "run_id": run_id,
        "mode": mode,
        "target": {
            "kind": target_kind,
            "id": target_id,
            "hash": target_hash,
            "snapshot_hash": target_hash,
            "snapshot_source": target_source,
            "snapshot_artifact": str(snapshot_artifact),
            **workspace_evidence,
        },
        "input_artifacts": _run_input_artifacts(target_kind, target),
        "runtime_contract": {
            "mode": mode,
            "run_mode_composition": {**composition.manifest(), "composition_hash": composition.composition_hash},
            **({"execution_runtime_plan": {
                **execution_plan.manifest(), "plan_hash": execution_plan.plan_hash,
            }} if execution_plan else {}),
            **({"strategy_runtime_plan": {
                **strategy_plan.manifest(), "plan_hash": strategy_plan.plan_hash,
            }} if strategy_plan else {}),
            **({"feed_bindings": list(live_bindings)} if live_bindings else {}),
            **({"feed_runtime_plan": {**feed_plan.manifest(), "plan_hash": feed_plan.plan_hash}} if feed_plan else {}),
            **({"feed_runtime_bundle": {
                **feed_plan.service_bundle_manifest(), "bundle_hash": feed_plan.service_bundle_hash,
            }} if feed_plan else {}),
            **({"feed_runtime_execution": feed_runtime_execution} if feed_runtime_execution else {}),
            **({"strategy_decision_execution": strategy_decision_execution} if strategy_decision_execution else {}),
            **({"freshness_gates": list(freshness_gates)} if freshness_gates else {}),
        },
        "started_at": material["at"],
        "outputs": {
            **({"strategy_decision": strategy_decision_execution["artifact"]} if strategy_decision_execution else {}),
        },
    }
    _write_json(snapshot_artifact, target)
    _write_json(directory / "manifest.json", manifest)
    _write_json(directory / "reports" / "summary.json", {
        "run_id": run_id,
        "passed": True,
        "target_hash": target_hash,
        **({"feed_runtime_execution": feed_runtime_execution} if feed_runtime_execution else {}),
        **({"strategy_decision_execution": strategy_decision_execution} if strategy_decision_execution else {}),
    })
    return {**manifest, "workspace": str(directory), "manifest": str(directory / "manifest.json")}



def _run_mode_composition(mode: str):
    if mode == "study":
        return study_composition()
    if mode == "backtest":
        return backtest_composition()
    if mode == "historical-simulation":
        return historical_simulation_composition()
    if mode == "paper":
        return paper_trading_composition("binance")
    if mode == "live":
        return live_composition("binance", "binance-live")
    raise ValueError(f"unsupported run mode: {mode}")


def _execute_feed_runtime(root: Path, run_directory: Path, mode: str, feed_plan, execution_plan, strategy_plan, strategy_target, args) -> dict[str, object]:
    seconds = float(getattr(args, "feed_runtime_seconds", 0.0) or 0.0)
    if seconds < 0:
        raise ValueError("feed runtime seconds cannot be negative")
    feed_runtime = None
    if feed_plan is not None and getattr(args, "execute_feeds", False):
        factory = getattr(args, "feed_runtime_factory", None)
        if factory is None:
            from kairos.connectors.binance import BinanceRuntimeFeedFactory

            factory = BinanceRuntimeFeedFactory(
                root,
                journal_root=run_directory / "feeds" / "binance",
            )
        feed_runtime = factory(feed_plan) if callable(factory) and not hasattr(factory, "build") else factory.build(feed_plan)

    async def run() -> tuple[object, ...]:
        paths = RuntimePaths.under(run_directory / "runtime")
        runtime_store = SQLiteRuntimeStore(paths.runtime_database)
        app = KairosApplication(
            ApplicationConfig(_runtime_environment(mode), paths),
            runtime_store,
            runtime_id=f"run-feed-runtime:{run_directory.name}",
        )
        tasks = tuple(feed_runtime.managed_services) if feed_runtime is not None else ()
        intent_bridge = _paper_intent_bridge(run_directory, mode, runtime_store) if (
            strategy_plan is not None and getattr(args, "execute_strategy", False)
        ) else None
        if execution_plan is not None:
            tasks += execution_plan.managed_services(_execution_gateway_runner_factory(run_directory, mode, intent_bridge))
        strategy_bindings = {}
        if strategy_plan is not None and getattr(args, "execute_strategy", False):
            strategy_runner_factory, strategy_bindings = _strategy_runner_factory(
                args, strategy_target, feed_runtime, run_directory, mode, intent_bridge,
            )
            tasks += strategy_plan.managed_services(strategy_runner_factory)
        runtime = AsyncKairosRuntime(app, tasks)
        await runtime.start()
        try:
            await asyncio.sleep(seconds)
            return runtime.service_snapshots(), strategy_bindings, intent_bridge
        finally:
            await runtime.stop()

    snapshots, strategy_bindings, intent_bridge = asyncio.run(run())
    capture_releases = _register_feed_capture_releases(root, run_directory, feed_plan, feed_runtime) if feed_runtime is not None else []
    return {
        "executed": True,
        "provider": "binance",
        "duration_seconds": seconds,
        **({"bundle_hash": feed_runtime.runtime_bundle.bundle_hash} if feed_runtime is not None else {}),
        **({"execution_plan_hash": execution_plan.plan_hash} if execution_plan else {}),
        **({"strategy_plan_hash": strategy_plan.plan_hash} if strategy_plan else {}),
        "services": [
            {
                "name": item.name,
                "criticality": item.criticality.value,
                "status": item.status.value,
                "attempts": item.attempts,
                "restart_count": item.restart_count,
                **({"last_fault": {
                    "task_name": item.last_fault.task_name,
                    "error_type": item.last_fault.error_type,
                    "message": item.last_fault.message,
                    "attempt": item.last_fault.attempt,
                    "occurred_at": item.last_fault.occurred_at.isoformat(),
                }} if item.last_fault else {}),
            }
            for item in snapshots
        ],
        "manifest_paths": {
            service_id: str(path)
            for service_id, path in getattr(feed_runtime, "manifest_paths", {}).items()
        },
        "strategy_bindings": {
            key: value.manifest() for key, value in strategy_bindings.items()
        },
        **({"intent_execution_bridge": intent_bridge.manifest()} if intent_bridge is not None else {}),
        "capture_releases": capture_releases,
    }


def _paper_intent_bridge(run_directory: Path, mode: str, runtime_store: SQLiteRuntimeStore):
    if mode != "paper":
        return None
    from kairos.application.strategy_runtime import PaperIntentExecutionBridge
    from kairos.domain.identity import AccountKey, AccountType, InstitutionId

    account = AccountKey(InstitutionId("simulated"), f"{run_directory.name}-paper", AccountType.CRYPTO_SPOT)
    return PaperIntentExecutionBridge(
        account=account,
        output_path=run_directory / "execution" / "paper-intent-bridge.json",
        approved_capital=Decimal("100000"),
        runtime_store=runtime_store,
    )


def _execute_strategy_model_decision(
    root: Path,
    run_directory: Path,
    strategy_lock: dict[str, Any],
    mode: str,
) -> dict[str, object]:
    if mode not in {"backtest", "historical-simulation"}:
        raise ValueError("user model.py decision execution is only supported for backtest or historical-simulation")
    model = strategy_lock.get("model")
    if not isinstance(model, dict) or model.get("kind") != "user-model-code":
        raise ValueError("strategy decision execution requires a user-model-code Strategy Lock")
    if model.get("side_effects_allowed") is not False:
        raise ValueError("strategy decision execution requires side_effects_allowed=false")
    model_path_value = model.get("artifact_path") or model.get("path")
    if not model_path_value:
        raise ValueError("Strategy Lock user model is missing artifact_path")
    model_path = Path(str(model_path_value))
    if not model_path.exists():
        raise FileNotFoundError(model_path)
    module_name = f"kairos_user_strategy_{sha256(str(model_path).encode()).hexdigest()[:12]}"
    module = _load_user_module(model_path, module_name)
    decide = getattr(module, "decide", None)
    if not callable(decide):
        raise ValueError("user strategy model.py must define decide(context)")
    context = StrategyDecisionContext(root, strategy_lock)
    try:
        decision = _json_safe(decide(context))
    except Exception as error:
        diagnostic = _run_data_plane_diagnostic(strategy_lock, mode, error)
        diagnostic_path = run_directory / "diagnostics" / "data_plane.json"
        _write_json(diagnostic_path, diagnostic)
        raise ValueError(
            f"strategy model decision failed; data plane diagnostic: {diagnostic_path}"
        ) from error
    payload = {
        "kind": "strategy.model_decision",
        "schema_version": 1,
        "strategy_id": strategy_lock.get("strategy_id"),
        "mode": mode,
        "model_code_hash": model.get("model_code_hash") or model.get("code_hash"),
        "model_contract_hash": model.get("model_contract_hash"),
        "context": context.manifest(),
        "decision": decision,
        "decided_at": _now(),
    }
    decision_hash = _stable_hash(payload)
    artifact = run_directory / "strategy" / "decision.json"
    _write_json(artifact, {**payload, "decision_hash": decision_hash})
    return {
        "executed": True,
        "kind": "strategy.model_decision",
        "artifact": str(artifact),
        "decision_hash": decision_hash,
        "model_code_hash": str(payload.get("model_code_hash") or ""),
        "model_contract_hash": str(payload.get("model_contract_hash") or ""),
    }


def _run_data_plane_diagnostic(strategy_lock: dict[str, Any], mode: str, error: Exception) -> dict[str, object]:
    message = str(error)
    issue = "strategy_model_error"
    next_action = "Inspect the strategy model error and rerun after fixing the declared input contract."
    if "factor contract without materialized InputTable" in message:
        issue = "factor_runtime_missing_input"
        next_action = "Run kairos study publish-factor for the referenced factor and refreeze the Study/Strategy."
    elif "no dataset-readable data files" in message or isinstance(error, FileNotFoundError):
        issue = "missing_history_release"
        next_action = "Run kairos data download or republish the Data Release so DatasetClient can materialize it."
    elif "not declared" in message:
        issue = "missing_dataset_fields"
        next_action = "Declare the input in Strategy model metadata or bind the missing Data/Factor input before freeze."
    checks = []
    for name, item in sorted(strategy_lock.get("data", {}).items()):
        checks.append({
            "name": name,
            "kind": "data",
            "dataset": item.get("dataset"),
            "release_id": item.get("release_id"),
            "materialized": bool(item.get("release_id")),
        })
    for name, item in sorted(strategy_lock.get("inputs", {}).items()):
        checks.append({
            "name": name,
            "kind": item.get("kind", "input"),
            "dataset": item.get("dataset"),
            "release_id": item.get("release_id"),
            "materialization_status": item.get("materialization_status", "unknown"),
            "materialized": bool(item.get("release_id") and item.get("dataset")),
        })
    return {
        "kind": "run.data_plane_diagnostic",
        "schema_version": 1,
        "strategy_id": strategy_lock.get("strategy_id"),
        "mode": mode,
        "passed": False,
        "issue": issue,
        "error_type": type(error).__name__,
        "message": message,
        "next_action": next_action,
        "inputs": checks,
        "diagnosed_at": _now(),
    }


def _execution_gateway_runner_factory(run_directory: Path, mode: str, intent_bridge=None):
    def factory(service):
        if service.execution_driver != "simulated":
            async def unbound() -> None:
                raise RuntimeError(f"execution driver {service.execution_driver!r} has no runtime gateway bound")

            return unbound

        async def run() -> None:
            from kairos.connectors.simulated import SimulatedExecutionAccountGateway
            from kairos.domain.identity import AccountKey, AccountType, InstitutionId, VenueId

            environment = _runtime_environment(mode)
            account = AccountKey(InstitutionId("simulated"), f"{run_directory.name}-paper", AccountType.CRYPTO_SPOT)
            gateway = SimulatedExecutionAccountGateway(VenueId("simulated"), account, environment=environment)
            if intent_bridge is not None:
                await intent_bridge.run_gateway(gateway)
                await asyncio.Event().wait()
            while True:
                gateway.account_state(account)
                await asyncio.sleep(0.01)

        return run

    return factory


def _strategy_runner_factory(args, strategy_target: dict[str, object], feed_runtime: object | None, run_directory: Path, mode: str, intent_bridge=None):
    factory = getattr(args, "strategy_runtime_factory", None)
    if factory is not None:
        return factory, {}
    from kairos.application.strategy_runtime import strategy_runtime_runner_from_lock

    return strategy_runtime_runner_from_lock(strategy_target, feed_runtime, run_directory, mode, intent_bridge)


def _register_feed_capture_releases(root: Path, run_directory: Path, feed_plan, feed_runtime) -> list[dict[str, object]]:
    results = []
    for service in feed_plan.services:
        capture_manifests = sorted((run_directory / "feeds").glob(f"**/{_safe_glob_part(service.service_id)}*.rotation.manifest.json"))
        if not capture_manifests:
            capture_manifests = sorted((run_directory / "feeds").glob("**/*.rotation.manifest.json"))
        if not capture_manifests:
            continue
        release = register_live_capture_release(
            root,
            dataset_id=service.dataset,
            capture_manifest_path=capture_manifests[-1],
            run_id=run_directory.name,
            live_view_id=service.live_view_id,
            provider="binance",
        )
        manifest_path = root / release.relative_path / "data_release_manifest.json"
        manifest = _read_json(manifest_path)
        results.append({
            "service_id": service.service_id,
            "dataset": service.dataset,
            "release_id": release.release_id,
            "content_hash": release.content_hash,
            "artifact_ref": manifest["artifact_ref"],
            "data_release_manifest_hash": stable_artifact_hash(manifest),
            "capture_manifest": str(capture_manifests[-1]),
        })
    return results


def _safe_glob_part(value: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_"} else "-" for character in value)


def _runtime_environment(mode: str) -> Environment:
    if mode == "live":
        return Environment.LIVE
    return Environment.PAPER


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
    schema_path = release_dir / "schema.json"
    schema = _read_json(schema_path) if schema_path.exists() else {}
    try:
        spec = catalog.product_spec(release.product_key)
        contract_hash = DataSetContractArtifact.from_product_contract(spec).contract_hash
        default_primary_time = spec.product.primary_time
    except KeyError:
        contract_hash = ""
        default_primary_time = catalog.product(release.product_key).primary_time
    contract_hash = str(manifest.get("contract_hash") or contract_hash)
    manifest_hash = stable_artifact_hash(manifest) if manifest else ""
    fields = _release_schema_fields(manifest, schema)
    primary_time = str(manifest.get("primary_time") or schema.get("primary_time") or default_primary_time or "")
    return {
        "dataset": str(release.product_key),
        "release_id": release.release_id,
        "content_hash": release.content_hash,
        "contract_hash": contract_hash,
        "manifest_hash": manifest_hash,
        "primary_time": primary_time,
        "fields": fields,
        "artifact_ref": data_release_ref(str(release.product_key), release.release_id),
        "quality_level": release.quality_level.value,
    }


def _release_schema_fields(manifest: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    raw_fields = manifest.get("fields")
    if not raw_fields:
        raw_fields = schema.get("fields") or schema.get("columns")
    if isinstance(raw_fields, dict):
        return [str(name) for name in raw_fields]
    if isinstance(raw_fields, list):
        return [str(item.get("name") if isinstance(item, dict) else item) for item in raw_fields]
    return []


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
                "factor_contract_hash": item.get("factor_contract_hash"),
                "parameters_hash": item.get("parameters_hash"),
                "primary_time": item.get("primary_time"),
                "point_in_time": item.get("point_in_time"),
                "output_schema": item.get("output_schema"),
                "runtime": item.get("runtime"),
                "factor_run_hash": item.get("factor_run_hash"),
                "factor_output_hash": item.get("factor_output_hash"),
                "metadata_status": item.get("metadata_status"),
                "strategy_eligible": item.get("strategy_eligible"),
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
    published_factors = study_lock.get("published_factors", {})
    for name, input_spec in strategy.get("inputs", {}).items():
        factor_name = input_spec.get("from_study_factor")
        if factor_name not in factors:
            raise ValueError(f"strategy input {name!r} references unknown Study factor {factor_name!r}")
        factor = factors[factor_name]
        if input_spec.get("source_hash") != factor.get("code_hash"):
            raise ValueError(f"strategy input {name!r} factor hash does not match the Frozen Study Lock")
        for key in ("factor_contract_hash", "parameters_hash"):
            if factor.get(key) is not None and input_spec.get(key) != factor.get(key):
                raise ValueError(f"strategy input {name!r} {key} does not match the Frozen Study Lock")
        if factor.get("metadata_status") == "declared" and input_spec.get("strategy_eligible") is not True:
            raise ValueError(f"strategy input {name!r} factor is not strategy eligible")
        published = published_factors.get(factor_name)
        if isinstance(published, dict) and input_spec.get("materialization_status") == "published_feature":
            for key in ("factor_run_hash", "factor_output_hash"):
                if published.get(key) is not None and input_spec.get(key) != published.get(key):
                    raise ValueError(f"strategy input {name!r} {key} does not match the published Feature evidence")


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
            "factor_contract_hash": item.get("factor_contract_hash"),
            "parameters_hash": item.get("parameters_hash"),
            "primary_time": item.get("primary_time"),
            "point_in_time": item.get("point_in_time"),
            "output_schema": item.get("output_schema"),
            "runtime": item.get("runtime"),
            "materialization_status": item.get("materialization_status"),
            "dataset": item.get("dataset"),
            "release_id": item.get("release_id"),
            "content_hash": item.get("content_hash"),
            "contract_hash": item.get("contract_hash"),
            "manifest_hash": item.get("manifest_hash"),
            "quality_level": item.get("quality_level"),
            "factor_run_hash": item.get("factor_run_hash"),
            "factor_output_hash": item.get("factor_output_hash"),
            "feature_artifact_ref": item.get("feature_artifact_ref"),
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
        sources=(SourceBinding("user-write", None, 100, QualityLevel.STUDY, ("file",)),),
        owner="user",
    )
    spec = DataProductContract(
        product,
        str(directory.parent.relative_to(root)),
        f"{dataset_id}.contract",
        storage_kind=DatasetStorageKind.TABULAR,
        quality_profile="contract",
        minimum_publication_level=QualityLevel.STUDY,
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
        DatasetStatus.APPROVED_FOR_STUDY,
        QualityLevel.STUDY,
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


def _read_policy_contract(path: str | Path, policy_kind: str) -> dict[str, Any]:
    payload = _read_contract(path)
    kind = payload.get("kind")
    if kind not in (None, f"strategy.{policy_kind}", f"{policy_kind}.policy", f"{policy_kind}_policy"):
        raise ValueError(f"{policy_kind} policy kind is not supported: {kind!r}")
    body = payload.get(policy_kind) if isinstance(payload.get(policy_kind), dict) else payload
    if not isinstance(body, dict) or not body:
        raise ValueError(f"{policy_kind} policy must be a non-empty object")
    _assert_json_serializable(body, f"{policy_kind} policy")
    if policy_kind == "execution":
        missing = [key for key in ("decision_time", "execution_time", "order_style") if not body.get(key)]
        if missing:
            raise ValueError(f"execution policy must declare: {', '.join(missing)}")
    return _json_safe(body)


def _strategy_model_contract(
    strategy: dict[str, Any],
    metadata_path: str | Path | None,
    workspace_dir: Path,
) -> dict[str, object]:
    if metadata_path is None:
        return {
            "metadata_status": "missing",
            "model_contract_hash": None,
        }
    source = _resolve_user_file(metadata_path, workspace_dir)
    metadata = _read_contract(source)
    inputs = _strategy_model_inputs(metadata)
    available = set(strategy.get("inputs", {})) | set(strategy.get("data", {}))
    unknown = sorted(set(inputs) - available)
    if unknown:
        raise ValueError(f"strategy model metadata references undeclared inputs: {', '.join(unknown)}")
    input_contracts = _strategy_model_input_contracts(metadata)
    _validate_strategy_model_input_contracts(strategy, input_contracts)
    intent_schema = metadata.get("intent_schema") or metadata.get("outputs") or {}
    if not isinstance(intent_schema, dict) or not intent_schema:
        raise ValueError("strategy model metadata must declare intent_schema")
    side_effects = bool(metadata.get("side_effects_allowed", False))
    if side_effects:
        raise ValueError("strategy model metadata must declare side_effects_allowed=false")
    contract = {
        "inputs": inputs,
        "input_contracts": input_contracts,
        "intent_schema": _json_safe(intent_schema),
        "side_effects_allowed": False,
        "parameters": _json_safe(metadata.get("parameters", {})),
    }
    _assert_json_serializable(contract, "strategy model metadata contract")
    return {
        "metadata_status": "declared",
        "metadata_path": str(source),
        "metadata": contract,
        "inputs": inputs,
        "input_contracts": input_contracts,
        "intent_schema": contract["intent_schema"],
        "side_effects_allowed": False,
        "parameters": contract["parameters"],
        "model_contract_hash": _stable_hash(contract),
    }


def _strategy_model_inputs(metadata: dict[str, Any]) -> list[str]:
    raw_inputs = metadata.get("inputs")
    if isinstance(raw_inputs, dict):
        inputs = [str(key) for key in raw_inputs]
    elif isinstance(raw_inputs, list):
        inputs = [str(item) for item in raw_inputs]
    else:
        raise ValueError("strategy model metadata must declare inputs as an object or list")
    if not inputs:
        raise ValueError("strategy model metadata inputs must not be empty")
    return sorted(inputs)


def _strategy_model_input_contracts(metadata: dict[str, Any]) -> dict[str, object]:
    raw_inputs = metadata.get("inputs")
    if not isinstance(raw_inputs, dict):
        return {}
    contracts: dict[str, object] = {}
    for name, value in sorted(raw_inputs.items()):
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ValueError(f"strategy model input {name!r} contract must be an object")
        contract: dict[str, object] = {}
        raw_fields = value.get("fields") or value.get("required_fields")
        if raw_fields is not None:
            if not isinstance(raw_fields, list) or not raw_fields:
                raise ValueError(f"strategy model input {name!r} fields must be a non-empty list")
            contract["fields"] = [str(item.get("name") if isinstance(item, dict) else item) for item in raw_fields]
        primary_time = value.get("primary_time")
        if not primary_time and isinstance(value.get("time"), dict):
            primary_time = value["time"].get("primary_time")
        if primary_time:
            contract["primary_time"] = str(primary_time)
        if contract:
            contracts[str(name)] = contract
    return contracts


def _validate_strategy_model_input_contracts(strategy: dict[str, Any], input_contracts: dict[str, object]) -> None:
    if not input_contracts:
        return
    available: dict[str, Any] = {
        **{str(name): value for name, value in strategy.get("data", {}).items() if isinstance(value, dict)},
        **{str(name): value for name, value in strategy.get("inputs", {}).items() if isinstance(value, dict)},
    }
    for name, contract in sorted(input_contracts.items()):
        if not isinstance(contract, dict):
            continue
        source = available.get(name)
        if not isinstance(source, dict):
            raise ValueError(f"strategy model input {name!r} is not bound")
        expected_fields = [str(item) for item in contract.get("fields", [])]
        if expected_fields:
            fields = _strategy_input_fields(source)
            if not fields:
                raise ValueError(f"strategy model input {name!r} has no declared fields to validate")
            missing = sorted(set(expected_fields) - set(fields))
            if missing:
                raise ValueError(f"strategy model input {name!r} is missing fields: {', '.join(missing)}")
        expected_primary_time = contract.get("primary_time")
        if expected_primary_time:
            primary_time = str(source.get("primary_time") or source.get("output_schema", {}).get("primary_time") or "")
            if not primary_time:
                raise ValueError(f"strategy model input {name!r} has no declared primary_time to validate")
            if str(expected_primary_time) != primary_time:
                raise ValueError(
                    f"strategy model input {name!r} primary_time mismatch: "
                    f"expected {expected_primary_time!r}, got {primary_time!r}"
                )


def _strategy_input_fields(source: dict[str, Any]) -> list[str]:
    raw_fields = source.get("fields")
    if raw_fields is None and isinstance(source.get("output_schema"), dict):
        raw_fields = source["output_schema"].get("fields")
    return [str(item.get("name") if isinstance(item, dict) else item) for item in raw_fields or []]


def _validate_download_key(key: str) -> str:
    value = str(key)
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"invalid data download key {key!r}")
    return value


def _validate_provider_name(name: str) -> str:
    value = str(name)
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"invalid data provider name {name!r}")
    return value


def _registered_download_spec_path(root: Path, key: str) -> Path:
    return root / "data-products" / "downloads" / _validate_download_key(key) / "download-spec.json"


def _registered_provider_spec_path(root: Path, name: str) -> Path:
    return root / "data-products" / "providers" / _validate_provider_name(name) / "provider-spec.json"


def _read_registered_provider(root: Path, name: str) -> dict[str, Any]:
    path = _registered_provider_spec_path(root, name)
    if not path.exists():
        providers_root = root / "data-products" / "providers"
        known = sorted(item.name for item in providers_root.iterdir() if item.is_dir()) if providers_root.exists() else []
        raise ValueError(f"unknown data provider {name!r}; registered providers: {', '.join(known) or '-'}")
    payload = _read_json(path)
    spec = payload.get("spec")
    if not isinstance(spec, dict):
        raise ValueError(f"registered provider spec is invalid: {path}")
    registered_from = payload.get("registered_from")
    if not registered_from:
        raise ValueError(f"registered provider spec is missing registered_from: {path}")
    return {
        "name": str(payload.get("name") or name),
        "spec": spec,
        "spec_hash": str(payload.get("spec_hash") or _stable_hash(spec)),
        "registered_from": str(registered_from),
    }


def _read_registered_download(root: Path, key: str) -> dict[str, Any]:
    path = _registered_download_spec_path(root, key)
    if not path.exists():
        known = sorted(BUILTIN_DOWNLOAD_KEYS)
        registered_root = root / "data-products" / "downloads"
        if registered_root.exists():
            known.extend(sorted(item.name for item in registered_root.iterdir() if item.is_dir()))
        raise ValueError(f"unknown data download key {key!r}; registered keys: {', '.join(known)}")
    payload = _read_json(path)
    spec = payload.get("spec")
    if not isinstance(spec, dict):
        raise ValueError(f"registered download spec is invalid: {path}")
    registered_from = payload.get("registered_from")
    if not registered_from:
        raise ValueError(f"registered download spec is missing registered_from: {path}")
    return {
        "key": str(payload.get("key") or key),
        "spec": spec,
        "spec_hash": str(payload.get("spec_hash") or _stable_hash(spec)),
        "registered_from": str(registered_from),
    }


def _resolve_user_file(value: str | Path, base: Path) -> Path:
    path = Path(value)
    if not path.is_absolute() and not path.exists():
        path = base / path
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _resolve_download_spec_file(value: str | Path, base: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _study_dir(root: str | Path, study_id: str) -> Path:
    return ProductPaths(Path(root)).studies / study_id


def _study_file(root: str | Path, study_id: str) -> Path:
    return _study_dir(root, study_id) / "study.json"


def _study_workspace_file(root: str | Path, study_id: str) -> Path:
    return _study_dir(root, study_id) / "workspace.json"


def _study_lifecycle_file(root: str | Path, study_id: str) -> Path:
    return _study_dir(root, study_id) / "lifecycle.jsonl"


def _write_study_state(root: str | Path, study_id: str, study: dict[str, Any]) -> None:
    _write_json(_study_file(root, study_id), study)
    manifest = {
        "product": "study",
        "kind": "study.workspace_manifest",
        "schema_version": 1,
        "study_id": study_id,
        "status": study.get("status"),
        "lifecycle": study.get("lifecycle", "DRAFT"),
        "data_count": len(study.get("data", {})),
        "factor_count": len(study.get("factors", {})),
        "published_factor_count": len(study.get("published_factors", {})),
        "latest_lock": study.get("latest_lock"),
        "latest_lock_hash": study.get("latest_lock_hash"),
        "updated_at": _now(),
    }
    _write_json(_study_workspace_file(root, study_id), manifest)


def _append_study_lifecycle(
    root: str | Path,
    study_id: str,
    event: str,
    study: dict[str, Any],
    extra: dict[str, object] | None = None,
) -> None:
    path = _study_lifecycle_file(root, study_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "product": "study",
        "kind": "study.lifecycle_event",
        "schema_version": 1,
        "study_id": study_id,
        "event": event,
        "status": study.get("status"),
        "lifecycle": study.get("lifecycle", "DRAFT"),
        "at": _now(),
        **(extra or {}),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _strategy_dir(root: str | Path, strategy_id: str) -> Path:
    return ProductPaths(Path(root)).strategies / strategy_id


def _strategy_file(root: str | Path, strategy_id: str) -> Path:
    return _strategy_dir(root, strategy_id) / "strategy.json"


def _load_study(root: str | Path, study_id: str) -> dict[str, Any]:
    return _read_json(_study_file(root, study_id))


def _load_strategy(root: str | Path, strategy_id: str) -> dict[str, Any]:
    return _read_json(_strategy_file(root, strategy_id))


def _study_material(root: str | Path, study_id: str, version: str | None = None) -> dict[str, Any]:
    if version is None:
        return _load_study(root, study_id)
    return _load_study_lock(root, study_id, version)


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


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


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
