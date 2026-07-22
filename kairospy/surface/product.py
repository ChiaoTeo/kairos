from __future__ import annotations

import asyncio
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import importlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace
from typing import Any, Iterable

from kairospy.runtime import paper_trading_composition, runtime_execution_plan, runtime_feed_plan
from kairospy.data import (
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
    resolve_live_dataset_subscription,
    stable_artifact_hash,
    write_live_view_manifest,
)
from kairospy.research.capture.tutorial_data import ensure_sma_tutorial_dataset


BUILTIN_DOWNLOAD_KEYS = {
    "tutorial-sma-data": "fixture:sma-bars-v1",
    "sma-tutorial-data": "fixture:sma-bars-v1",
}


class InputTableRef(dict):
    def __init__(self, root: str | Path, evidence: dict[str, object]) -> None:
        super().__init__(evidence)
        self._root = Path(root)

    def arrow(self, *, columns: tuple[str, ...] | list[str] | None = None):
        from kairospy.data import OutputFormat
        return self._query(columns).collect(OutputFormat.ARROW)

    def pandas(self, *, columns: tuple[str, ...] | list[str] | None = None):
        from kairospy.data import OutputFormat
        return self._query(columns).collect(OutputFormat.PANDAS)

    def rows(self, *, columns: tuple[str, ...] | list[str] | None = None) -> list[dict[str, object]]:
        from kairospy.data import OutputFormat
        return self._query(columns).collect(OutputFormat.ROWS)

    def _query(self, columns):
        from kairospy.data import DatasetClient
        release_id = str(self["release_id"])
        return DatasetClient(self._root).get(release_id, fields=tuple(columns) if columns is not None else None)


class DataAddInputError(ValueError):
    def __init__(self, code: str, message: str, *, source: str | Path, why: str, next_command: str) -> None:
        self.code = code
        self.source = Path(source)
        self.why = why
        self.next_command = next_command
        super().__init__(message)

    def to_payload(self, *, dataset_id: str) -> dict[str, object]:
        return {
            "product": "data",
            "operation": "add",
            "dataset": dataset_id,
            "status": "needs_input",
            "source": str(self.source),
            "issues": [{
                "code": self.code,
                "message": str(self),
                "why": self.why,
            }],
            "next_command": self.next_command,
        }


class DataProductNotFoundError(KeyError):
    def __init__(
        self,
        key: str,
        *,
        operation: str = "use",
        known_keys: tuple[str, ...],
        aliases: dict[str, str] | None = None,
    ) -> None:
        self.key = key
        self.operation = operation
        self.known_keys = known_keys
        self.aliases = aliases or {}
        super().__init__(f"unknown built-in data product: {key}")

    def to_payload(self) -> dict[str, object]:
        return {
            "product": "data",
            "operation": self.operation,
            "status": "needs_input",
            "key": self.key,
            "issues": [{
                "code": "unknown_built_in_product",
                "message": f"Unknown built-in Data product: {self.key}",
                "why": "This command requires one of the registered built-in Data product keys or aliases.",
            }],
            "known_keys": list(self.known_keys),
            "aliases": dict(sorted(self.aliases.items())),
            "next_command": (
                "kairospy data use --list-products"
                if self.operation == "use"
                else "kairospy data product list"
            ),
        }


class DataLiveDatasetNotConfiguredError(KeyError):
    def __init__(self, dataset_id: str) -> None:
        self.dataset_id = dataset_id
        super().__init__(f"Dataset {dataset_id!r} has no configured live view to reconnect")

    def to_payload(self) -> dict[str, object]:
        return {
            "product": "data",
            "operation": "reconnect",
            "dataset": self.dataset_id,
            "status": "needs_input",
            "issues": [{
                "code": "live_not_configured",
                "message": f"Dataset {self.dataset_id} has no configured live source.",
                "why": "Reconnect can only reuse a Dataset that was previously connected as live data.",
            }],
            "next_command": (
                "kairospy data connect <source> --as "
                f"{self.dataset_id} --instrument <symbol>"
            ),
        }


class DataDatasetInputError(KeyError):
    def __init__(
        self,
        operation: str,
        dataset_id: str,
        *,
        code: str,
        status: str,
        message: str,
        why: str,
        next_command: str,
    ) -> None:
        self.operation = operation
        self.dataset_id = dataset_id
        self.code = code
        self.status = status
        self.message = message
        self.why = why
        self.next_command = next_command
        super().__init__(message)

    def to_payload(self) -> dict[str, object]:
        return {
            "product": "data",
            "operation": self.operation,
            "dataset": self.dataset_id,
            "status": self.status,
            "issues": [{
                "code": self.code,
                "message": self.message,
                "why": self.why,
            }],
            "next_command": self.next_command,
        }


def _dataset_not_found_error(operation: str, dataset_id: str) -> DataDatasetInputError:
    return DataDatasetInputError(
        operation,
        dataset_id,
        code="dataset_not_found",
        status="needs_input",
        message=f"Dataset {dataset_id} is not registered.",
        why="This command needs an existing Dataset name. Add user data, use a built-in product, or connect a live source first.",
        next_command="kairospy data start",
    )


def _historical_not_configured_error(operation: str, dataset_id: str) -> DataDatasetInputError:
    return DataDatasetInputError(
        operation,
        dataset_id,
        code="historical_not_configured",
        status="needs_data",
        message=f"Dataset {dataset_id} has no historical data release.",
        why="This command needs historical data. A live-only Dataset can be monitored or reconnected, but cannot be queried, validated, or promoted for backtest.",
        next_command=f"kairospy data add <file> --name {dataset_id}",
    )


@dataclass(frozen=True, slots=True)
class Data:
    """Data product entrypoint for setup, readiness checks, and dataset consumption."""

    root: str | Path = "data"

    def reader(self, *, run_mode: object = "workspace", **kwargs: object):
        from kairospy.data import DatasetClient

        return DatasetClient(self.root, run_mode=run_mode, **kwargs)

    def products(self) -> dict[str, object]:
        return data_product_list(_args(self.root))

    def list(self, *, dimensions: dict[str, str] | None = None) -> dict[str, object]:
        return data_list(_args(self.root, dimension=dimensions or {}))

    def apply(self, manifest: str | Path = "kairos.data.toml", *, only: str | None = None,
              dry_run: bool = False) -> dict[str, object]:
        return data_apply(_args(self.root, manifest=Path(manifest), only=only, dry_run=dry_run))

    def start(self, *, dry_run: bool = False, kind: str | None = None, source: str | Path | None = None,
              product: str | None = None, name: str | None = None,
              as_dataset: str | None = None, time: str | None = None,
              start: str | None = None, end: str | None = None,
              account: str | None = None, channel: str | None = None,
              instruments: tuple[str, ...] = (), for_use: str = "workspace") -> dict[str, object]:
        return data_start(_args(
            self.root,
            dry_run=dry_run,
            kind=kind,
            source=Path(source) if source is not None else None,
            product=product,
            name=name,
            as_dataset=as_dataset,
            time=time,
            start_time=start,
            end_time=end,
            account=account,
            channel=channel,
            instrument=list(instruments),
            for_use=for_use,
        ))

    def add(self, source: str | Path, *, name: str, time: str | None = None,
            protocol: str | None = None, start: str | None = None, end: str | None = None,
            instruments: tuple[str, ...] = ()) -> dict[str, object]:
        return data_add(_args(
            self.root,
            source=Path(source),
            name=name,
            time=time,
            protocol=protocol,
            start=start,
            end=end,
            instrument=list(instruments),
        ))

    def use(self, key: str, *, as_dataset: str | None = None, start: str | None = None,
            end: str | None = None, instruments: tuple[str, ...] = (),
            provider: str | None = None, venue: str | None = None,
            refresh: bool = False, dry_run: bool = False, for_use: str = "workspace") -> dict[str, object]:
        return data_use(_args(
            self.root,
            key=key,
            as_dataset=as_dataset,
            start=start,
            end=end,
            dry_run=dry_run,
            list_products=False,
            provider=provider,
            venue=venue,
            instrument=list(instruments),
            refresh=refresh,
            for_use=for_use,
        ))

    def connect(self, source: str | Path, *, as_dataset: str, time: str = "timestamp",
                account: str | None = None, channel: str | None = None,
                instruments: tuple[str, ...] = (), freshness_seconds: float = 5.0,
                for_use: str = "shadow", market: str = "spot", levels: int | None = None,
                interval: str | None = None) -> dict[str, object]:
        return data_connect(_args(
            self.root,
            source=Path(source),
            as_dataset=as_dataset,
            time=time,
            account=account,
            channel=channel,
            instrument=list(instruments),
            freshness_seconds=freshness_seconds,
            for_use=for_use,
            market=market,
            levels=levels,
            interval=interval,
        ))

    def sample(self, source: str, *, as_dataset: str | None = None, channel: str | None = None,
               instruments: tuple[str, ...] = (), limit: int = 5, connector: object | None = None,
               environment: object | None = None, market: str = "spot", levels: int | None = None,
               interval: str | None = None) -> dict[str, object]:
        return data_sample(_args(
            self.root,
            source=source,
            as_dataset=as_dataset,
            channel=channel,
            instrument=list(instruments),
            limit=limit,
            connector=connector,
            environment=environment,
            market=market,
            levels=levels,
            interval=interval,
        ))

    def reconnect(self, dataset: str, *, account: str | None = None,
                  channel: str | None = None, instruments: tuple[str, ...] = (),
                  freshness_seconds: float | None = None, market: str | None = None,
                  levels: int | None = None, interval: str | None = None) -> dict[str, object]:
        return data_reconnect(_args(
            self.root,
            dataset=dataset,
            account=account,
            channel=channel,
            instrument=list(instruments),
            freshness_seconds=freshness_seconds,
            market=market,
            levels=levels,
            interval=interval,
        ))

    def doctor(self, dataset: str) -> dict[str, object]:
        return data_doctor(_args(self.root, dataset=dataset))

    def metadata(self, dataset: str, *, time: str | None = None) -> dict[str, object]:
        return data_metadata(_args(self.root, dataset=dataset, time=time))

    def replay(self, dataset: str, *, start: str | None = None, end: str | None = None,
               fields: tuple[str, ...] = (), instruments: tuple[str, ...] = (),
               limit: int = 20) -> dict[str, object]:
        return data_replay(_args(
            self.root,
            dataset=dataset,
            start=start,
            end=end,
            field=list(fields),
            instrument=list(instruments),
            limit=limit,
        ))

    def protocol(self, action: str = "list", *, kind: str | None = None,
                 source: str | Path | None = None, output: str | Path | None = None,
                 name: str = "workspace.protocol_check", start: str | None = None,
                 end: str | None = None, instruments: tuple[str, ...] = (),
                 account: str | None = None, channel: str | None = None) -> dict[str, object]:
        return data_protocol(_args(
            self.root,
            protocol_action=action,
            kind=kind,
            source=Path(source) if source is not None else None,
            output=Path(output) if output is not None else None,
            name=name,
            start=start,
            end=end,
            instrument=list(instruments),
            account=account,
            channel=channel,
        ))

    def validate(self, dataset: str) -> dict[str, object]:
        return data_validate(_args(self.root, dataset=dataset))

    def promote(self, dataset: str, *, for_use: str = "backtest") -> dict[str, object]:
        return data_promote(_args(
            self.root,
            dataset=dataset,
            for_use=for_use,
            actor=None,
            reason=None,
        ))

    def audit(self, dataset: str, *, verbose: bool = False) -> dict[str, object]:
        return data_audit(_args(self.root, dataset=dataset, verbose=verbose))

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
class RunProductApi:
    root: str | Path = "data"

    def start(
        self,
        *,
        workspace: str,
        entrypoint: str,
        mode: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, object]:
        return run_start(_args(
            self.root,
            workspace=workspace,
            entrypoint=entrypoint,
            mode=mode,
            param=[f"{key}={value}" for key, value in (params or {}).items()],
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
    def workspaces(self) -> Path:
        return self.root / ".kairos" / "workspace"

    @property
    def runs(self) -> Path:
        return self.root / ".kairos" / "run"


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


def data_apply(args) -> dict[str, object]:
    from kairospy.data import DataManifest

    return DataManifest.load(getattr(args, "manifest")).apply(
        args.lake_root,
        only=getattr(args, "only", None),
        dry_run=bool(getattr(args, "dry_run", False)),
    )


def data_start(args) -> dict[str, object]:
    kind = str(getattr(args, "kind", "") or "").strip()
    file_source = getattr(args, "file", None)
    source = file_source or getattr(args, "source", None)
    product = getattr(args, "product", None)
    dataset = getattr(args, "as_dataset", None) or getattr(args, "name", None)
    target = str(getattr(args, "for_use", None) or ("shadow" if kind == "live" else "workspace"))
    choices = [
        {
            "kind": "file",
            "description": "CSV or Parquet historical file",
            "template": "kairospy data start --kind file --file signals.csv --name features.my_signal",
        },
        {
            "kind": "connector",
            "description": "Python HistoricalDataProtocol connector",
            "template": "kairospy data start --kind connector --source connectors/my_vendor.py --name features.my_dataset",
        },
        {
            "kind": "product",
            "description": "built-in historical Data product",
            "template": "kairospy data start --kind product --product massive.equity.ohlcv.1d --as market.equity.us.ohlcv.1d --start 2024-01-01T00:00:00Z --end 2024-02-01T00:00:00Z",
        },
        {
            "kind": "live",
            "description": "live source or LiveDataProtocol connector",
            "template": "kairospy data start --kind live --source binance.quote --as market.quote.crypto.binance.btc-usdt --account binance-testnet --instrument BTCUSDT --channel quote --for paper",
        },
    ]
    if not kind:
        return {
            "product": "data",
            "operation": "start",
            "status": "needs_input",
            "question": "what data do you have?",
            "choices": choices,
        }
    if kind not in {"file", "connector", "product", "live"}:
        raise ValueError(f"unsupported data start kind: {kind}")
    missing: list[str] = []
    if kind in {"file", "connector", "live"} and source is None:
        missing.append("--file" if kind == "file" else "--source")
    if kind == "product" and not product:
        missing.append("--product")
    if kind in {"file", "connector"} and not dataset:
        missing.append("--name")
    if kind in {"product", "live"} and not dataset:
        missing.append("--as")
    if kind == "live":
        if _data_start_live_requires_account(source) and not getattr(args, "account", None):
            missing.append("--account")
        if not getattr(args, "instrument", None):
            missing.append("--instrument")
    file_check = _data_start_file_check(source) if kind == "file" and source is not None else None
    return {
        "product": "data",
        "operation": "start",
        "status": "ready" if not missing and not (file_check and file_check["status"] == "missing") else "needs_input",
        "kind": kind,
        "target_use": target,
        "command": _data_start_command(args, kind, str(dataset) if dataset else None),
        "missing": missing,
        **({"file": file_check} if file_check else {}),
        "will_run": False,
        "choices": [] if not missing else [item for item in choices if item["kind"] == kind],
    }


def _data_start_live_requires_account(source: Path | str | None) -> bool:
    if source is None:
        return False
    path = Path(source)
    if path.exists():
        return False
    try:
        from kairospy.data import BuiltInDataProductRegistry

        product = BuiltInDataProductRegistry.from_default_products().resolve(str(source))
    except KeyError:
        return False
    return bool(product.requires_account)


def _data_start_file_check(source: Path) -> dict[str, object]:
    exists = source.exists()
    suffix = source.suffix.lower()
    supported = suffix in {".csv", ".parquet"}
    readable = exists and source.is_file()
    return {
        "path": str(source),
        "exists": exists,
        "readable": readable,
        "format": suffix[1:] if suffix else None,
        "supported": supported,
        "status": "ready" if exists and readable and supported else "missing" if not exists else "unsupported",
        "issues": (
            [] if exists and readable and supported else
            ["file_not_found"] if not exists else
            ["not_a_file"] if not readable else
            ["unsupported_file_format"]
        ),
    }


def _data_start_command(args, kind: str, dataset: str | None) -> str | None:
    source = getattr(args, "file", None) or getattr(args, "source", None)
    product = getattr(args, "product", None)
    if kind == "file":
        if source is None or dataset is None:
            return None
        parts = ["kairospy", "data", "add", str(source), "--name", dataset]
        if getattr(args, "time", None):
            parts.extend(["--time", str(args.time)])
        return _shell_command(parts)
    if kind == "connector":
        if source is None or dataset is None:
            return None
        parts = ["kairospy", "data", "add", str(source), "--name", dataset, "--protocol", "historical"]
        if getattr(args, "time", None):
            parts.extend(["--time", str(args.time)])
        if getattr(args, "start_time", None):
            parts.extend(["--start", str(args.start_time)])
        if getattr(args, "end_time", None):
            parts.extend(["--end", str(args.end_time)])
        for instrument in getattr(args, "instrument", None) or ():
            parts.extend(["--instrument", str(instrument)])
        return _shell_command(parts)
    if kind == "product":
        if not product or dataset is None:
            return None
        parts = ["kairospy", "data", "use", str(product), "--as", dataset]
        if getattr(args, "start_time", None):
            parts.extend(["--start", str(args.start_time)])
        if getattr(args, "end_time", None):
            parts.extend(["--end", str(args.end_time)])
        for instrument in getattr(args, "instrument", None) or ():
            parts.extend(["--instrument", str(instrument)])
        target_use = str(getattr(args, "for_use", None) or "workspace")
        if target_use != "workspace":
            parts.extend(["--for", target_use])
        return _shell_command(parts)
    if kind == "live":
        if source is None or dataset is None:
            return None
        parts = ["kairospy", "data", "connect", str(source), "--as", dataset]
        for key in ("account", "channel", "time", "market", "levels", "interval"):
            value = getattr(args, key, None)
            if value:
                if key == "market" and str(value) == "spot":
                    continue
                parts.extend([f"--{key}", str(value)])
        for instrument in getattr(args, "instrument", None) or ():
            parts.extend(["--instrument", str(instrument)])
        target_use = str(getattr(args, "for_use", None) or "shadow")
        if target_use != "shadow":
            parts.extend(["--for", target_use])
        return _shell_command(parts)
    return None


def _shell_command(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(item)) for item in parts)


def data_add(args) -> dict[str, object]:
    from kairospy.data import HistoricalDataService

    return HistoricalDataService(args.lake_root).add(args)


def _data_add_impl(args) -> dict[str, object]:
    from kairospy.data.historical_service import HistoricalDataService

    return HistoricalDataService(args.lake_root).add(args)


def _validate_data_add_file_source(source: Path, dataset_id: str) -> None:
    add_command = f"kairospy data add {shlex.quote(str(source))} --name {shlex.quote(dataset_id)}"
    if not source.exists():
        raise DataAddInputError(
            "file_not_found",
            "Source file does not exist.",
            source=source,
            why="Data cannot be imported until the file path points to an existing CSV or Parquet file.",
            next_command=add_command,
        )
    if not source.is_file():
        raise DataAddInputError(
            "not_a_file",
            "Source path is not a file.",
            source=source,
            why="Data add expects one CSV or Parquet file, not a directory or special path.",
            next_command=add_command,
        )
    if source.suffix.lower() not in {".csv", ".parquet"}:
        raise DataAddInputError(
            "unsupported_file_format",
            "Data add supports CSV and Parquet files.",
            source=source,
            why="Other file formats need to be converted first or exposed through a HistoricalDataProtocol connector.",
            next_command=f"kairospy data add {shlex.quote(str(source))} --name {shlex.quote(dataset_id)} --protocol historical",
        )


def _is_historical_protocol_add(args) -> bool:
    protocol = getattr(args, "protocol", None)
    source = Path(args.source)
    return protocol == "historical" or protocol is None and source.suffix == ".py"


def _materialize_historical_protocol(args) -> Path:
    from kairospy.data import HistoricalDataRequest

    source = Path(args.source)
    if not source.exists():
        raise FileNotFoundError(source)
    module_hash = sha256(source.read_bytes()).hexdigest()
    module = _load_user_module(source, f"kairospy_user_historical_data_{module_hash[:12]}")
    protocol = _historical_protocol_object(module)
    request = HistoricalDataRequest(
        dataset_id=str(args.name),
        start=_optional_datetime(getattr(args, "start", None)),
        end=_optional_datetime(getattr(args, "end", None)),
        instruments=tuple(str(item) for item in getattr(args, "instrument", ()) or ()),
    )
    rows = _protocol_rows(protocol.load(request), "HistoricalDataProtocol.load")
    target = (
        Path(args.lake_root)
        / "source"
        / "user_defined"
        / "historical_protocol"
        / str(args.name).replace(".", "/")
        / module_hash[:12]
        / "rows.csv"
    )
    _write_protocol_rows_csv(target, rows)
    return target


def _historical_protocol_object(module):
    for name in ("PROTOCOL", "protocol", "SOURCE", "source", "ADAPTER", "adapter"):
        protocol = getattr(module, name, None)
        if protocol is not None and hasattr(protocol, "load") and callable(protocol.load):
            return protocol
    factory = getattr(module, "get_protocol", None)
    if callable(factory):
        protocol = factory()
        if hasattr(protocol, "load") and callable(protocol.load):
            return protocol
    legacy_factory = getattr(module, "get_adapter", None)
    if callable(legacy_factory):
        protocol = legacy_factory()
        if hasattr(protocol, "load") and callable(protocol.load):
            return protocol
    load = getattr(module, "load", None)
    if callable(load):
        class _FunctionProtocol:
            def load(self, request):
                return load(request)
        return _FunctionProtocol()
    raise ValueError("historical protocol module must define load(request), PROTOCOL.load(request), or get_protocol().load(request)")


def _protocol_rows(value: object, label: str) -> list[dict[str, object]]:
    if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
        value = value.to_dict(orient="records")
    rows = list(value or [])
    if not rows:
        raise ValueError(f"{label} returned no rows")
    result = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{label} row {index} must be a mapping")
        result.append({str(key): _protocol_cell(value) for key, value in row.items()})
    return result


def _protocol_cell(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _write_protocol_rows_csv(target: Path, rows: list[dict[str, object]]) -> None:
    fields: list[str] = []
    seen = set()
    for row in rows:
        for field in row:
            if field not in seen:
                fields.append(field)
                seen.add(field)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def data_use(args) -> dict[str, object]:
    from kairospy.data import HistoricalDataService

    return HistoricalDataService(args.lake_root).use_builtin(args)


def _data_use_impl(args) -> dict[str, object]:
    from kairospy.data.historical_service import HistoricalDataService

    return HistoricalDataService(args.lake_root).use_builtin(args)


def data_product_list(args) -> dict[str, object]:
    from kairospy.data import BuiltInDataProductRegistry

    registry = BuiltInDataProductRegistry.from_default_products()
    aliases = registry.aliases()
    aliases_by_target: dict[str, list[str]] = {}
    for alias, target in aliases.items():
        aliases_by_target.setdefault(target, []).append(alias)
    return {
        "product": "data",
        "operation": "product.list",
        "products": [
            _user_builtin_product_payload(item, aliases=aliases_by_target.get(item.key, ()))
            for item in registry.list()
        ],
    }


def data_product_doctor(args) -> dict[str, object]:
    from kairospy.surface import providers as provider_surface

    return provider_surface.data_product_doctor(
        args.lake_root,
        args.product,
    )


def _user_builtin_product_payload(item, *, aliases: Iterable[str] = ()) -> dict[str, object]:
    payload = {
        "key": item.key,
        "title": item.title,
        "capability": item.capability,
        "requires_account": item.requires_account,
        "default_dataset_name": item.default_dataset_name,
        "primary_time": item.primary_time,
        "provider": item.provider,
        "venue": item.venue,
    }
    if aliases:
        payload["aliases"] = sorted(aliases)
    return payload


_HISTORICAL_PROTOCOL_TEMPLATE = '''from __future__ import annotations


def load(request):
    """Return iterable rows for request.dataset_id.

    request has: dataset_id, start, end, instruments, params.
    Each row must be a mapping and should include one time field.
    """
    return [
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "instrument": request.instruments[0] if request.instruments else "DEMO",
            "value": 1.0,
        }
    ]
'''


_LIVE_PROTOCOL_TEMPLATE = '''from __future__ import annotations


async def stream(request):
    """Yield live rows for request.dataset_id.

    request has: dataset_id, account, instruments, channel, params.
    Each yielded item must be a mapping and should include one time field.
    """
    yield {
        "timestamp": "2026-01-01T00:00:00Z",
        "instrument": request.instruments[0] if request.instruments else "DEMO",
        "value": 1.0,
    }
'''


def data_protocol(args) -> dict[str, object]:
    action = str(getattr(args, "protocol_action", "list") or "list")
    if action == "list":
        return {
            "product": "data",
            "operation": "protocol.list",
            "protocols": [
                {
                    "kind": "historical",
                    "interface": "HistoricalDataProtocol.load(request)",
                    "used_by": "kairospy data add <connector.py> --name <dataset> --protocol historical",
                    "requires_time": True,
                },
                {
                    "kind": "live",
                    "interface": "LiveDataProtocol.stream(request)",
                    "used_by": "kairospy data connect <connector.py> --as <dataset> --protocol live",
                    "requires_time": True,
                },
            ],
        }
    if action == "template":
        kind = str(getattr(args, "kind", "") or "")
        template = _protocol_template(kind)
        output = getattr(args, "output", None)
        payload: dict[str, object] = {
            "product": "data",
            "operation": "protocol.template",
            "kind": kind,
            "status": "ready",
            "next_command": _protocol_template_next_command(kind, output),
        }
        if output:
            path = Path(output)
            if path.exists():
                raise ValueError(f"protocol template output already exists: {path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(template, encoding="utf-8")
            payload["written"] = True
            payload["file"] = path.name
        else:
            payload["template"] = template
        return payload
    if action == "check":
        return _check_user_protocol(args)
    raise ValueError(f"unsupported data protocol action {action!r}")


def data_protocol_error(args, error: BaseException) -> dict[str, object]:
    action = str(getattr(args, "protocol_action", "check") or "check")
    kind = str(getattr(args, "kind", "") or "")
    source = getattr(args, "source", None)
    return {
        "product": "data",
        "operation": f"protocol.{action}",
        "kind": kind or None,
        "source": Path(source).name if source is not None else None,
        "status": "needs_fix",
        "issues": [{
            "code": error.__class__.__name__,
            "message": str(error),
        }],
        "next_command": f"kairospy data protocol template --kind {kind or '<historical|live>'}",
    }


def _protocol_template(kind: str) -> str:
    if kind == "historical":
        return _HISTORICAL_PROTOCOL_TEMPLATE
    if kind == "live":
        return _LIVE_PROTOCOL_TEMPLATE
    raise ValueError("data protocol template --kind must be historical or live")


def _protocol_template_next_command(kind: str, output: object) -> str:
    path = str(output) if output else "<connector.py>"
    if kind == "historical":
        return f"kairospy data add {path} --name <dataset> --protocol historical --time timestamp"
    if kind == "live":
        return f"kairospy data connect {path} --as <dataset> --protocol live --time timestamp"
    return "kairospy data protocol list"


def _check_user_protocol(args) -> dict[str, object]:
    source = Path(args.source)
    if not source.exists():
        raise FileNotFoundError(source)
    if source.suffix != ".py":
        raise ValueError("data protocol check expects a Python .py file")
    kind = str(getattr(args, "kind", "") or "")
    module_hash = sha256(source.read_bytes()).hexdigest()
    module = _load_user_module(source, f"kairospy_user_protocol_check_{kind}_{module_hash[:12]}")
    if kind == "historical":
        return _check_historical_protocol(args, module)
    if kind == "live":
        _live_protocol_object(module)
        return {
            "product": "data",
            "operation": "protocol.check",
            "kind": "live",
            "source": source.name,
            "status": "ready",
            "checks": [
                {"name": "load_module", "passed": True},
                {"name": "find_stream", "passed": True},
            ],
            "next_command": f"kairospy data connect {source.name} --as {getattr(args, 'name', '<dataset>')} --protocol live --time timestamp",
        }
    raise ValueError("data protocol check --kind must be historical or live")


def _check_historical_protocol(args, module) -> dict[str, object]:
    from kairospy.data import HistoricalDataRequest

    protocol = _historical_protocol_object(module)
    request = HistoricalDataRequest(
        dataset_id=str(getattr(args, "name", None) or "workspace.protocol_check"),
        start=_optional_datetime(getattr(args, "start", None)),
        end=_optional_datetime(getattr(args, "end", None)),
        instruments=tuple(str(item) for item in getattr(args, "instrument", ()) or ()),
    )
    rows = _protocol_rows(protocol.load(request), "HistoricalDataProtocol.load")
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    time_candidates = [field for field in fields if field.lower() in {"time", "timestamp", "date", "datetime", "event_time"}]
    return {
        "product": "data",
        "operation": "protocol.check",
        "kind": "historical",
        "source": Path(args.source).name,
        "status": "ready" if time_candidates else "needs_time",
        "checks": [
            {"name": "load_module", "passed": True},
            {"name": "find_load", "passed": True},
            {"name": "sample_rows", "passed": True, "value": len(rows)},
            {"name": "time_field", "passed": bool(time_candidates), "value": time_candidates[0] if time_candidates else None},
        ],
        "fields": fields,
        "row_count": len(rows),
        "next_command": (
            f"kairospy data add {Path(args.source).name} --name {request.dataset_id} "
            f"--protocol historical --time {time_candidates[0] if time_candidates else '<time-field>'}"
        ),
    }


def _time_range_payload(value) -> dict[str, str]:
    return {"start": value.start.isoformat(), "end": value.end.isoformat()}


def data_connect(args) -> dict[str, object]:
    from kairospy.data import LiveDataService

    return LiveDataService(args.lake_root).connect(args)


def _data_connect_impl(args) -> dict[str, object]:
    from kairospy.data.live_service import LiveDataService

    return LiveDataService(args.lake_root).connect(args)


def _live_runtime_config(protocol, request) -> dict[str, object]:
    runtime_config = getattr(protocol, "runtime_config", None)
    if not callable(runtime_config):
        return {}
    value = runtime_config(request)
    if value is None:
        return {}
    if not isinstance(value, dict):
        return dict(value)
    return value


def data_sample(args, *, on_row=None) -> dict[str, object]:
    import asyncio

    from kairospy.data import BuiltInDataProductRegistry, LiveDataRequest, default_builtin_protocol_registry

    raw_limit = getattr(args, "limit", 5)
    limit = 5 if raw_limit is None else int(raw_limit)
    if limit <= 0:
        raise ValueError("data sample --limit must be positive")
    if not tuple(getattr(args, "instrument", ()) or ()):
        raise ValueError("data sample requires --instrument")
    registry = BuiltInDataProductRegistry.from_default_products()
    try:
        built_in = registry.resolve(str(args.source))
    except KeyError as error:
        raise DataProductNotFoundError(
            str(args.source),
            operation="sample",
            known_keys=tuple(item.key for item in registry.list()),
            aliases=registry.aliases(),
        ) from error
    if built_in.capability not in {"live", "both"}:
        raise ValueError(f"built-in data product {built_in.key!r} is not a live source")
    protocols = default_builtin_protocol_registry(args.lake_root, registry.list())
    protocol = protocols.live(built_in.protocol_name)
    dataset_id = str(getattr(args, "as_dataset", None) or built_in.default_dataset_name)
    request = LiveDataRequest(
        dataset_id,
        instruments=tuple(getattr(args, "instrument", ()) or ()),
        channel=getattr(args, "channel", None),
        params={
            "message_limit": limit,
            **_live_source_params(args),
            **({"connector": getattr(args, "connector")} if getattr(args, "connector", None) is not None else {}),
            **({"environment": getattr(args, "environment")} if getattr(args, "environment", None) is not None else {}),
        },
    )
    runtime_config = _live_runtime_config(protocol, request)

    async def collect() -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        async for row in protocol.stream(request):
            item = dict(row)
            rows.append(item)
            if on_row is not None:
                on_row(item)
            if len(rows) >= limit:
                break
        return rows

    rows = asyncio.run(collect())
    return {
        "product": "data",
        "operation": "sample",
        "source": built_in.key,
        "dataset": dataset_id,
        "source_kind": built_in.source_kind,
        "provider": built_in.provider,
        "venue": built_in.venue,
        "runtime": _runtime_summary(runtime_config),
        "limit": limit,
        "row_count": len(rows),
        "rows": rows,
    }


def _live_source_params(args) -> dict[str, object]:
    return {
        key: value
        for key, value in {
            "market": getattr(args, "market", None),
            "levels": getattr(args, "levels", None),
            "interval": getattr(args, "interval", None),
        }.items()
        if value is not None
    }


def _runtime_source_fields(runtime_config: dict[str, object]) -> dict[str, object]:
    return {
        key: runtime_config[key]
        for key in (
            "symbol",
            "channel",
            "stream",
            "instrument_id",
            "market",
            "levels",
            "interval",
            "public_only",
            "futures",
            "source_instance",
            "maximum_reconnects",
            "channel_capacity",
        )
        if key in runtime_config
    }


def _runtime_summary(runtime_config: dict[str, object]) -> dict[str, object]:
    return {
        key: runtime_config[key]
        for key in (
            "provider", "venue", "market", "symbol", "channel", "levels", "interval",
            "stream", "instrument_id", "public_only", "futures",
        )
        if key in runtime_config
    }


def data_doctor(args) -> dict[str, object]:
    from kairospy.data.diagnostics import DataDiagnosticsService

    return DataDiagnosticsService(args.lake_root).doctor(_dataset_argument(args))


def data_list(args) -> dict[str, object]:
    from kairospy.data import DataCatalog, load_live_view_manifest
    from kairospy.data.diagnostics import DataDiagnosticsService

    root = Path(args.lake_root)
    catalog = DataCatalog(root)
    dimensions = getattr(args, "dimension", {}) or {}
    products = catalog.search(**dimensions) if dimensions else catalog.products()
    dataset_names = {str(product.key) for product in products}
    live_root = root / "live-views"
    if live_root.exists():
        for path in sorted(live_root.glob("**/manifest.json")):
            manifest = load_live_view_manifest(path)
            dataset_names.add(manifest.dataset_id)

    diagnostics = DataDiagnosticsService(root)
    rows = []
    for dataset in sorted(dataset_names):
        status = diagnostics.doctor(dataset)
        rows.append({
            "dataset": dataset,
            "status": status.get("status"),
            "time": status.get("time"),
            "ready_for": list(status.get("ready_for") or ()),
            "blocked_for": list(status.get("blocked_for") or ()),
            "issues": list(status.get("issues") or ()),
        })
    return {
        "product": "data",
        "operation": "list",
        "datasets": rows,
        "products": rows,
    }


def data_metadata(args) -> dict[str, object]:
    root = Path(args.lake_root)
    dataset = _dataset_argument(args)
    override_time = str(getattr(args, "time", "") or "").strip()
    if override_time:
        return _override_dataset_primary_time(root, dataset, override_time)
    readiness = data_doctor(args)
    historical: dict[str, object] = {
        "configured": False,
        "schema": {},
        "quality": {},
        "coverage": {},
    }
    try:
        catalog = DataCatalog(root)
        product = catalog.product(dataset)
        releases = catalog.releases(product)
    except KeyError:
        product = None
        releases = ()
    if product is not None and releases:
        release = catalog.release(product)
        release_dir = root / release.relative_path
        quality = _read_optional_json(release_dir / "quality.json")
        manifest = _read_optional_json(release_dir / "manifest.json")
        fields = list(quality.get("fields") or manifest.get("fields") or ())
        historical = {
            "configured": True,
            "format": release.format,
            "status": release.status.value,
            "quality_level": release.quality_level.value,
            "schema": {
                "primary_time": product.primary_time,
                "fields": fields,
            },
            "quality": {
                "gate_passed": quality.get("gate_passed"),
                "diagnostic_passed": quality.get("diagnostic_passed"),
                "row_count": quality.get("row_count"),
                "checks": [
                    {"name": item.get("name"), "passed": item.get("passed"), "kind": item.get("kind")}
                    for item in [*quality.get("gate_checks", ()), *quality.get("diagnostic_checks", ())]
                    if isinstance(item, dict)
                ],
            },
            "coverage": _metadata_coverage(quality, product.primary_time),
        }
    live_views = _live_view_metadata_payloads(root, dataset)
    if product is None and not live_views:
        raise _dataset_not_found_error("metadata", dataset)
    return {
        "product": "data",
        "operation": "metadata",
        "dataset": dataset,
        "source_kind": readiness.get("source_kind"),
        "time": readiness.get("time"),
        "historical": historical,
        "live": {
            "configured": bool(live_views),
            "views": live_views,
        },
    }


def _override_dataset_primary_time(root: Path, dataset: str, primary_time: str) -> dict[str, object]:
    from dataclasses import replace

    if not primary_time:
        raise DataDatasetInputError(
            "metadata",
            dataset,
            code="time_required",
            status="needs_input",
            message="Metadata override requires a non-empty time field.",
            why="Dataset metadata needs a primary time field to support point-in-time reads and readiness checks.",
            next_command=f"kairospy data metadata {dataset} --time <field>",
        )
    catalog = DataCatalog(root)
    try:
        spec = catalog.product_spec(dataset)
    except KeyError as error:
        if _live_view_metadata_payloads(root, dataset):
            return _override_live_primary_time(root, dataset, primary_time)
        raise _dataset_not_found_error("metadata", dataset) from error
    known_fields = _metadata_historical_fields(root, spec)
    if known_fields and primary_time not in known_fields:
        raise DataDatasetInputError(
            "metadata",
            dataset,
            code="time_field_not_found",
            status="needs_input",
            message=f"Time field {primary_time} is not present in Dataset {dataset}.",
            why="Metadata override can only point at a field that exists in the Dataset schema.",
            next_command=f"kairospy data metadata {dataset} --time <field>",
        )
    updated_product = replace(spec.product, primary_time=primary_time)
    updated = replace(spec, product=updated_product)
    catalog.update_product_spec(
        updated,
        actor="data-metadata",
        reason=f"override Dataset primary time to {primary_time}",
    )
    payload = data_metadata(_args(root, dataset=dataset))
    return {
        **payload,
        "operation": "metadata",
        "status": "updated",
        "updated": {
            "time": primary_time,
            "fields": known_fields,
        },
    }


def _override_live_primary_time(root: Path, dataset: str, primary_time: str) -> dict[str, object]:
    from dataclasses import replace
    from kairospy.data import load_live_view_manifest

    directory = root / "live-views" / dataset.replace(".", "/")
    manifests = []
    for path in sorted(directory.glob("*/manifest.json")):
        manifest = load_live_view_manifest(path)
        if manifest.dataset_id == dataset:
            manifests.append((path, manifest))
    if not manifests:
        raise _dataset_not_found_error("metadata", dataset)
    known_fields = sorted({field for _, manifest in manifests for field in manifest.fields})
    if primary_time not in known_fields:
        raise DataDatasetInputError(
            "metadata",
            dataset,
            code="time_field_not_found",
            status="needs_input",
            message=f"Time field {primary_time} is not present in Dataset {dataset}.",
            why="Metadata override can only point at a field that exists in the live Dataset schema.",
            next_command=f"kairospy data metadata {dataset} --time <field>",
        )
    for path, manifest in manifests:
        write_live_view_manifest(path, replace(manifest, primary_time=primary_time))
    payload = data_metadata(_args(root, dataset=dataset))
    return {
        **payload,
        "status": "updated",
        "updated": {
            "time": primary_time,
            "fields": known_fields,
        },
    }


def _metadata_historical_fields(root: Path, spec: DataProductContract) -> list[str]:
    try:
        release = DataCatalog(root).release(spec.key)
    except KeyError:
        return []
    release_dir = root / release.relative_path
    quality = _read_optional_json(release_dir / "quality.json")
    manifest = _read_optional_json(release_dir / "manifest.json")
    fields = list(quality.get("fields") or manifest.get("fields") or ())
    return [str(field) for field in fields]


def data_validate(args) -> dict[str, object]:
    from kairospy.data import DatasetQualityService

    root = Path(args.lake_root)
    catalog = DataCatalog(root)
    release_arg = getattr(args, "release", None)
    if release_arg:
        if getattr(args, "dataset", None) or getattr(args, "dataset_arg", None):
            raise ValueError("use either Dataset or --release, not both")
        release = catalog.release(str(release_arg))
        target = release.release_id
        legacy_release_mode = True
    else:
        dataset = _dataset_argument(args)
        try:
            release = catalog.release(dataset)
        except KeyError as error:
            if _live_view_metadata_payloads(root, dataset):
                raise _historical_not_configured_error("validate", dataset) from error
            raise _dataset_not_found_error("validate", dataset) from error
        target = release.release_id
        legacy_release_mode = False

    assessment = DatasetQualityService(root).assess(target)
    checks = _user_quality_checks(assessment.checks)
    issues = [
        {
            "name": item["name"],
            "severity": item["severity"],
            "requirement": item["requirement"],
            "value": item["value"],
        }
        for item in checks
        if item["severity"] == "gate" and not item["passed"]
    ]
    payload: dict[str, object] = {
        "product": "data",
        "operation": "validate",
        "dataset": str(release.product_key),
        "status": "passed" if assessment.passed else "needs_fix",
        "profile": assessment.profile,
        "quality_level": assessment.level.value,
        "ready_for": _ready_for_quality(assessment.level) if assessment.passed else [],
        "blocked_for": _blocked_for_quality(assessment.level, passed=assessment.passed),
        "issues": issues,
        "checks": checks,
    }
    if legacy_release_mode:
        payload["release_id"] = release.release_id
    return payload


def data_replay(args) -> dict[str, object]:
    from kairospy.data import DatasetClient, OutputFormat
    from kairospy.infrastructure.storage.codec import to_primitive

    root = Path(args.lake_root)
    dataset = _dataset_argument(args)
    try:
        query = DatasetClient(root).get(
            dataset,
            start=getattr(args, "start", None),
            end=getattr(args, "end", None),
            instruments=tuple(getattr(args, "instrument", ()) or ()),
            fields=tuple(getattr(args, "field", ()) or ()) or None,
        )
    except KeyError as error:
        if _live_view_metadata_payloads(root, dataset):
            return _data_replay_live_capture(root, args, dataset)
        raise _dataset_not_found_error("replay", dataset) from error
    rows = list(query.collect(OutputFormat.ROWS))
    explain = query.explain()
    time_field = str(explain.get("time_field") or "")
    rows = _sort_replay_rows(rows, time_field)
    limit = int(getattr(args, "limit", 20) or 20)
    if limit <= 0:
        raise ValueError("data replay --limit must be positive")
    return {
        "product": "data",
        "operation": "replay",
        "dataset": str(explain.get("logical_name") or dataset),
        "time": time_field or None,
        "window": {
            "start": getattr(args, "start", None),
            "end": getattr(args, "end", None),
            "boundary": "[start,end)",
        },
        "replay": {
            "source": "governed_dataset",
            "order": time_field or "storage_order",
            "deterministic": True,
        },
        "returned_rows": min(len(rows), limit),
        "total_rows": len(rows),
        "rows": to_primitive(rows[:limit]),
    }


def _sort_replay_rows(rows: list[dict[str, object]], time_field: str) -> list[dict[str, object]]:
    if not time_field or not all(isinstance(row, dict) and time_field in row for row in rows):
        return rows
    return sorted(rows, key=lambda row: str(row.get(time_field)))


def _data_replay_live_capture(root: Path, args, dataset: str) -> dict[str, object]:
    import asyncio
    from kairospy.market.capture import CapturedCanonicalEventSource
    from kairospy.infrastructure.storage.codec import to_primitive

    manifest = _latest_live_view_manifest(root, dataset)
    if manifest is None:
        raise _dataset_not_found_error("replay", dataset)
    evidence = manifest.live_data_plane.get("freshness_evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    capture_path = str(evidence.get("artifact") or "").strip()
    if not capture_path:
        raise DataDatasetInputError(
            "replay",
            dataset,
            code="live_capture_not_available",
            status="needs_data",
            message=f"Dataset {dataset} has no replayable live capture evidence.",
            why="Live replay needs canonical capture evidence recorded by the live data monitor.",
            next_command=f"kairospy data connect {manifest.source.get('name', '<source>')} --as {dataset}",
        )
    path = Path(capture_path)
    if not path.exists():
        raise DataDatasetInputError(
            "replay",
            dataset,
            code="live_capture_missing",
            status="needs_data",
            message=f"Dataset {dataset} live capture evidence is missing.",
            why="The Dataset points at capture evidence that is no longer available on disk.",
            next_command=f"kairospy data reconnect {dataset}",
        )

    async def collect_events() -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        async for event in CapturedCanonicalEventSource(path).events():
            result.append(_user_replay_event_row(to_primitive(event)))
        return result

    rows = asyncio.run(collect_events())
    rows = _filter_live_replay_rows(
        rows,
        start=getattr(args, "start", None),
        end=getattr(args, "end", None),
        instruments=tuple(getattr(args, "instrument", ()) or ()),
    )
    limit = int(getattr(args, "limit", 20) or 20)
    if limit <= 0:
        raise ValueError("data replay --limit must be positive")
    return {
        "product": "data",
        "operation": "replay",
        "dataset": dataset,
        "time": manifest.primary_time,
        "window": {
            "start": getattr(args, "start", None),
            "end": getattr(args, "end", None),
            "boundary": "[start,end)",
        },
        "replay": {
            "source": "live_capture",
            "order": "available_time",
            "deterministic": True,
        },
        "returned_rows": min(len(rows), limit),
        "total_rows": len(rows),
        "rows": rows[:limit],
    }


def _user_replay_event_row(raw: dict[str, object]) -> dict[str, object]:
    return {
        key: raw.get(key)
        for key in (
            "kind",
            "instrument_id",
            "event_time",
            "available_time",
            "canonical_sequence",
            "payload",
        )
        if key in raw
    }


def _filter_live_replay_rows(
    rows: list[dict[str, object]],
    *,
    start: str | None,
    end: str | None,
    instruments: tuple[str, ...],
) -> list[dict[str, object]]:
    allowed_instruments = {str(item) for item in instruments}

    def timestamp(value: object) -> str:
        if isinstance(value, dict):
            return str(value.get("$datetime") or value.get("$date") or "")
        return str(value or "")

    def instrument_value(value: object) -> str:
        if isinstance(value, dict):
            return str(value.get("value") or value)
        return str(value or "")

    filtered = []
    for row in rows:
        current = timestamp(row.get("available_time") or row.get("event_time"))
        if start and current < str(start):
            continue
        if end and current >= str(end):
            continue
        if allowed_instruments and instrument_value(row.get("instrument_id")) not in allowed_instruments:
            continue
        filtered.append(row)
    return sorted(filtered, key=lambda row: timestamp(row.get("available_time") or row.get("event_time")))


def _user_quality_checks(checks) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    integrity_passed = True
    integrity_seen = False
    for item in checks:
        if "hash" in item.name:
            integrity_seen = True
            integrity_passed = integrity_passed and item.passed
            continue
        payload.append({
            "name": item.name,
            "passed": item.passed,
            "severity": item.severity,
            "requirement": item.requirement,
            "value": item.value,
        })
    if integrity_seen:
        payload.append({
            "name": "release_integrity",
            "passed": integrity_passed,
            "severity": "gate",
            "requirement": "registered data matches its immutable release evidence",
            "value": "verified" if integrity_passed else "failed",
        })
    return payload


def _ready_for_quality(level: QualityLevel) -> list[str]:
    if level is QualityLevel.PRODUCTION:
        return ["workspace", "backtest", "production"]
    if level is QualityLevel.BACKTEST:
        return ["workspace", "backtest"]
    if level is QualityLevel.WORKSPACE:
        return ["workspace"]
    return []


def _blocked_for_quality(level: QualityLevel, *, passed: bool) -> list[str]:
    if not passed:
        return ["workspace", "backtest", "production"]
    if level is QualityLevel.PRODUCTION:
        return []
    if level is QualityLevel.BACKTEST:
        return ["production"]
    if level is QualityLevel.WORKSPACE:
        return ["backtest", "production"]
    return ["workspace", "backtest", "production"]


def _read_optional_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    value = _read_json(path)
    return value if isinstance(value, dict) else {}


def _metadata_coverage(quality: dict[str, object], primary_time: str) -> dict[str, object]:
    row_count = int(quality.get("row_count") or 0)
    checks = quality.get("diagnostic_checks") if isinstance(quality.get("diagnostic_checks"), list) else []
    duplicate = next((item for item in checks if isinstance(item, dict) and item.get("name") == "duplicate_primary_time"), {})
    missing = next((item for item in checks if isinstance(item, dict) and item.get("name") == "missing_values"), {})
    missing_by_field = missing.get("missing_by_field") if isinstance(missing, dict) else {}
    return {
        "row_count": row_count,
        "primary_time": primary_time,
        "duplicate_primary_time": duplicate.get("duplicates") if isinstance(duplicate, dict) else None,
        "missing_primary_time": (
            missing_by_field.get(primary_time)
            if isinstance(missing_by_field, dict) and primary_time
            else None
        ),
    }


def _live_view_metadata_payloads(root: Path, dataset: str) -> list[dict[str, object]]:
    from kairospy.data import evaluate_live_view_freshness, load_live_view_manifest

    directory = root / "live-views" / dataset.replace(".", "/")
    payloads = []
    for path in sorted(directory.glob("*/manifest.json")):
        manifest = load_live_view_manifest(path)
        if manifest.dataset_id != dataset:
            continue
        plane = manifest.live_data_plane
        freshness = plane.get("freshness") if isinstance(plane.get("freshness"), dict) else {}
        gate = evaluate_live_view_freshness(manifest, policy=PAPER_LIVE_FRESHNESS_POLICY)
        payloads.append({
            "primary_time": manifest.primary_time,
            "fields": list(manifest.fields),
            "freshness_policy": {
                "name": PAPER_LIVE_FRESHNESS_POLICY.name,
                "max_age_seconds": freshness.get("max_age_seconds"),
                "status": manifest.freshness_status,
                "passed": gate.passed,
                "channel_failures": list(gate.channel_failures),
            },
            "source": {
                key: value
                for key, value in manifest.source.items()
                if key in {"source_kind", "provider", "venue", "channel", "instrument_id", "stream"}
            },
        })
    return payloads


def _dataset_argument(args) -> str:
    option = getattr(args, "dataset", None)
    positional = getattr(args, "dataset_arg", None)
    if option and positional and str(option) != str(positional):
        raise ValueError(f"conflicting Dataset values: {positional} and {option}")
    dataset = option or positional
    if not dataset:
        raise ValueError("Dataset is required")
    return str(dataset)


def _live_protocol_object(module):
    for name in ("PROTOCOL", "protocol", "SOURCE", "source", "ADAPTER", "adapter"):
        protocol = getattr(module, name, None)
        if protocol is not None and hasattr(protocol, "stream") and callable(protocol.stream):
            return protocol
    factory = getattr(module, "get_protocol", None)
    if callable(factory):
        protocol = factory()
        if hasattr(protocol, "stream") and callable(protocol.stream):
            return protocol
    legacy_factory = getattr(module, "get_adapter", None)
    if callable(legacy_factory):
        protocol = legacy_factory()
        if hasattr(protocol, "stream") and callable(protocol.stream):
            return protocol
    stream = getattr(module, "stream", None)
    if callable(stream):
        class _FunctionProtocol:
            async def stream(self, request):
                async for item in stream(request):
                    yield item
        return _FunctionProtocol()
    raise ValueError("live protocol module must define stream(request), PROTOCOL.stream(request), or get_protocol().stream(request)")


def data_reconnect(args) -> dict[str, object]:
    from kairospy.data import LiveDataService

    return LiveDataService(args.lake_root).reconnect(args)


def _data_reconnect_impl(args) -> dict[str, object]:
    from kairospy.data.live_service import LiveDataService

    return LiveDataService(args.lake_root).reconnect(args)


def _latest_live_view_manifest(root: Path, dataset: str):
    from kairospy.data import load_live_view_manifest

    directory = root / "live-views" / dataset.replace(".", "/")
    candidates = []
    for path in sorted(directory.glob("*/manifest.json")):
        manifest = load_live_view_manifest(path)
        if manifest.dataset_id == dataset:
            candidates.append(manifest)
    return candidates[-1] if candidates else None


def data_promote(args) -> dict[str, object]:
    from kairospy.data import DatasetQualityService, evaluate_data_promotion_policy

    dataset = str(args.dataset)
    target_quality = _promotion_quality(str(args.for_use))
    target_status = _promotion_status(target_quality)
    root = Path(args.lake_root)
    catalog = DataCatalog(root)
    try:
        release = catalog.release(dataset)
    except KeyError as error:
        if _live_view_metadata_payloads(root, dataset):
            raise _historical_not_configured_error("promote", dataset) from error
        raise _dataset_not_found_error("promote", dataset) from error
    try:
        assessment = DatasetQualityService(root).assess(release.release_id)
        policy = evaluate_data_promotion_policy(release.release_id, assessment, target_quality)
    except Exception as error:
        return {
            "product": "data",
            "operation": "promote",
            "dataset": dataset,
            "target": str(args.for_use),
            "status": "needs_fix",
            "ready_for": ["workspace"] if release.status is DatasetStatus.APPROVED_FOR_WORKSPACE else [],
            "blocked_for": [str(args.for_use)],
            "issues": [f"{type(error).__name__}: {error}"],
        }
    if not policy.passed:
        return {
            "product": "data",
            "operation": "promote",
            "dataset": dataset,
            "target": str(args.for_use),
            "status": "needs_fix",
            "quality_level": assessment.level.value,
            "ready_for": ["workspace"] if release.status is DatasetStatus.APPROVED_FOR_WORKSPACE else [],
            "blocked_for": [str(args.for_use)],
            "issues": [policy.reason],
            "checks": [
                {"name": item.name, "passed": item.passed, "severity": item.severity}
                for item in assessment.checks
            ],
        }
    current = DataCatalog(root).release(release.release_id)
    promoted = current
    actor = getattr(args, "actor", None) or "data-promote"
    reason = getattr(args, "reason", None) or f"promote Dataset {dataset} for {args.for_use}"
    while _dataset_status_rank(promoted.status) < _dataset_status_rank(target_status):
        promoted = DataCatalog(root).promote(promoted.release_id, _next_dataset_status(promoted.status),
                                             actor=actor, reason=reason)
    return {
        "product": "data",
        "operation": "promote",
        "dataset": dataset,
        "target": str(args.for_use),
        "status": _ready_status(promoted.status),
        "quality_level": assessment.level.value,
        "ready_for": _ready_for_status(promoted.status),
        "blocked_for": _blocked_for_status(promoted.status),
        "issues": [],
    }


def _promotion_quality(value: str) -> QualityLevel:
    if value == "workspace":
        return QualityLevel.WORKSPACE
    if value == "backtest":
        return QualityLevel.BACKTEST
    if value == "production":
        return QualityLevel.PRODUCTION
    raise ValueError(f"unsupported data promotion target: {value}")


def _promotion_status(level: QualityLevel) -> DatasetStatus:
    if level is QualityLevel.WORKSPACE:
        return DatasetStatus.APPROVED_FOR_WORKSPACE
    if level is QualityLevel.BACKTEST:
        return DatasetStatus.APPROVED_FOR_BACKTEST
    return DatasetStatus.APPROVED_FOR_PRODUCTION


def _dataset_status_rank(status: DatasetStatus) -> int:
    order = [
        DatasetStatus.VALIDATED,
        DatasetStatus.APPROVED_FOR_WORKSPACE,
        DatasetStatus.APPROVED_FOR_BACKTEST,
        DatasetStatus.APPROVED_FOR_PRODUCTION,
    ]
    return order.index(status) if status in order else -1


def _next_dataset_status(status: DatasetStatus) -> DatasetStatus:
    if status is DatasetStatus.VALIDATED:
        return DatasetStatus.APPROVED_FOR_WORKSPACE
    if status is DatasetStatus.APPROVED_FOR_WORKSPACE:
        return DatasetStatus.APPROVED_FOR_BACKTEST
    if status is DatasetStatus.APPROVED_FOR_BACKTEST:
        return DatasetStatus.APPROVED_FOR_PRODUCTION
    raise ValueError(f"cannot promote release from {status.value}")


def _ready_status(status: DatasetStatus) -> str:
    if status is DatasetStatus.APPROVED_FOR_PRODUCTION:
        return "ready_for_production"
    if status is DatasetStatus.APPROVED_FOR_BACKTEST:
        return "ready_for_backtest"
    return "ready_for_workspace"


def _ready_for_status(status: DatasetStatus) -> list[str]:
    ready = ["workspace"]
    if _dataset_status_rank(status) >= _dataset_status_rank(DatasetStatus.APPROVED_FOR_BACKTEST):
        ready.append("backtest")
    if _dataset_status_rank(status) >= _dataset_status_rank(DatasetStatus.APPROVED_FOR_PRODUCTION):
        ready.append("production")
    return ready


def _blocked_for_status(status: DatasetStatus) -> list[str]:
    return [item for item in ("backtest", "production") if item not in _ready_for_status(status)]


def data_audit(args) -> dict[str, object]:
    root = Path(args.lake_root)
    dataset = str(args.dataset)
    verbose = bool(getattr(args, "verbose", False))
    releases = []
    try:
        catalog = DataCatalog(root)
        product = catalog.product(dataset)
        releases = [
            _release_audit_payload(root, release, verbose=verbose)
            for release in catalog.releases(product)
        ]
    except KeyError:
        releases = []
    live_views = _live_view_audit_payloads(root, dataset, verbose=verbose)
    if not releases and not live_views:
        raise KeyError(f"unknown Dataset for audit: {dataset}")
    return {
        "product": "data",
        "operation": "audit",
        "dataset": dataset,
        "historical": {
            "release_count": len(releases),
            "releases": releases,
        },
        "live": {
            "live_view_count": len(live_views),
            "live_views": live_views,
        },
    }


def _release_audit_payload(root: Path, release: DatasetRelease, *, verbose: bool) -> dict[str, object]:
    evidence = _data_release_evidence(root, release)
    payload = {
        **evidence,
        "status": release.status.value,
        "provider": release.provider,
        "venue": release.venue,
        "storage_kind": release.storage_kind.value,
        "format": release.format,
    }
    if verbose:
        release_dir = root / release.relative_path
        payload["artifact"] = str(release_dir)
        documents = _metadata_documents(release_dir)
        payload["documents"] = documents
        payload["lineage_summary"] = _audit_lineage_summary(documents)
        payload["source_cache_summary"] = _audit_source_cache_summary(release_dir, documents, release)
        payload["quality_report"] = _audit_quality_report_summary(documents)
    return payload


def _live_view_audit_payloads(root: Path, dataset: str, *, verbose: bool) -> list[dict[str, object]]:
    from kairospy.data import load_live_view_manifest

    directory = root / "live-views" / dataset.replace(".", "/")
    payloads = []
    for path in sorted(directory.glob("*/manifest.json")):
        manifest = load_live_view_manifest(path)
        if manifest.dataset_id != dataset:
            continue
        payload = {
            "dataset": manifest.dataset_id,
            "live_view_id": manifest.live_view_id,
            "manifest_hash": manifest.manifest_hash,
            "artifact_ref": manifest.artifact_ref,
            "primary_time": manifest.primary_time,
            "fields": list(manifest.fields),
            "freshness_status": manifest.freshness_status,
            "source": dict(manifest.source),
        }
        if verbose:
            payload["artifact"] = str(path)
            payload["manifest"] = manifest.to_primitive()
        payloads.append(payload)
    return payloads


def _metadata_documents(directory: Path) -> dict[str, object]:
    documents = {}
    for path in sorted(directory.glob("*.json")):
        try:
            documents[path.stem] = _read_json(path)
        except json.JSONDecodeError:
            documents[path.stem] = {"error": "invalid_json", "path": str(path)}
    return documents


def _audit_lineage_summary(documents: dict[str, object]) -> dict[str, object]:
    lineage = documents.get("lineage")
    if isinstance(lineage, dict):
        return {
            key: lineage.get(key)
            for key in ("source", "inputs", "transform", "producer")
            if key in lineage
        }
    manifest = documents.get("manifest")
    manifest = manifest if isinstance(manifest, dict) else {}
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    return {
        "source": {
            key: source.get(key)
            for key in ("kind", "name", "provider", "venue")
            if key in source
        }
    }


def _audit_source_cache_summary(directory: Path, documents: dict[str, object], release: DatasetRelease) -> dict[str, object]:
    from kairospy.infrastructure.storage.source_cache import SourceCacheStore

    manifest = documents.get("manifest")
    manifest = manifest if isinstance(manifest, dict) else {}
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    source_summary = {
        key: source.get(key)
        for key in ("kind", "name", "provider", "venue")
        if key in source
    }
    return SourceCacheStore(directory).summary(
        directory,
        provider=release.provider,
        venue=release.venue,
        source=source_summary,
    )


def _audit_quality_report_summary(documents: dict[str, object]) -> dict[str, object]:
    quality = documents.get("quality")
    if not isinstance(quality, dict):
        return {}
    row_count = quality.get("row_count")
    if row_count is None and isinstance(quality.get("checks"), list):
        for check in quality["checks"]:
            if isinstance(check, dict) and check.get("name") == "non_empty":
                row_count = check.get("value")
                break
    return {
        "row_count": row_count,
        "quality_level": quality.get("quality_level") or quality.get("level"),
        "gate_passed": quality.get("gate_passed") if "gate_passed" in quality else quality.get("passed"),
        "diagnostic_passed": quality.get("diagnostic_passed"),
        "quality_report_hash": quality.get("quality_report_hash") or quality.get("report_hash"),
    }


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
    module = _load_user_module(provider_path, f"kairospy_data_provider_{provider_hash[:12]}")
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
            from kairospy.infrastructure.configuration import KairosProjectConfig
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
        QualityLevel.WORKSPACE: 2,
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
    from kairospy.infrastructure.storage.source_cache import SourceCacheStore

    if not source.exists():
        raise FileNotFoundError(source)
    fields = _contract_fields(contract)
    primary_time = str(contract.get("primary_time") or contract.get("time", {}).get("primary_time") or "")
    if not primary_time:
        raise ValueError("data write contract must declare primary_time")
    source_format = _historical_file_format(source)
    _validate_historical_file_fields(source, fields, source_format)
    material = source.read_bytes() + json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    content_hash = sha256(material).hexdigest()
    release_id = f"{dataset_id}:write:{content_hash[:12]}"
    cache = SourceCacheStore(root)
    directory = cache.release_directory(dataset_id, release_id)
    directory.mkdir(parents=True, exist_ok=True)
    cache.cache_user_file(source, dataset_id=dataset_id, release_id=release_id)
    readable = directory / "event_year=all" / "event_month=all" / f"part-000.{source_format}"
    readable.parent.mkdir(parents=True, exist_ok=True)
    if not readable.exists() or readable.read_bytes() != source.read_bytes():
        readable.write_bytes(source.read_bytes())
    manifest = DataReleaseManifest(
        dataset_id,
        release_id,
        stable_artifact_hash(contract),
        content_hash,
        primary_time,
        tuple(fields),
        QualityLevel.WORKSPACE,
        {"kind": "file", "name": source.name},
        _now(),
    )
    manifest_payload = manifest.to_primitive()
    _write_json(directory / "manifest.json", manifest_payload)
    quality_report = _data_quality_report(source, dataset_id, contract, fields, primary_time)
    quality_report_hash = _stable_hash(quality_report)
    quality_report = {**quality_report, "quality_report_hash": quality_report_hash}
    _write_json(directory / "quality.json", quality_report)
    _register_written_release(root, dataset_id, release_id, directory, content_hash, primary_time, source_format)
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
    rows = _historical_file_rows(source)
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
        "quality_level": QualityLevel.WORKSPACE.value,
        "row_count": len(rows),
        "fields": fields,
        "contract_hash": stable_artifact_hash(contract),
        "gate_checks": gate_checks,
        "diagnostic_checks": diagnostic_checks,
        "gate_passed": all(bool(item["passed"]) for item in gate_checks),
        "diagnostic_passed": all(bool(item["passed"]) for item in diagnostic_checks),
        "checked_at": _now(),
    }


def _historical_file_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return "parquet"
    if suffix == ".csv":
        return "csv"
    raise ValueError("data add supports CSV and Parquet files; use --protocol historical for Python connectors")


def _validate_historical_file_fields(path: Path, fields: list[str], file_format: str) -> None:
    if file_format == "csv":
        _validate_csv_header(path, fields)
        return
    if file_format == "parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as error:
            raise RuntimeError("Parquet data add requires pyarrow") from error
        header = list(pq.read_schema(path).names)
        missing = sorted(set(fields) - set(header))
        if missing:
            raise ValueError(f"Parquet file is missing contract fields: {', '.join(missing)}")
        return
    raise ValueError(f"unsupported historical file format: {file_format}")


def _historical_file_rows(path: Path) -> list[dict[str, object]]:
    file_format = _historical_file_format(path)
    if file_format == "csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("Parquet data add requires pyarrow") from error
    return [dict(row) for row in pq.read_table(path).to_pylist()]


def _write_rows_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def run_exists(root: str | Path, run_id: str) -> bool:
    return _find_run_manifest(root, run_id) is not None


def _run_start_workspace_entrypoint(args) -> dict[str, object]:
    if not getattr(args, "entrypoint", None):
        raise ValueError("RunConfig run.entrypoint is required")

    from kairospy.infrastructure.configuration import KairosProjectConfig, PROJECT_STATE_DIR
    from kairospy.infrastructure.storage.codec import to_primitive
    from kairospy.workspace import WorkspaceRepository

    config = KairosProjectConfig.discover(Path.cwd())
    workspace = WorkspaceRepository(config.root).open(args.workspace)
    params = _parse_run_params(getattr(args, "param", ()))
    run_config = getattr(args, "_run_config", None)
    run_config_path = getattr(run_config, "path", None)
    run_config_hash = _file_sha256(run_config_path) if run_config_path is not None else None
    project_config_hash = _file_sha256(config.path)
    workspace_snapshot = workspace.snapshot()
    entrypoint_ref = str(args.entrypoint)
    module, callable_name, entrypoint = _load_run_entrypoint(entrypoint_ref, config.root)
    _validate_workspace_entrypoint_requirements(module, entrypoint, workspace_snapshot)

    material = {
        "workspace": workspace.name,
        "mode": args.mode,
        "entrypoint": entrypoint_ref,
        "params": params,
        "run_config": str(run_config_path) if run_config_path is not None else None,
        "run_config_hash": run_config_hash,
        "at": _now(),
    }
    run_id = f"run_{sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:16]}"
    directory = config.root / PROJECT_STATE_DIR / "run" / run_id
    workspace_snapshot_hash = _stable_hash(_json_safe(workspace_snapshot))
    strategy_hash = _stable_hash({"entrypoint": entrypoint_ref, "module": module.__name__, "callable": callable_name})
    params_hash = _stable_hash(params)
    config_hash = _stable_hash({"params": params, "run_config_hash": run_config_hash})
    context = SimpleNamespace(
        run_id=run_id,
        workspace=workspace.name,
        mode=args.mode,
        now=datetime.now(timezone.utc),
        data=workspace.data,
        params=params,
        state={},
    )
    execution_holder: dict[str, object] = {}

    def strategy_runner(_prepared: object):
        execution = _execute_workspace_strategy_entrypoint(entrypoint, workspace, params, context)
        execution_holder["execution"] = execution
        return _strategy_run_result_from_workspace_execution(execution)

    runtime_launch = None
    run_kernel_result = None
    if args.mode == "paper":
        runtime_launch, run_kernel_result = _launch_workspace_paper_run(
            directory,
            run_id,
            workspace.name,
            workspace_snapshot_hash,
            strategy_hash,
            config_hash,
            params,
            strategy_runner,
        )
    elif args.mode == "live":
        runtime_launch, run_kernel_result = _launch_workspace_live_run(
            config,
            directory,
            run_id,
            workspace.name,
            workspace_snapshot_hash,
            strategy_hash,
            config_hash,
            params,
            strategy_runner,
            run_config=run_config,
            workspace_snapshot=workspace_snapshot,
            confirm_live=bool(getattr(args, "confirm_live", False)),
            supervise_live_services=bool(getattr(args, "supervise_live_services", False)),
        )
    else:
        strategy_runner(None)
    execution = execution_holder.get("execution") or {
        "entrypoint_kind": "not_run",
        "decisions": [],
        "result": None,
    }
    decisions = _json_fallback(to_primitive(execution["decisions"]))
    result = _json_fallback(to_primitive(execution["result"]))
    decision_artifact = directory / "artifacts" / "decisions.json"
    result_artifact = directory / "artifacts" / "result.json"
    snapshot_artifact = directory / "workspace_snapshot.json"
    artifacts = {
        "decisions": str(decision_artifact),
        "summary": str(directory / "reports" / "summary.json"),
    }
    if result is not None:
        artifacts["result"] = str(result_artifact)
    bindings_table = _run_config_table(run_config, "bindings") if run_config is not None else {}
    guards_table = _run_config_table(run_config, "guards") if run_config is not None else {}
    evidence_table = _run_config_table(run_config, "evidence") if run_config is not None else {}
    account_binding_name = str(bindings_table.get("account") or "")
    execution_binding_name = str(bindings_table.get("execution") or account_binding_name)
    market_binding = bindings_table.get("market", ())
    account_binding = config.get(f"accounts.{account_binding_name}", {}) if account_binding_name else {}
    execution_binding = config.get(f"accounts.{execution_binding_name}", {}) if execution_binding_name else {}
    bindings_manifest = {
        "account_binding": account_binding_name or None,
        "account_binding_hash": _stable_hash(_json_safe(account_binding)) if account_binding else None,
        "market": list(market_binding) if isinstance(market_binding, list) else [],
        "market_binding_hash": _stable_hash(_json_safe(market_binding)),
        "execution_binding": execution_binding_name or None,
        "execution_binding_hash": _stable_hash(_json_safe(execution_binding)) if execution_binding else None,
    }
    guards_manifest = {
        **dict(guards_table),
        "confirm_live": bool(getattr(args, "confirm_live", False)),
        "readiness_ref": evidence_table.get("readiness"),
        "promotion_ref": evidence_table.get("promotion"),
    }
    _write_json(snapshot_artifact, workspace_snapshot)
    resolved_config_artifact = None
    if run_config_path is not None:
        resolved_config_artifact = directory / "resolved_config.toml"
        resolved_config_artifact.parent.mkdir(parents=True, exist_ok=True)
        resolved_config_artifact.write_text(Path(run_config_path).read_text(encoding="utf-8"), encoding="utf-8")
    from kairospy.runtime.run_instance import RunManifestBuilder

    manifest = RunManifestBuilder().build(
        run_id=run_id,
        mode=args.mode,
        status="completed",
        project_config_path=config.path,
        project_config_hash=project_config_hash,
        run_config_path=run_config_path,
        run_config_hash=run_config_hash,
        workspace_name=workspace.name,
        workspace_root=workspace.root,
        workspace_snapshot_artifact=snapshot_artifact,
        workspace_snapshot_hash=workspace_snapshot_hash,
        strategy={
            "entrypoint": entrypoint_ref,
            "module": module.__name__,
            "callable": callable_name,
            "entrypoint_kind": execution["entrypoint_kind"],
            "hash": strategy_hash,
            "params": params,
        },
        params_hash=params_hash,
        config_hash=config_hash,
        bindings=bindings_manifest,
        guards=guards_manifest,
        started_at=material["at"],
        finished_at=_now(),
        artifacts=artifacts,
        runtime_launch=runtime_launch,
        run_result=run_kernel_result.manifest() if run_kernel_result is not None else None,
        resolved_config_artifact=resolved_config_artifact,
    )
    _write_json(directory / "entrypoint.json", manifest["strategy"])
    _write_json(decision_artifact, decisions)
    if result is not None:
        _write_json(result_artifact, result)
    _write_json(directory / "manifest.json", manifest)
    _write_json(directory / "reports" / "summary.json", {
        "run_id": run_id,
        "workspace": workspace.name,
        "mode": args.mode,
        "entrypoint": entrypoint_ref,
        "passed": True,
        "decisions_count": len(decisions) if isinstance(decisions, list) else int(decisions is not None),
        "result_artifact": str(result_artifact) if result is not None else None,
        "runtime_launch": runtime_launch,
    })
    return {
        **manifest,
        "workspace_root": str(workspace.root),
        "run_workspace": str(directory),
        "manifest": str(directory / "manifest.json"),
    }


def _parse_run_params(values: Iterable[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"run parameter must be key=value: {item!r}")
        key, value = item.split("=", 1)
        if not key.strip():
            raise ValueError("run parameter key is required")
        params[key.strip()] = value
    return params


def _load_run_entrypoint(ref: str, project_root: Path):
    if ":" not in ref:
        raise ValueError("strategy entrypoint must be module:callable")
    module_name, callable_name = ref.split(":", 1)
    if not module_name or not callable_name:
        raise ValueError("strategy entrypoint must be module:callable")
    project_root = project_root.expanduser().resolve()
    root_text = str(project_root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    _drop_stale_project_module(module_name, project_root)
    importlib.invalidate_caches()
    module = importlib.import_module(module_name)
    entrypoint = getattr(module, callable_name)
    if not callable(entrypoint):
        raise ValueError(f"strategy entrypoint is not callable: {ref}")
    return module, callable_name, entrypoint


def _drop_stale_project_module(module_name: str, project_root: Path) -> None:
    parts = module_name.split(".")
    top_level = parts[0]
    if not ((project_root / f"{top_level}.py").exists() or (project_root / top_level).exists()):
        return
    for index in range(1, len(parts) + 1):
        candidate = ".".join(parts[:index])
        module = sys.modules.get(candidate)
        if module is None:
            continue
        locations = []
        module_file = getattr(module, "__file__", None)
        if module_file:
            locations.append(Path(str(module_file)))
        locations.extend(Path(str(item)) for item in getattr(module, "__path__", ()) or ())
        if not locations:
            continue
        try:
            resolved = tuple(path.resolve() for path in locations)
        except OSError:
            continue
        if not any(path.is_relative_to(project_root) for path in resolved):
            sys.modules.pop(candidate, None)


def _validate_workspace_entrypoint_requirements(module: object, entrypoint: object, snapshot: dict[str, Any]) -> None:
    requires = getattr(entrypoint, "REQUIRES", None) or getattr(module, "REQUIRES", None)
    if not isinstance(requires, dict):
        return
    inputs = requires.get("inputs", {})
    if not isinstance(inputs, dict):
        return
    bindings = snapshot.get("bindings", {})
    missing = [name for name in inputs if name not in bindings]
    if missing:
        raise ValueError(f"workspace is missing strategy data bindings: {', '.join(sorted(missing))}")


def _execute_workspace_strategy_entrypoint(entrypoint: object, workspace: object, params: dict[str, str], context: SimpleNamespace) -> dict[str, object]:
    signature = inspect.signature(entrypoint)
    positional = [
        param
        for param in signature.parameters.values()
        if param.kind in {param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD}
    ]
    if len(positional) <= 1:
        return {
            "entrypoint_kind": "context_decide",
            "decisions": _normalize_decisions(entrypoint(context)),
            "result": None,
        }

    strategy = entrypoint(workspace, params)
    if hasattr(strategy, "decide"):
        return {
            "entrypoint_kind": "workspace_strategy",
            "decisions": _normalize_decisions(strategy.decide(context)),
            "result": None,
        }

    decisions: list[object] = []
    used_hooks = False
    for hook, args in (
        ("on_start", (context,)),
        ("on_tick", (0, context)),
        ("on_stop", (context,)),
    ):
        callback = getattr(strategy, hook, None)
        if callable(callback):
            used_hooks = True
            decisions.extend(_normalize_decisions(callback(*args)))
    if used_hooks:
        return {
            "entrypoint_kind": "workspace_lifecycle_strategy",
            "decisions": decisions,
            "result": None,
        }
    return {
        "entrypoint_kind": "workspace_function",
        "decisions": [],
        "result": strategy,
    }


def _normalize_decisions(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _json_fallback(value: object) -> object:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _strategy_run_result_from_workspace_execution(execution: dict[str, object]):
    from kairospy.infrastructure.storage.codec import to_primitive
    from kairospy.runtime import StrategyRunResult

    decisions = tuple(_normalize_decisions(execution.get("decisions")))
    factor_hash = _run_component_hash(())
    decision_hash = _run_component_hash(to_primitive(decisions))
    intent_hash = _run_component_hash(())
    audit_hash = _run_component_hash({
        "events": [],
        "factor_hash": factor_hash,
        "decision_hash": decision_hash,
        "intent_hash": intent_hash,
    })
    return StrategyRunResult((), (), decisions, (), factor_hash, decision_hash, intent_hash, audit_hash)


def _launch_workspace_paper_run(
    directory: Path,
    run_id: str,
    workspace_name: str,
    workspace_snapshot_hash: str,
    strategy_hash: str,
    config_hash: str,
    params: dict[str, str],
    strategy_runner,
) -> tuple[dict[str, object], object]:
    from kairospy.data.contracts import RunMode
    from kairospy.governance import GovernanceRunArtifactWriter, ReadinessEvidence, RunArtifactRepository
    from kairospy.environment import Environment
    from kairospy.runtime import ManagedServiceEvidenceProvider, RunKernel, RunRequest, RuntimeRunLauncher
    from kairospy.runtime.application import FunctionProbe, KairosApplication
    from kairospy.runtime.config import ApplicationConfig, RuntimePaths
    from kairospy.runtime.profiles.simulation import paper_simulation_profile
    from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore

    profile = paper_simulation_profile(
        provider="workspace",
        dataset_hash=workspace_snapshot_hash,
        strategy_hash=strategy_hash,
        config_hash=config_hash,
        readiness_evidence=(ReadinessEvidence(
            "simulation",
            "pass",
            required_ports=("market", "reference", "execution", "account"),
            evidence_refs={"workspace_snapshot": workspace_snapshot_hash},
            connector_id="workspace",
        ),),
    )
    paths = RuntimePaths.under(directory / "runtime")
    application = KairosApplication(
        ApplicationConfig(Environment.PAPER, paths),
        SQLiteRuntimeStore(paths.runtime_database),
        runtime_id=run_id,
        probes=(FunctionProbe("workspace", lambda: (True, workspace_name)),),
    )
    launcher = RuntimeRunLauncher(
        application,
        RunKernel(profile),
        service_evidence_provider=ManagedServiceEvidenceProvider(
            _SurfaceRunServiceEvidence(),
            "surface-run-services",
        ),
    )
    request = RunRequest(
        run_id,
        RunMode.PAPER_TRADING,
        profile.profile_id,
        workspace_snapshot_hash,
        workspace_snapshot_hash,
        workspace_name,
        "workspace-entrypoint",
        strategy_hash,
        config_hash,
        datetime.now(timezone.utc),
        {"surface": "run start", "params": dict(params)},
    )
    launch = launcher.run(
        request,
        strategy_runner,
        artifact_writer_factory=lambda evidence: GovernanceRunArtifactWriter(
            RunArtifactRepository(directory / "governance-artifacts"),
            execution={"runtime_launch": evidence},
        ),
    )
    return dict(launch.evidence), launch.run_result


def _launch_workspace_live_run(
    config: object,
    directory: Path,
    run_id: str,
    workspace_name: str,
    workspace_snapshot_hash: str,
    strategy_hash: str,
    config_hash: str,
    params: dict[str, str],
    strategy_runner,
    *,
    run_config: object,
    workspace_snapshot: dict[str, object] | None = None,
    confirm_live: bool,
    supervise_live_services: bool = False,
) -> tuple[dict[str, object], object]:
    if not confirm_live:
        raise ValueError("live RunConfig start requires --confirm-live")
    if getattr(config, "get")("execution.live_trading_enabled", False) is not True:
        raise ValueError("live RunConfig start requires execution.live_trading_enabled = true")

    from kairospy.data.contracts import RunMode
    from kairospy.governance import GovernanceRunArtifactWriter, RunArtifactRepository
    from kairospy.environment import Environment
    from kairospy.runtime import (
        LiveRuntimeComponents,
        ManagedServiceEvidenceProvider,
        RunKernel,
        RunRequest,
        RuntimeRunLauncher,
        bind_live_runtime_components,
    )
    from kairospy.runtime.application import FunctionProbe, KairosApplication
    from kairospy.runtime.config import ApplicationConfig, RuntimePaths
    from kairospy.runtime.live_config import live_runtime_binding_config_from_run_config
    from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore

    live_config = live_runtime_binding_config_from_run_config(
        run_config,
        workspace_hash=workspace_snapshot_hash,
        strategy_hash=strategy_hash,
        config_hash=config_hash,
    )
    strategy_spec = _run_config_strategy_spec(run_config, getattr(config, "root", Path.cwd()), strategy_hash)
    paths = RuntimePaths.under(directory / "runtime")
    store = SQLiteRuntimeStore(paths.runtime_database)
    provider_binding = _live_provider_binding(config, run_config)
    market_binding = _live_market_binding(config, run_config, workspace_snapshot or {})
    supervise_market_services = supervise_live_services or _live_market_service_supervision_enabled(market_binding)
    reference_catalog = None
    provider_ports = None
    market_source = None
    accounts = ()
    recovery = None
    shutdown_handler = None
    if provider_binding.get("enabled") is True:
        from kairospy.integrations.live_ports import build_live_provider_ports, parse_account_ref

        account = parse_account_ref(_required_live_provider_binding(provider_binding, "account"))
        reference_catalog = _live_reference_catalog(config, provider_binding)
        provider_ports = build_live_provider_ports(
            config,
            provider=str(provider_binding.get("provider") or live_config.provider),
            execution_driver=str(provider_binding.get("execution_driver") or live_config.execution_driver),
            account=account,
            reference_catalog=reference_catalog,
            product=_optional_live_provider_binding(provider_binding, "product"),
            inverse=bool(provider_binding.get("inverse")),
        )
        accounts = (provider_ports.account,)
        recovery = live_config.runtime_recovery_service()
    if market_binding.get("enabled") is True:
        from kairospy.integrations.live_ports import build_live_market_event_source

        market_source = build_live_market_event_source(
            config,
            provider=str(market_binding.get("provider") or live_config.provider),
            name=_required_live_market_binding(market_binding, "name"),
            dataset=_required_live_market_binding(market_binding, "dataset"),
            live_view_id=_required_live_market_binding(market_binding, "live_view_id"),
            lake_root=_optional_live_market_binding(market_binding, "lake_root"),
            journal_root=_optional_live_market_binding(market_binding, "journal_root"),
        )
    if supervise_market_services and market_source is None:
        raise ValueError("live service supervision requires RunConfig bindings.live_views for the selected market")
    application = KairosApplication(
        ApplicationConfig(Environment.LIVE, paths),
        store,
        runtime_id=run_id,
        accounts=accounts,
        probes=(
            FunctionProbe("workspace", lambda: (True, workspace_name)),
            FunctionProbe("live-runtime-profile", lambda: (True, live_config.profile_id)),
        ),
        recovery=recovery,
    )
    if provider_ports is None and market_source is None:
        profile = live_config.bind()
    else:
        if provider_ports is None:
            raise ValueError("RunConfig live market binding requires live.bind_provider = true")
        if reference_catalog is None:
            reference_catalog = _live_reference_catalog(config, provider_binding)
        components = LiveRuntimeComponents(
            live_config,
            application,
            store,
            reference_catalog,
            provider_ports.execution_gateway,
            provider_ports.account_gateway,
            accounts=accounts,
            market_event_source=market_source.event_source if market_source is not None else provider_ports.market_event_source,
            order_recovery_gateway=provider_ports.order_recovery_gateway,
        )
        application.order_recovery = components.order_recovery_service()
        profile = bind_live_runtime_components(components)
        if strategy_spec is not None:
            stop_controller = components.stop_controller(strategy_spec)

            def shutdown_handler(reason: str):
                return stop_controller.execute(reason)
    service_evidence_provider = None
    market_services_supervised = bool(supervise_market_services and market_source is not None)
    managed_service_list = []
    if market_services_supervised and market_source is not None:
        managed_service_list.extend(market_source.managed_services)
    if provider_ports is not None:
        managed_service_list.append(components.outbox_dispatcher_service(run_id).managed_service())
        if supervise_market_services:
            managed_service_list.extend(
                service.managed_service()
                for service in components.reconciliation_monitor_services(run_id)
            )
    managed_services = tuple(managed_service_list)
    if not managed_services:
        service_evidence_provider = ManagedServiceEvidenceProvider(
            _SurfaceRunServiceEvidence(market_source.managed_services if market_source is not None else ()),
            "surface-live-run-services",
        )
    launcher = RuntimeRunLauncher(
        application,
        RunKernel(profile),
        service_evidence_provider=service_evidence_provider,
        managed_services=managed_services,
        service_evidence_binding_id="surface-live-run-services",
        shutdown_handler=shutdown_handler,
    )
    stop_policy = _strategy_stop_policy_metadata(
        strategy_spec,
        controller_bound=shutdown_handler is not None,
    )
    metadata = {
        "surface": "run start",
        "params": dict(params),
        "confirmed_live": True,
        "provider_binding": bool(provider_ports),
        "market_binding": bool(market_source),
        "market_services_supervised": market_services_supervised,
    }
    if stop_policy is not None:
        metadata["stop_policy"] = stop_policy
    request = RunRequest(
        run_id,
        RunMode.LIVE,
        profile.profile_id,
        workspace_snapshot_hash,
        workspace_snapshot_hash,
        workspace_name,
        "workspace-entrypoint",
        strategy_hash,
        config_hash,
        datetime.now(timezone.utc),
        metadata,
    )
    launch = launcher.run(
        request,
        strategy_runner,
        artifact_writer_factory=lambda evidence: GovernanceRunArtifactWriter(
            RunArtifactRepository(directory / "governance-artifacts"),
            execution={"runtime_launch": evidence},
        ),
    )
    evidence = dict(launch.evidence)
    stop_report = store.runtime_state("runtime_stop:last")
    if isinstance(stop_report, dict):
        evidence["stop_report"] = stop_report
    return evidence, launch.run_result


def _run_config_strategy_spec(run_config: object, project_root: Path, strategy_hash: str):
    resolver = getattr(run_config, "strategy_spec", None)
    if not callable(resolver):
        return None
    return resolver(project_root=project_root, default_evidence_hash=strategy_hash)


def _strategy_stop_policy_metadata(strategy_spec: object | None, *, controller_bound: bool) -> dict[str, object] | None:
    if strategy_spec is None:
        return None
    policy = getattr(strategy_spec, "default_stop_policy")
    return {
        "source": "strategy_spec",
        "strategy_id": str(getattr(strategy_spec, "strategy_id")),
        "strategy_version": str(getattr(strategy_spec, "version")),
        "strategy_spec_hash": str(getattr(strategy_spec, "spec_hash")),
        "policy_id": str(getattr(policy, "policy_id")),
        "controller_bound": controller_bound,
        "rules": tuple(
            {
                "reason": getattr(rule.reason, "value", str(rule.reason)),
                "action": getattr(rule.action, "value", str(rule.action)),
            }
            for rule in getattr(policy, "rules")
        ),
    }


def _live_provider_binding(config: object, run_config: object) -> dict[str, object]:
    bindings = _run_config_table(run_config, "bindings")
    live = _run_config_table(run_config, "live")
    if live.get("bind_provider") is not True and live.get("bind_ports") is not True:
        return {}
    account_name = str(bindings.get("account") or "")
    if not account_name.strip():
        return {}
    account = getattr(config, "get")(f"accounts.{account_name}", {})
    if not isinstance(account, dict):
        raise ValueError(f"RunConfig bindings.account references unknown account: {account_name}")
    products = account.get("allowed_products", ())
    product = products[0] if isinstance(products, list) and products else None
    return {
        "enabled": True,
        "provider": str(live.get("provider") or account.get("provider") or ""),
        "execution_driver": str(live.get("execution_driver") or ""),
        "account": str(account.get("account_ref") or account_name),
        "product": product,
        "reference_catalog_path": str(getattr(config, "relative_path")("paths.reference_catalog", ".kairos/data/reference/catalog.json")),
        "inverse": bool(live.get("inverse", False)),
    }


def _live_market_binding(config: object, run_config: object, workspace_snapshot: dict[str, object]) -> dict[str, object]:
    bindings = _run_config_table(run_config, "bindings")
    live = _run_config_table(run_config, "live")
    market_names = bindings.get("market", ())
    if not isinstance(market_names, list) or not market_names:
        return {}
    name = str(market_names[0])
    live_views = bindings.get("live_views", {})
    view = live_views.get(name) if isinstance(live_views, dict) else None
    workspace_bindings = workspace_snapshot.get("bindings", {}) if isinstance(workspace_snapshot, dict) else {}
    workspace_binding = workspace_bindings.get(name, {}) if isinstance(workspace_bindings, dict) else {}
    if not isinstance(view, dict):
        if not isinstance(workspace_binding, dict) or workspace_binding.get("kind") != "live_view":
            return {}
        view = {}
    dataset = str(view.get("dataset") or workspace_binding.get("dataset") or "")
    live_view_id = str(view.get("live_view_id") or workspace_binding.get("live_view_id") or "")
    if not live_view_id:
        return {}
    return {
        "enabled": True,
        "provider": str(view.get("provider") or live.get("provider") or ""),
        "name": name,
        "dataset": dataset,
        "live_view_id": live_view_id,
        "lake_root": view.get("lake_root"),
        "journal_root": view.get("journal_root"),
        "supervise_services": bool(view.get("supervise_services", view.get("supervise", False))),
    }


def _live_market_service_supervision_enabled(binding: dict[str, object]) -> bool:
    value = binding.get("supervise_services", binding.get("supervise"))
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "supervised"}


def _run_config_table(run_config: object, name: str) -> dict[str, object]:
    getter = getattr(run_config, "get", None)
    raw = getter(name, {}) if callable(getter) else {}
    if not isinstance(raw, dict):
        raise ValueError(f"RunConfig [{name}] must be a table")
    return dict(raw)


def _required_live_provider_binding(binding: dict[str, object], name: str) -> str:
    value = str(binding.get(name) or "")
    if not value.strip():
        raise ValueError(f"RunConfig live provider binding field is required: {name}")
    return value


def _optional_live_provider_binding(binding: dict[str, object], name: str) -> str | None:
    value = binding.get(name)
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _required_live_market_binding(binding: dict[str, object], name: str) -> str:
    value = str(binding.get(name) or "")
    if not value.strip():
        raise ValueError(f"RunConfig live market binding field is required: {name}")
    return value


def _optional_live_market_binding(binding: dict[str, object], name: str) -> str | None:
    value = binding.get(name)
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _live_reference_catalog(config: object, binding: dict[str, object]):
    from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT
    from kairospy.reference.repository import ReferenceCatalogRepository

    path_value = binding.get("reference_catalog_path")
    if path_value is None:
        path = getattr(config, "relative_path")("data.reference_catalog_path", f"{DEFAULT_LAKE_ROOT}/reference/catalog.json")
    else:
        path = Path(str(path_value)).expanduser()
        root = getattr(config, "root", Path.cwd())
        if not path.is_absolute():
            path = Path(root) / path
    if not path.exists():
        raise ValueError(f"RunConfig live provider reference catalog is missing: {path}")
    return ReferenceCatalogRepository(path).load()


class _SurfaceRunServiceEvidence:
    healthy = True

    def __init__(self, service_specs: tuple[object, ...] = ()) -> None:
        self.service_specs = tuple(service_specs)

    def snapshots(self):
        from kairospy.runtime import ManagedServiceSnapshot, ManagedServiceStatus

        return tuple(
            ManagedServiceSnapshot(
                spec.name,
                spec.criticality,
                ManagedServiceStatus.CREATED,
                0,
                0,
                None,
            )
            for spec in self.service_specs
        )


def run_live(args) -> dict[str, object]:
    action = str(getattr(args, "live_action", "status") or "status")
    if action not in {"start", "recover", "status", "stop", "kill-switch", "reset-kill-switch", "reload-risk-limits"}:
        raise ValueError(f"unsupported run live action: {action}")

    from kairospy.infrastructure.configuration import KairosProjectConfig, PROJECT_STATE_DIR
    from kairospy.infrastructure.storage.codec import to_primitive
    from kairospy.environment import Environment
    from kairospy.runtime import LiveRunDaemon, LiveRunRegistry, OperatorCommandBus, OperatorCommandType
    from kairospy.runtime.application import FunctionProbe, KairosApplication
    from kairospy.runtime.config import ApplicationConfig, RuntimePaths
    from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore

    config = KairosProjectConfig.discover(Path.cwd())
    raw_run_id = str(getattr(args, "run_id", None) or "")
    run_id = raw_run_id.strip()
    if not run_id:
        raise ValueError("run live requires --run-id")
    if raw_run_id != run_id or run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
        raise ValueError("run live --run-id must be a single path segment")
    runtime_root = config.root / PROJECT_STATE_DIR / "runtime" / "live" / run_id
    paths = RuntimePaths.under(runtime_root)
    if action == "status" and not paths.runtime_database.exists():
        return _run_live_status_payload(action, run_id, paths.runtime_database, None)
    store = SQLiteRuntimeStore(paths.runtime_database)

    if action == "status":
        state = store.runtime_state(f"{LiveRunDaemon.STATE_KEY_PREFIX}:{run_id}")
        stop_report = store.runtime_state("runtime_stop:last")
        live_binding = store.runtime_state(f"live_run_config:{run_id}")
        heartbeat = LiveRunRegistry(store).status(
            run_id,
            at=datetime.now(timezone.utc),
            stale_after_seconds=float(getattr(args, "stale_after_seconds", 5.0) or 5.0),
        )
        return _run_live_status_payload(
            action,
            run_id,
            paths.runtime_database,
            state if isinstance(state, dict) else None,
            stop_report if isinstance(stop_report, dict) else None,
            tuple(command.manifest() for command in OperatorCommandBus(store).commands(run_id, limit=5)),
            heartbeat,
            live_binding if isinstance(live_binding, dict) else None,
        )

    if action == "stop":
        application = KairosApplication(
            ApplicationConfig(Environment.LIVE, paths),
            store,
            runtime_id=run_id,
            probes=(FunctionProbe("live-run-config", lambda: (True, run_id)),),
        )
        daemon = LiveRunDaemon(application, (), run_id=run_id)
        snapshot = daemon.request_stop(
            str(getattr(args, "reason", None) or "operator stop requested"),
            actor=str(getattr(args, "actor", None) or "cli"),
        )
        stop_report = store.runtime_state("runtime_stop:last")
        commands = tuple(command.manifest() for command in OperatorCommandBus(store).commands(run_id, limit=5))
        return {
            **_run_live_snapshot_payload(action, snapshot, paths.runtime_database),
            "status": "stop_requested",
            "stop_requested": True,
            **({"stop_report": stop_report} if isinstance(stop_report, dict) else {}),
            "operator_commands": commands,
            **({"operator_command": commands[-1]} if commands else {}),
        }

    if action in {"kill-switch", "reset-kill-switch", "reload-risk-limits"}:
        command_type = {
            "kill-switch": OperatorCommandType.KILL_SWITCH,
            "reset-kill-switch": OperatorCommandType.RESET_KILL_SWITCH,
            "reload-risk-limits": OperatorCommandType.RELOAD_RISK_LIMITS,
        }[action]
        payload = {}
        if action == "reset-kill-switch":
            evidence = str(getattr(args, "reconciliation_evidence", None) or "")
            if not evidence.strip():
                raise ValueError("run live reset-kill-switch requires --reconciliation-evidence")
            payload["reconciliation_evidence"] = evidence
        if action == "reload-risk-limits":
            limits_hash = str(getattr(args, "risk_limits_hash", None) or "")
            if not limits_hash.strip():
                raise ValueError("run live reload-risk-limits requires --risk-limits-hash")
            payload["risk_limits_hash"] = limits_hash
        command = OperatorCommandBus(store).submit(
            run_id=run_id,
            command_type=command_type,
            payload=payload,
            actor=str(getattr(args, "actor", None) or "cli"),
            reason=str(getattr(args, "reason", None) or action),
            idempotency_key=None,
            at=datetime.now(timezone.utc),
        )
        return {
            "product": "run",
            "operation": "live",
            "live_action": action,
            "run_id": run_id,
            "status": "command_submitted",
            "runtime_database": str(paths.runtime_database),
            "operator_command": command.manifest(),
        }

    if not bool(getattr(args, "confirm_live", False)):
        raise ValueError("run live start/recover requires --confirm-live")
    if config.get("execution.live_trading_enabled", False) is not True:
        raise ValueError("run live start/recover requires execution.live_trading_enabled = true")
    live_binding = None
    if getattr(args, "config", None) is not None:
        daemon, live_binding = _run_live_daemon_from_run_config(config, args, paths, store, run_id)
    else:
        application = KairosApplication(
            ApplicationConfig(Environment.LIVE, paths),
            store,
            runtime_id=run_id,
            probes=(FunctionProbe("live-run-config", lambda: (True, run_id)),),
        )
        daemon = LiveRunDaemon(
            application,
            _run_live_managed_services(config, args, application, store, paths),
            run_id=run_id,
            stop_handler=_run_live_stop_handler(config, args, application, store, run_id),
        )
    duration = getattr(args, "duration_seconds", None)
    poll = float(getattr(args, "poll_seconds", 0.25) or 0.25)
    result = asyncio.run(_run_live_foreground_daemon(
        daemon,
        action,
        duration_seconds=float(duration) if duration is not None else None,
        poll_seconds=poll,
    ))
    return {
        **result,
        "runtime_database": str(paths.runtime_database),
        "state_key": daemon.state_key,
        "services": to_primitive(result.get("services", ())),
        **({"run_config": live_binding} if isinstance(live_binding, dict) else {}),
    }


def _run_live_managed_services(
    config: object,
    args: object,
    application: object,
    store: object,
    paths: object,
) -> tuple[object, ...]:
    override = getattr(args, "_managed_services", None)
    if override is not None:
        return tuple(override)
    raise ValueError("run live start/recover requires managed services from a RunConfig-backed live run")


def _run_live_daemon_from_run_config(
    config: object,
    args: object,
    paths: object,
    store: object,
    run_id: str,
) -> tuple[object, dict[str, object]]:
    from kairospy.data.contracts import RunMode
    from kairospy.environment import Environment
    from kairospy.governance import GovernanceRunArtifactWriter, RunArtifactRepository
    from kairospy.runtime import (
        LiveRunDaemon,
        LiveRunKernelService,
        LiveRuntimeComponents,
        BoundRunProfile,
        EventSourceRunEventProvider,
        RunKernel,
        RunRequest,
        bind_live_runtime_components,
    )
    from kairospy.runtime.application import FunctionProbe, KairosApplication
    from kairospy.runtime.config import ApplicationConfig
    from kairospy.runtime.live_config import live_runtime_binding_config_from_run_config
    from kairospy.runtime.run_config import load_run_config
    from kairospy.workspace import WorkspaceRepository

    run_config = load_run_config(getattr(args, "config"), project_root=getattr(config, "root", Path.cwd()))
    start_args = run_config.to_start_args(
        confirm_live=True,
        supervise_live_services=bool(getattr(args, "supervise_live_services", False)),
        param_overrides=tuple(getattr(args, "param", ()) or ()),
    )
    if start_args.mode != "live":
        raise ValueError("run live start/recover --config requires run.mode = \"live\"")

    workspace = WorkspaceRepository(getattr(config, "root", Path.cwd())).open(start_args.workspace)
    params = _parse_run_params(getattr(start_args, "param", ()))
    workspace_snapshot = workspace.snapshot()
    entrypoint_ref = str(start_args.entrypoint)
    module, callable_name, entrypoint = _load_run_entrypoint(entrypoint_ref, getattr(config, "root", Path.cwd()))
    _validate_workspace_entrypoint_requirements(module, entrypoint, workspace_snapshot)

    run_id = str(getattr(args, "run_id"))
    workspace_snapshot_hash = _stable_hash(_json_safe(workspace_snapshot))
    strategy_hash = _stable_hash({"entrypoint": entrypoint_ref, "module": module.__name__, "callable": callable_name})
    run_config_hash = _file_sha256(run_config.path)
    config_hash = _stable_hash({"params": params, "run_config_hash": run_config_hash})
    live_config = live_runtime_binding_config_from_run_config(
        run_config,
        workspace_hash=workspace_snapshot_hash,
        strategy_hash=strategy_hash,
        config_hash=config_hash,
    )
    strategy_spec = _run_config_strategy_spec(run_config, getattr(config, "root", Path.cwd()), strategy_hash)
    provider_binding = _live_provider_binding(config, run_config)
    market_binding = _live_market_binding(config, run_config, workspace_snapshot)
    reference_catalog = None
    provider_ports = None
    market_source = None
    accounts = ()
    recovery = None
    if provider_binding.get("enabled") is True:
        from kairospy.integrations.live_ports import build_live_provider_ports, parse_account_ref

        account = parse_account_ref(_required_live_provider_binding(provider_binding, "account"))
        reference_catalog = _live_reference_catalog(config, provider_binding)
        provider_ports = build_live_provider_ports(
            config,
            provider=str(provider_binding.get("provider") or live_config.provider),
            execution_driver=str(provider_binding.get("execution_driver") or live_config.execution_driver),
            account=account,
            reference_catalog=reference_catalog,
            product=_optional_live_provider_binding(provider_binding, "product"),
            inverse=bool(provider_binding.get("inverse")),
        )
        accounts = (provider_ports.account,)
        recovery = live_config.runtime_recovery_service()
    if market_binding.get("enabled") is True:
        from kairospy.integrations.live_ports import build_live_market_event_source

        market_source = build_live_market_event_source(
            config,
            provider=str(market_binding.get("provider") or live_config.provider),
            name=_required_live_market_binding(market_binding, "name"),
            dataset=_required_live_market_binding(market_binding, "dataset"),
            live_view_id=_required_live_market_binding(market_binding, "live_view_id"),
            lake_root=_optional_live_market_binding(market_binding, "lake_root"),
            journal_root=_optional_live_market_binding(market_binding, "journal_root"),
        )
    application = KairosApplication(
        ApplicationConfig(Environment.LIVE, paths),
        store,
        runtime_id=run_id,
        accounts=accounts,
        probes=(
            FunctionProbe("live-run-config", lambda: (True, str(run_config.path))),
            FunctionProbe("workspace", lambda: (True, workspace.name)),
            FunctionProbe("live-runtime-profile", lambda: (True, live_config.profile_id)),
        ),
        recovery=recovery,
    )
    components = None
    if provider_ports is None and market_source is None:
        profile = live_config.bind()
    elif provider_ports is None:
        profile = BoundRunProfile(
            live_config.to_live_profile(),
            live_config.binding_id,
            market_event_provider=EventSourceRunEventProvider(
                market_source.event_source,
                f"{live_config.binding_id}:market-events",
            ),
            recovery_handler=live_config.runtime_recovery_handler(),
        )
    else:
        if reference_catalog is None:
            reference_catalog = _live_reference_catalog(config, provider_binding)
        components = LiveRuntimeComponents(
            live_config,
            application,
            store,
            reference_catalog,
            provider_ports.execution_gateway,
            provider_ports.account_gateway,
            accounts=accounts,
            market_event_source=market_source.event_source if market_source is not None else provider_ports.market_event_source,
            order_recovery_gateway=provider_ports.order_recovery_gateway,
        )
        application.order_recovery = components.order_recovery_service()
        profile = bind_live_runtime_components(components)
    context = SimpleNamespace(
        run_id=run_id,
        workspace=workspace.name,
        mode="live",
        now=datetime.now(timezone.utc),
        data=workspace.data,
        params=params,
        state={},
    )

    def strategy_runner(_prepared: object):
        execution = _execute_workspace_strategy_entrypoint(entrypoint, workspace, params, context)
        return _strategy_run_result_from_workspace_execution(execution)

    stop_policy = _strategy_stop_policy_metadata(
        strategy_spec,
        controller_bound=components is not None and strategy_spec is not None,
    )
    strategy_id = getattr(strategy_spec, "strategy_id", workspace.name)
    strategy_version = getattr(strategy_spec, "version", "workspace-entrypoint")
    request = RunRequest(
        run_id,
        RunMode.LIVE,
        profile.profile_id,
        workspace_snapshot_hash,
        workspace_snapshot_hash,
        str(strategy_id),
        str(strategy_version),
        strategy_hash,
        config_hash,
        datetime.now(timezone.utc),
        {
            "surface": "run live",
            "params": dict(params),
            "run_config": str(run_config.path),
            "run_config_hash": run_config_hash,
            "provider_binding": bool(provider_ports),
            "market_binding": bool(market_source),
            "market_services_supervised": bool(market_source is not None and market_source.managed_services),
            **({"stop_policy": stop_policy} if stop_policy is not None else {}),
        },
    )
    artifact_writer = GovernanceRunArtifactWriter(
        RunArtifactRepository(Path(getattr(paths, "artifacts")) / "governance-artifacts"),
        execution={"runtime_launch": {"run_id": run_id, "daemon": True}},
    )
    service = LiveRunKernelService(
        store,
        RunKernel(profile),
        request,
        strategy_runner,
        artifact_writer=artifact_writer,
        clock=application.clock,
    )
    stop_handler = _run_live_stop_handler(config, args, application, store, run_id)
    if stop_handler is None and components is not None and strategy_spec is not None:
        stop_controller = components.stop_controller(strategy_spec)

        def stop_handler(reason: str = "manual"):
            return stop_controller.execute(reason)

    managed_services = (
        *((market_source.managed_services if market_source is not None else ())),
        *((components.outbox_dispatcher_service(run_id).managed_service(),) if components is not None else ()),
        *(tuple(
            monitor.managed_service()
            for monitor in components.reconciliation_monitor_services(run_id)
        ) if components is not None else ()),
        service.managed_service(),
    )
    binding = {
        "path": str(run_config.path),
        "hash": run_config_hash,
        "workspace": workspace.name,
        "profile_id": profile.profile_id,
        "provider_binding": bool(provider_ports),
        "market_binding": bool(market_source),
        "managed_service_names": tuple(getattr(item, "name", str(item)) for item in managed_services),
        **({"stop_policy": stop_policy} if stop_policy is not None else {}),
    }
    store.set_runtime_state(f"live_run_config:{run_id}", binding, datetime.now(timezone.utc))
    return LiveRunDaemon(application, managed_services, run_id=run_id, stop_handler=stop_handler), binding


def _run_live_stop_handler(config: object, args: object, application: object, store: object, run_id: str):
    handler = getattr(args, "_stop_handler", None)
    if handler is not None:
        return handler
    factory = getattr(args, "_stop_handler_factory", None)
    if factory is not None:
        return factory(application, store, run_id)
    return None


async def _run_live_foreground_daemon(
    daemon: object,
    action: str,
    *,
    duration_seconds: float | None,
    poll_seconds: float,
) -> dict[str, object]:
    from kairospy.runtime import OperatorCommandType

    if duration_seconds is not None and duration_seconds < 0:
        raise ValueError("run live --duration-seconds cannot be negative")
    if poll_seconds <= 0:
        raise ValueError("run live --poll-seconds must be positive")
    starter = daemon.recover if action == "recover" else daemon.start
    snapshot = await starter()
    if duration_seconds == 0:
        stopped = await daemon.stop(reason="scheduled")
        return {
            **_run_live_snapshot_payload(action, stopped, None),
            **_run_live_stop_report_payload(daemon),
            "started": _run_live_snapshot_payload(action, snapshot, None),
        }
    loop = asyncio.get_running_loop()
    deadline = None if duration_seconds is None else loop.time() + duration_seconds
    fault_task = asyncio.create_task(daemon.wait_for_critical_fault())
    try:
        while True:
            stop_command = daemon.claim_stop_command()
            if stop_command is not None:
                stop_command = daemon.command_bus.start(stop_command.command_id, daemon.clock.now())
            if stop_command is not None or daemon.stop_requested():
                try:
                    stopped = await daemon.stop(reason="manual")
                except Exception as error:
                    if stop_command is not None:
                        daemon.fail_operator_command(stop_command, error)
                    raise
                completed_command = (
                    daemon.complete_operator_command(stop_command, {
                        "phase": getattr(stopped.phase, "value", str(stopped.phase)),
                        "application_status": getattr(stopped.application_status, "value", str(stopped.application_status)),
                    })
                    if stop_command is not None else None
                )
                return {
                    **_run_live_snapshot_payload(action, stopped, None),
                    **_run_live_stop_report_payload(daemon),
                    "status": "stopped",
                    "stop_requested": True,
                    **({"operator_command": completed_command.manifest()} if completed_command is not None else {}),
                    "started": _run_live_snapshot_payload(action, snapshot, None),
                }
            operator_command = daemon.claim_operator_command(
                OperatorCommandType.KILL_SWITCH,
                OperatorCommandType.RESET_KILL_SWITCH,
                OperatorCommandType.RELOAD_RISK_LIMITS,
            )
            if operator_command is not None:
                running_command = daemon.command_bus.start(operator_command.command_id, daemon.clock.now())
                try:
                    command_result = _run_live_apply_operator_command(daemon, running_command)
                except Exception as error:
                    daemon.fail_operator_command(running_command, error)
                    raise
                daemon.complete_operator_command(running_command, command_result)
                daemon.heartbeat(
                    phase=command_result.get("phase"),
                    desired_state=str(command_result.get("desired_state") or "running"),
                    reason=running_command.reason,
                )
                continue
            if deadline is not None and loop.time() >= deadline:
                stopped = await daemon.stop(reason="scheduled")
                return {
                    **_run_live_snapshot_payload(action, stopped, None),
                    **_run_live_stop_report_payload(daemon),
                    "status": "stopped",
                    "stop_requested": False,
                    "started": _run_live_snapshot_payload(action, snapshot, None),
                }
            daemon.heartbeat(phase="running", desired_state="running")
            wait_seconds = poll_seconds if deadline is None else min(poll_seconds, max(0.0, deadline - loop.time()))
            done, _pending = await asyncio.wait({fault_task}, timeout=wait_seconds)
            if done:
                fault, fault_snapshot = fault_task.result()
                await daemon.stop(reason="crash")
                return {
                    **_run_live_snapshot_payload(action, fault_snapshot, None),
                    **_run_live_stop_report_payload(daemon),
                    "status": "reduce_only",
                    "fault": {
                        "task_name": fault.task_name,
                        "error_type": fault.error_type,
                        "message": fault.message,
                        "attempt": fault.attempt,
                    },
                    "started": _run_live_snapshot_payload(action, snapshot, None),
                }
    finally:
        if not fault_task.done():
            fault_task.cancel()


def _run_live_apply_operator_command(daemon: object, command: object) -> dict[str, object]:
    from kairospy.governance.kill_switch import KillSwitch
    from kairospy.runtime import OperatorCommandType
    from kairospy.runtime.application import RuntimeStatus

    command_type = OperatorCommandType(getattr(command, "command_type"))
    switch = KillSwitch((), getattr(daemon, "clock", None), daemon.application.store)
    if command_type is OperatorCommandType.KILL_SWITCH:
        result = switch.trigger(tuple(daemon.application.accounts), str(getattr(command, "reason")))
        if daemon.application.status in {
            RuntimeStatus.READY,
            RuntimeStatus.RUNNING,
            RuntimeStatus.DEGRADED,
            RuntimeStatus.REDUCE_ONLY,
        }:
            daemon.application.degrade(str(getattr(command, "reason")), reduce_only=True)
        snapshot = daemon.mark_reduce_only(str(getattr(command, "reason")))
        return {
            "phase": snapshot.phase.value,
            "desired_state": "reduce_only",
            "triggered_at": result.triggered_at.isoformat(),
            "cancelled_orders": result.cancelled_orders,
            "failures": result.failures,
        }
    if command_type is OperatorCommandType.RESET_KILL_SWITCH:
        payload = dict(getattr(command, "payload", {}) or {})
        evidence = str(payload.get("reconciliation_evidence") or "")
        if not evidence.strip():
            raise ValueError("reset kill switch requires reconciliation_evidence")
        switch.reset(
            actor=str(getattr(command, "actor")),
            reason=str(getattr(command, "reason")),
            evidence={"reconciliation_evidence": evidence},
        )
        if daemon.application.status is RuntimeStatus.REDUCE_ONLY:
            daemon.application.clear_reduce_only(str(getattr(command, "reason")))
        snapshot = daemon.mark_running(str(getattr(command, "reason")))
        return {
            "phase": snapshot.phase.value,
            "desired_state": "running",
            "reset_evidence": {"reconciliation_evidence": evidence},
        }
    if command_type is OperatorCommandType.RELOAD_RISK_LIMITS:
        payload = dict(getattr(command, "payload", {}) or {})
        limits_hash = str(payload.get("risk_limits_hash") or "")
        if not limits_hash.strip():
            raise ValueError("reload risk limits requires risk_limits_hash")
        at = daemon.clock.now()
        daemon.application.store.set_runtime_state(
            "risk_runtime:last",
            {
                "run_id": daemon.run_id,
                "status": "ok",
                "limits_hash": limits_hash,
                "actor": str(getattr(command, "actor")),
                "reason": str(getattr(command, "reason")),
                "updated_at": at.isoformat(),
            },
            at,
        )
        snapshot = daemon.mark_running(str(getattr(command, "reason")))
        return {
            "phase": snapshot.phase.value,
            "desired_state": "running",
            "risk_limits_hash": limits_hash,
        }
    raise ValueError(f"unsupported operator command: {command_type.value}")


def _run_live_status_payload(
    action: str,
    run_id: str,
    runtime_database: Path,
    state: dict[str, object] | None,
    stop_report: dict[str, object] | None = None,
    operator_commands: tuple[dict[str, object], ...] = (),
    heartbeat: dict[str, object] | None = None,
    live_binding: dict[str, object] | None = None,
) -> dict[str, object]:
    if state is None:
        payload = {
            "product": "run",
            "operation": "live",
            "live_action": action,
            "run_id": run_id,
            "status": "not_started",
            "phase": "created",
            "runtime_database": str(runtime_database),
            "state_key": f"live_run_daemon:{run_id}",
        }
        if stop_report is not None:
            payload["stop_report"] = stop_report
        if operator_commands:
            payload["operator_commands"] = operator_commands
        if heartbeat is not None:
            payload["heartbeat"] = heartbeat
            payload["status"] = str(heartbeat.get("status", payload["status"]))
        if live_binding is not None:
            payload["run_config"] = live_binding
        return payload
    status = "unknown"
    if heartbeat is not None:
        status = str(heartbeat.get("status", status))
    payload = {
        "product": "run",
        "operation": "live",
        "live_action": action,
        "run_id": run_id,
        "status": status,
        "phase": str(state.get("phase", "unknown")),
        "application_status": state.get("application_status"),
        "reason": state.get("reason"),
        "stop_requested": bool(state.get("stop_requested")),
        "snapshot_hash": state.get("snapshot_hash"),
        "runtime_database": str(runtime_database),
        "state_key": f"live_run_daemon:{run_id}",
        "services": state.get("services", ()),
    }
    if stop_report is not None:
        payload["stop_report"] = stop_report
    if operator_commands:
        payload["operator_commands"] = operator_commands
        latest = operator_commands[-1]
        if latest.get("command_type") == "stop" and latest.get("status") not in {"succeeded", "failed", "rejected", "expired"}:
            payload["stop_requested"] = True
            payload["operator_command"] = latest
    if heartbeat is not None:
        payload["heartbeat"] = heartbeat
    if live_binding is not None:
        payload["run_config"] = live_binding
    return payload


def _run_live_stop_report_payload(daemon: object) -> dict[str, object]:
    store = getattr(getattr(daemon, "application", None), "store", None)
    if store is None or not hasattr(store, "runtime_state"):
        return {}
    stop_report = store.runtime_state("runtime_stop:last")
    if not isinstance(stop_report, dict):
        return {}
    return {"stop_report": stop_report}


def _run_live_snapshot_payload(
    action: str,
    snapshot: object,
    runtime_database: Path | None,
) -> dict[str, object]:
    from kairospy.infrastructure.storage.codec import to_primitive

    payload = {
        "product": "run",
        "operation": "live",
        "live_action": action,
        "run_id": str(getattr(snapshot, "run_id")),
        "status": str(getattr(snapshot, "phase").value),
        "phase": str(getattr(snapshot, "phase").value),
        "application_status": str(getattr(snapshot, "application_status").value),
        "reason": str(getattr(snapshot, "reason")),
        "recovery_ready": getattr(snapshot, "recovery_ready"),
        "order_recovery_complete": getattr(snapshot, "order_recovery_complete"),
        "updated_at": getattr(snapshot, "updated_at").isoformat(),
        "snapshot_hash": getattr(snapshot, "snapshot_hash"),
        "services": to_primitive(getattr(snapshot, "services")),
    }
    if runtime_database is not None:
        payload["runtime_database"] = str(runtime_database)
    return payload


def _run_component_hash(value: object) -> str:
    from kairospy.infrastructure.storage.codec import to_primitive

    return sha256(json.dumps(
        to_primitive(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()


def _file_sha256(path: str | Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def run_start(args) -> dict[str, object]:
    from kairospy.infrastructure.configuration import KairosProjectConfig
    from kairospy.runtime.run_config import load_run_config

    config = KairosProjectConfig.discover(Path.cwd())
    run_config = load_run_config(getattr(args, "config"), project_root=config.root)
    start_args = run_config.to_start_args(
        confirm_live=bool(getattr(args, "confirm_live", False)),
        supervise_live_services=bool(getattr(args, "supervise_live_services", False)),
        param_overrides=tuple(getattr(args, "param", ()) or ()),
    )
    return _run_start_workspace_entrypoint(start_args)


def run_config(args) -> dict[str, object]:
    from kairospy.infrastructure.configuration import KairosProjectConfig
    from kairospy.runtime.run_config import load_run_config

    project_config = KairosProjectConfig.discover(Path.cwd())
    loaded = load_run_config(getattr(args, "path"), project_root=project_config.root)
    payload = {
        "product": "run",
        "operation": f"config.{getattr(args, 'config_action')}",
        **loaded.explain(),
    }
    if getattr(args, "config_action") == "validate":
        return payload
    if getattr(args, "config_action") == "explain":
        loaded.require_valid()
        return payload
    raise ValueError(f"unsupported run config action: {getattr(args, 'config_action', None)!r}")


def run_live_data_preflight(args) -> dict[str, object]:
    mode = str(args.action)
    if mode not in {"paper", "shadow"}:
        raise ValueError(f"live Dataset preflight is not supported for run mode: {mode}")
    bindings = tuple(
        resolve_live_dataset_subscription(
            args.lake_root,
            name=name,
            dataset_id=dataset,
            policy=PAPER_LIVE_FRESHNESS_POLICY,
        ).to_primitive()
        for name, dataset in _run_data_bindings(getattr(args, "data", ()))
    )
    composition = paper_trading_composition("binance")
    feed_plan = runtime_feed_plan("paper", bindings)
    execution_plan = runtime_execution_plan("paper", composition) if mode == "paper" else None
    return {
        "product": "run",
        "operation": mode,
        "status": "ready_for_paper" if mode == "paper" else "ready_for_shadow",
        "strategy": str(getattr(args, "strategy", "")),
        "mode": "paper-trading" if mode == "paper" else "shadow",
        "data_inputs": [
            _user_live_binding_payload(item)
            for item in bindings
        ],
        "runtime_contract": {
            "run_mode_composition": {**composition.manifest(), "composition_hash": composition.composition_hash},
            "feed_bindings": [_user_live_binding_payload(item) for item in bindings],
            "freshness_gates": [
                {
                    "name": item["name"],
                    "dataset": item["dataset"],
                    **_user_freshness_gate_payload(item["freshness_gate"], dataset=str(item["dataset"])),
                }
                for item in bindings
            ],
            "feed_runtime_plan": _user_feed_runtime_plan_payload(feed_plan),
            **({"execution_runtime_plan": {
                **execution_plan.manifest(), "plan_hash": execution_plan.plan_hash,
            }} if execution_plan else {}),
        },
        "will_execute": False,
    }


def _user_live_binding_payload(item: dict[str, object]) -> dict[str, object]:
    return {
        "name": item["name"],
        "dataset": item["dataset"],
        "transport": item.get("transport"),
        "freshness_gate": _user_freshness_gate_payload(item.get("freshness_gate"), dataset=str(item["dataset"])),
    }


def _user_freshness_gate_payload(value: object, *, dataset: str) -> dict[str, object]:
    gate = dict(value) if isinstance(value, dict) else {}
    gate.pop("live_view_id", None)
    policy = str(gate.get("policy") or "freshness")
    if gate.get("passed"):
        gate["reason"] = f"Dataset {dataset} satisfies {policy}"
    elif gate.get("channel_failures"):
        gate["reason"] = f"Dataset {dataset} channel diagnostics failed: {', '.join(str(item) for item in gate['channel_failures'])}"
    elif gate.get("freshness_status"):
        gate["reason"] = f"Dataset {dataset} freshness status {gate['freshness_status']!r} does not satisfy {policy}"
    return gate


def _user_feed_runtime_plan_payload(feed_plan) -> dict[str, object]:
    manifest = feed_plan.manifest()
    services = []
    for service in manifest.get("services", ()):
        if not isinstance(service, dict):
            continue
        services.append({
            "name": service.get("name"),
            "dataset": service.get("dataset"),
            "capture_policy": service.get("capture_policy"),
        })
    return {
        "mode": manifest.get("mode"),
        "services": services,
        "plan_hash": feed_plan.plan_hash,
    }


def _run_data_bindings(values: Iterable[str]) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for raw in values:
        if "=" not in str(raw):
            raise ValueError("run --data must use name=dataset")
        name, dataset = str(raw).split("=", 1)
        name = name.strip()
        dataset = dataset.strip()
        if not name or not dataset:
            raise ValueError("run --data must use non-empty name=dataset")
        result.append((name, dataset))
    if not result:
        raise ValueError("run paper/shadow requires at least one --data name=dataset binding")
    return tuple(result)


def run_inspect(args) -> dict[str, object]:
    manifest = _load_run_manifest(args.lake_root, args.run_id)
    return {**manifest, "artifact": str(_find_run_manifest(args.lake_root, args.run_id))}


def run_replay(args) -> dict[str, object]:
    path = _find_run_manifest(args.lake_root, args.run_id)
    if path is None:
        raise FileNotFoundError(args.run_id)
    manifest = _read_json(path)
    snapshot_path = path.with_name("workspace_snapshot.json")
    if not snapshot_path.exists():
        snapshot_path = path.with_name("snapshot.json")
    snapshot = _read_json(snapshot_path)
    snapshot_hash = str(snapshot.get("lock_hash") or _stable_hash(snapshot))
    expected = str(manifest.get("workspace", {}).get("snapshot_hash") or manifest.get("target", {}).get("hash") or "")
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
        "same_target": (
            first.get("target") == second.get("target")
            if "target" in first or "target" in second
            else first.get("workspace") == second.get("workspace")
            and first.get("strategy") == second.get("strategy")
        ),
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


def _register_written_release(
    root: Path,
    dataset_id: str,
    release_id: str,
    directory: Path,
    content_hash: str,
    primary_time: str,
    file_format: str,
) -> None:
    layer = _dataset_layer(dataset_id)
    product = DataProductDefinition(
        DatasetKey(dataset_id),
        dataset_id,
        layer,
        description="User-written Data Product release",
        dimensions={"source": "user-write"},
        primary_time=primary_time,
        sources=(SourceBinding("user-write", None, 100, QualityLevel.WORKSPACE, ("file",)),),
        owner="user",
    )
    spec = DataProductContract(
        product,
        str(directory.parent.relative_to(root)),
        f"{dataset_id}.contract",
        storage_kind=DatasetStorageKind.TABULAR,
        quality_profile="generic",
        minimum_publication_level=QualityLevel.WORKSPACE,
    )
    catalog = DataCatalog(root)
    catalog.register_product_spec(spec, enrich=True)
    try:
        existing = catalog.release(release_id)
    except KeyError:
        existing = None
    if existing is not None:
        if (
            str(existing.product_key) == dataset_id
            and existing.content_hash == content_hash
            and existing.relative_path == str(directory.relative_to(root))
        ):
            catalog.save()
            return
        raise ValueError(f"immutable dataset release conflicts with existing content: {release_id}")
    catalog.register_release(DatasetRelease(
        release_id,
        product.key,
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        spec.schema_id,
        "1",
        "data.write",
        "1",
        str(directory.relative_to(root)),
        file_format,
        content_hash,
        "user-write",
        None,
        (),
        DatasetStatus.APPROVED_FOR_WORKSPACE,
        QualityLevel.WORKSPACE,
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
    root_path = Path(root)
    candidates = [
        root_path / ".kairos" / "run" / run_id / "manifest.json",
        root_path.parent / "run" / run_id / "manifest.json",
    ]
    try:
        from kairospy.infrastructure.configuration import KairosProjectConfig, PROJECT_STATE_DIR

        project = KairosProjectConfig.discover(Path.cwd()).root
        candidates.append(project / PROJECT_STATE_DIR / "run" / run_id / "manifest.json")
    except Exception:
        pass
    for candidate in candidates:
        if candidate.exists():
            return candidate
    matches = list((root_path / "runs").glob(f"*/{run_id}/manifest.json"))
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
