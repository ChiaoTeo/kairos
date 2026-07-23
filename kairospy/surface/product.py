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
import subprocess
import sys
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any, Iterable

from kairospy.runtime import paper_trading_composition, runtime_execution_plan, runtime_feed_plan
from kairospy.data.catalog import DataCatalog
from kairospy.data.contracts import (
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
    QualityLevel,
    SourceBinding,
    data_release_ref,
    stable_artifact_hash,
)
from kairospy.data.quality.freshness import (
    PAPER_LIVE_FRESHNESS_POLICY,
    live_view_manifest_path,
    resolve_live_dataset_subscription,
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
        return self._read(columns, OutputFormat.ARROW)

    def pandas(self, *, columns: tuple[str, ...] | list[str] | None = None):
        from kairospy.data import OutputFormat
        return self._read(columns, OutputFormat.PANDAS)

    def rows(self, *, columns: tuple[str, ...] | list[str] | None = None) -> list[dict[str, object]]:
        from kairospy.data import OutputFormat
        return self._read(columns, OutputFormat.ROWS)

    def _read(self, columns, output):
        from kairospy.data import DatasetClient

        return DatasetClient(self._root).read(
            str(self["dataset"]),
            columns=tuple(columns) if columns is not None else None,
            output=output,
        )


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
        message=f"Dataset {dataset_id} does not exist.",
        why="This command reads the Dataset Store directly and needs a Dataset directory or alias.",
        next_command="kairospy data start",
    )


def _historical_not_configured_error(operation: str, dataset_id: str) -> DataDatasetInputError:
    return DataDatasetInputError(
        operation,
        dataset_id,
        code="historical_not_configured",
        status="needs_data",
        message=f"Dataset {dataset_id} has no historical data files.",
        why="This command needs files under the Dataset data/ directory. Live-only Datasets can expose a live path but cannot be queried as history.",
        next_command=f"kairospy data add <file> --name {dataset_id}",
    )


@dataclass(frozen=True, slots=True)
class Data:
    """Data product entrypoint for setup, readiness checks, and dataset consumption."""

    root: str | Path = "data"

    def reader(self):
        from kairospy.data import DatasetClient

        return DatasetClient(self.root)

    def read(self, dataset: str, **query: object):
        return self.reader().read(dataset, **query)

    def live(self, dataset: str, *, view: str = "default"):
        return self.reader().live(dataset, view=view)

    def alias(self, dataset: str, alias: str) -> dict[str, object]:
        from kairospy.data import DatasetStore

        path = DatasetStore(self.root).alias(dataset, alias)
        return {
            "product": "data",
            "operation": "alias",
            "dataset": dataset,
            "alias": alias,
            "path": str(path),
        }

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

    def connect(self, source: str | Path, *, as_dataset: str | None = None, time: str = "timestamp",
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
        return InputTableRef(self.root, {
            "product": "data",
            "operation": "dataset",
            "dataset": name,
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
        workspace: str | None = None,
        strategy: str | None = None,
        mode: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, object]:
        return run_start(_args(
            self.root,
            workspace=workspace,
            strategy=strategy,
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
    dataset_id = ensure_sma_tutorial_dataset(args.lake_root)
    data_path = Path(args.lake_root) / "datasets" / Path(*str(dataset_id).split(".")) / "data"
    files = sorted(path for path in data_path.rglob("*.parquet"))
    report = {
        "product": "data",
        "operation": "download",
        "key": key,
        "dataset": str(dataset_id),
        "files": len(files),
        "artifact": str(data_path),
        "artifact_ref": f"data://{dataset_id}",
        "contract": "DatasetStore",
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
            "template": "kairospy data start --kind product --product massive.equity.ohlcv.1d --start 2024-01-01T00:00:00Z --end 2024-02-01T00:00:00Z",
        },
        {
            "kind": "live",
            "description": "live source or LiveDataProtocol connector",
            "template": "kairospy data start --kind live --source binance.quote --account binance-testnet --instrument BTCUSDT --channel quote --for paper",
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
    if kind == "live" and source is not None and Path(source).exists() and not dataset:
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
        if not product:
            return None
        parts = ["kairospy", "data", "use", str(product)]
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
        if source is None:
            return None
        parts = ["kairospy", "data", "connect", str(source)]
        if Path(source).exists() and dataset is not None:
            parts.extend(["--as", dataset])
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
    from kairospy.data.acquisition.historical_service import HistoricalDataService

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
    from kairospy.data.acquisition.historical_service import HistoricalDataService

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
    from kairospy.data.live.services import LiveDataService

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

    from kairospy.data import BuiltInDataProductRegistry, LiveDataRequest, built_in_dataset_id, default_builtin_protocol_registry

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
    dataset_id = built_in_dataset_id(
        built_in,
        instruments=tuple(getattr(args, "instrument", ()) or ()),
        params=_live_source_params(args),
    )
    requested_dataset = str(getattr(args, "as_dataset", None) or "").strip()
    if requested_dataset and requested_dataset != dataset_id:
        raise ValueError("built-in data products use canonical Dataset IDs; create an alias after sampling instead")
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
    return _dataset_store_status(Path(args.lake_root), _dataset_argument(args), operation="doctor")


def data_list(args) -> dict[str, object]:
    from kairospy.data import DatasetStore

    root = Path(args.lake_root)
    store = DatasetStore(root)
    rows = [
        _dataset_store_status(root, str(dataset), operation="list")
        for dataset in store.list_datasets()
    ]
    return {
        "product": "data",
        "operation": "list",
        "datasets": rows,
        "aliases": store.aliases(),
    }


def data_metadata(args) -> dict[str, object]:
    root = Path(args.lake_root)
    dataset = _dataset_argument(args)
    override_time = str(getattr(args, "time", "") or "").strip()
    if override_time:
        return _override_dataset_primary_time(root, dataset, override_time)

    from kairospy.data import DatasetReader, DatasetStore

    store = DatasetStore(root)
    dataset_id = store.resolve(dataset)
    dataset_path = store.dataset_path(dataset_id)
    if not dataset_path.exists():
        raise _dataset_not_found_error("metadata", dataset)
    metadata = _read_optional_json(store.layout.dataset_json_path(dataset_id))
    data_files = _dataset_data_files(store, dataset_id)
    live_views = _live_view_metadata_payloads(root, str(dataset_id))
    fields = [str(item) for item in metadata.get("fields") or ()]
    row_count: int | None = None
    if data_files:
        try:
            table = DatasetReader(store).read(dataset_id, output="arrow")
            fields = list(table.column_names) or fields
            row_count = table.num_rows
        except Exception:
            row_count = None
    primary_time = _dataset_primary_time(metadata, fields, live_views)
    return {
        "product": "data",
        "operation": "metadata",
        "dataset": str(dataset_id),
        "path": str(dataset_path),
        "source_kind": _dataset_source_kind(metadata, live_views),
        "time": primary_time,
        "historical": {
            "configured": bool(data_files),
            "status": "ready" if data_files else "not_configured",
            "data_root": str(store.data_path(dataset_id)),
            "file_count": len(data_files),
            "schema": {
                "primary_time": primary_time,
                "fields": fields,
            },
            "coverage": {
                "row_count": row_count,
                "boundary": "[start,end)",
            },
        },
        "live": {
            "configured": bool(live_views),
            "views": live_views,
        },
    }


def _override_dataset_primary_time(root: Path, dataset: str, primary_time: str) -> dict[str, object]:
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
    from kairospy.data import DatasetStore

    store = DatasetStore(root)
    dataset_id = store.resolve(dataset)
    if not store.dataset_path(dataset_id).exists():
        raise _dataset_not_found_error("metadata", dataset)
    known_fields = _dataset_known_fields(store, dataset_id)
    if known_fields and primary_time not in known_fields:
        raise DataDatasetInputError(
            "metadata",
            str(dataset_id),
            code="time_field_not_found",
            status="needs_input",
            message=f"Time field {primary_time} is not present in Dataset {dataset_id}.",
            why="Metadata override can only point at a field that exists in the Dataset schema.",
            next_command=f"kairospy data metadata {dataset} --time <field>",
        )
    metadata_path = store.layout.dataset_json_path(dataset_id)
    metadata = _read_optional_json(metadata_path)
    metadata.update({"dataset": str(dataset_id), "primary_time": primary_time})
    if known_fields:
        metadata["fields"] = known_fields
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    payload = data_metadata(_args(root, dataset=str(dataset_id)))
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
    return _override_dataset_primary_time(root, dataset, primary_time)


def _metadata_historical_fields(root: Path, spec: DataProductContract) -> list[str]:
    from kairospy.data import DatasetStore

    store = DatasetStore(root)
    try:
        return _dataset_known_fields(store, str(spec.key))
    except Exception:
        return []


def data_validate(args) -> dict[str, object]:
    root = Path(args.lake_root)
    dataset = _dataset_argument(args)
    status = _dataset_store_status(root, dataset, operation="validate")
    passed = status["status"] == "ready"
    return {
        "product": "data",
        "operation": "validate",
        "dataset": status["dataset"],
        "status": "passed" if passed else "needs_data",
        "ready_for": status["ready_for"],
        "blocked_for": status["blocked_for"],
        "issues": status["issues"],
        "checks": [
            {
                "name": "dataset_directory",
                "passed": True,
                "severity": "diagnostic",
                "requirement": "Dataset directory exists",
                "value": status["path"],
            },
            {
                "name": "historical_or_live_data",
                "passed": passed,
                "severity": "diagnostic",
                "requirement": "Dataset has files under data/ or a configured live view",
                "value": status["status"],
            },
        ],
    }


def data_replay(args) -> dict[str, object]:
    from kairospy.data import DatasetClient, OutputFormat
    from kairospy.infrastructure.storage.codec import to_primitive

    root = Path(args.lake_root)
    dataset = _dataset_argument(args)
    metadata = data_metadata(_args(root, dataset=dataset))
    time_field = str(metadata.get("time") or "")
    try:
        rows = list(DatasetClient(root).read(
            dataset,
            start=getattr(args, "start", None),
            end=getattr(args, "end", None),
            instruments=tuple(getattr(args, "instrument", ()) or ()),
            columns=tuple(getattr(args, "field", ()) or ()) or None,
            output=OutputFormat.ROWS,
            time_field=time_field or None,
        ))
    except FileNotFoundError as error:
        if metadata.get("live", {}).get("configured"):
            raise _historical_not_configured_error("replay", dataset) from error
        raise _dataset_not_found_error("replay", dataset) from error
    rows = _sort_replay_rows(rows, time_field)
    limit = int(getattr(args, "limit", 20) or 20)
    if limit <= 0:
        raise ValueError("data replay --limit must be positive")
    return {
        "product": "data",
        "operation": "replay",
        "dataset": str(metadata.get("dataset") or dataset),
        "time": time_field or None,
        "window": {
            "start": getattr(args, "start", None),
            "end": getattr(args, "end", None),
            "boundary": "[start,end)",
        },
        "replay": {
            "source": "dataset_store",
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


def _dataset_store_status(root: Path, dataset: str, *, operation: str) -> dict[str, object]:
    from kairospy.data import DatasetStore

    store = DatasetStore(root)
    dataset_id = store.resolve(dataset)
    dataset_path = store.dataset_path(dataset_id)
    if not dataset_path.exists():
        raise _dataset_not_found_error(operation, dataset)
    metadata = _read_optional_json(store.layout.dataset_json_path(dataset_id))
    data_files = _dataset_data_files(store, dataset_id)
    live_views = _live_view_metadata_payloads(root, str(dataset_id))
    fields = [str(item) for item in metadata.get("fields") or ()]
    if not fields:
        fields = _dataset_known_fields(store, dataset_id)
    primary_time = _dataset_primary_time(metadata, fields, live_views)
    ready_for: list[str] = []
    if data_files:
        ready_for.append("read")
    if live_views:
        ready_for.append("live")
    issues = [] if ready_for else ["empty_dataset"]
    return {
        "product": "data",
        "operation": operation,
        "dataset": str(dataset_id),
        "path": str(dataset_path),
        "status": "ready" if ready_for else "needs_data",
        "source_kind": _dataset_source_kind(metadata, live_views),
        "time": primary_time,
        "ready_for": ready_for,
        "blocked_for": [item for item in ("read", "live") if item not in ready_for],
        "issues": issues,
        "historical": {
            "status": "ready" if data_files else "not_configured",
            "ready_for": ["read"] if data_files else [],
            "blocked_for": [] if data_files else ["read"],
            "issues": [],
            "file_count": len(data_files),
            "data_root": str(store.data_path(dataset_id)),
        },
        "live": {
            "status": "configured" if live_views else "not_configured",
            "ready_for": ["live"] if live_views else [],
            "blocked_for": [] if live_views else ["live"],
            "issues": [],
            "views": live_views,
            "live_root": str(store.live_path(dataset_id)),
        },
    }


def _dataset_data_files(store, dataset: object) -> list[Path]:
    data_root = store.data_path(dataset)
    if not data_root.exists():
        return []
    return sorted(
        path
        for pattern in ("**/*.parquet", "**/*.csv")
        for path in data_root.glob(pattern)
        if path.is_file() and not _dataset_relative_tmp(path, data_root)
    )


def _dataset_relative_tmp(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(part == "tmp" or part.startswith(".tmp") for part in parts)


def _dataset_known_fields(store, dataset: object) -> list[str]:
    metadata = _read_optional_json(store.layout.dataset_json_path(store.resolve(dataset)))
    fields = [str(item) for item in metadata.get("fields") or ()]
    if fields:
        return fields
    if _dataset_data_files(store, dataset):
        try:
            from kairospy.data import DatasetReader

            return list(DatasetReader(store).read(dataset, output="arrow").column_names)
        except Exception:
            return []
    live_fields = sorted({
        str(field)
        for view in _live_view_metadata_payloads(store.root, str(store.resolve(dataset)))
        for field in view.get("fields", ())
    })
    return live_fields


def _dataset_primary_time(
    metadata: dict[str, object],
    fields: Iterable[str],
    live_views: Iterable[dict[str, object]],
) -> str | None:
    configured = str(metadata.get("primary_time") or "").strip()
    if configured:
        return configured
    for view in live_views:
        value = str(view.get("primary_time") or "").strip()
        if value:
            return value
    field_set = {str(field) for field in fields}
    for candidate in ("event_time", "timestamp", "period_start", "available_time", "time", "date"):
        if candidate in field_set:
            return candidate
    return None


def _dataset_source_kind(metadata: dict[str, object], live_views: Iterable[dict[str, object]]) -> str | None:
    source = metadata.get("source") if isinstance(metadata.get("source"), dict) else {}
    value = str(source.get("source_kind") or "").strip()
    if value:
        return value
    for view in live_views:
        view_source = view.get("source") if isinstance(view.get("source"), dict) else {}
        value = str(view_source.get("source_kind") or "").strip()
        if value:
            return value
    return None


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
    from kairospy.data import DatasetStore

    store = DatasetStore(root)
    try:
        dataset_id = store.resolve(dataset)
    except ValueError:
        return []
    directory = store.live_path(dataset_id)
    payloads = []
    for path in sorted(directory.glob("*/state.json")):
        state = _read_optional_json(path)
        if str(state.get("dataset") or dataset_id) != str(dataset_id):
            continue
        plane = state.get("live_data_plane") if isinstance(state.get("live_data_plane"), dict) else {}
        freshness = plane.get("freshness") if isinstance(plane.get("freshness"), dict) else {}
        source = state.get("source") if isinstance(state.get("source"), dict) else {}
        payloads.append({
            "view": path.parent.name,
            "path": str(path.parent),
            "status": state.get("status") or "configured",
            "primary_time": state.get("primary_time"),
            "fields": list(state.get("fields") or ()),
            "freshness_policy": {
                "name": "configured",
                "max_age_seconds": freshness.get("max_age_seconds"),
                "status": state.get("status") or "configured",
                "passed": True,
                "channel_failures": [],
            },
            "source": {
                key: value
                for key, value in source.items()
                if key in {"name", "source_kind", "provider", "venue", "channel", "instrument_id", "stream", "market"}
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
    from kairospy.data.live.services import LiveDataService

    return LiveDataService(args.lake_root).reconnect(args)


def _latest_live_view_manifest(root: Path, dataset: str):
    from kairospy.data.quality.freshness import load_live_view_manifest

    directory = root / "live-views" / dataset.replace(".", "/")
    candidates = []
    for path in sorted(directory.glob("*/manifest.json")):
        manifest = load_live_view_manifest(path)
        if manifest.dataset_id == dataset:
            candidates.append(manifest)
    return candidates[-1] if candidates else None


def data_promote(args) -> dict[str, object]:
    dataset = str(args.dataset)
    return {
        "product": "data",
        "operation": "promote",
        "dataset": dataset,
        "target": str(args.for_use),
        "status": "removed",
        "ready_for": [],
        "blocked_for": [str(args.for_use)],
        "issues": [{
            "code": "promotion_removed",
            "message": "Dataset promotion has been removed from the Data product.",
            "why": "Datasets only describe stored data and read paths. Backtest or production approval belongs outside Dataset storage.",
        }],
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
    dataset = str(args.dataset)
    return {
        "product": "data",
        "operation": "audit",
        "dataset": dataset,
        "status": "removed",
        "issues": [{
            "code": "audit_removed",
            "message": "Dataset audit evidence has been removed from the Data product.",
            "why": "Dataset Store reads use the file tree as source of truth; hash and release audit files are no longer gates.",
        }],
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
    from kairospy.data.quality.freshness import load_live_view_manifest

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
    from kairospy.infrastructure.configuration import KairosProjectConfig, PROJECT_STATE_DIR
    from kairospy.infrastructure.storage.codec import to_primitive
    from kairospy.workspace import WorkspaceBuildContext

    config = KairosProjectConfig.discover(Path.cwd())
    params = _parse_run_params(getattr(args, "param", ()))
    run_config = getattr(args, "_run_config", None)
    run_config_path = getattr(run_config, "path", None)
    run_config_hash = _file_sha256(run_config_path) if run_config_path is not None else None
    project_config_hash = _file_sha256(config.path)
    workspace_ref = str(getattr(args, "workspace", "") or "kairospy.workspace.defaults:empty_workspace")
    strategy_ref = str(getattr(args, "strategy", "") or "kairospy.workspace.defaults:EmptyStrategy")
    workspace_module, workspace_callable_name, workspace_entrypoint = _load_run_entrypoint(workspace_ref, config.root)
    strategy_module, strategy_callable_name, strategy_entrypoint = _load_run_entrypoint(strategy_ref, config.root)
    workspace_context = WorkspaceBuildContext(
        project_root=config.root,
        data_root=config.relative_path("paths.lake_root", ".kairos/data"),
    )
    projection = workspace_entrypoint(workspace_context, params)
    if projection is None:
        projection = workspace_context.project()
    if not hasattr(projection, "to_dict"):
        raise ValueError(f"workspace entrypoint must return WorkspaceProjection: {workspace_ref}")
    projection_snapshot = projection.to_dict()
    workspace_preflight = projection.preflight(str(args.mode)) if hasattr(projection, "preflight") else {
        "mode": str(args.mode),
        "passed": True,
        "issues": [],
    }
    if not workspace_preflight.get("passed", True):
        errors = [
            str(item.get("message") or item.get("code") or item)
            for item in workspace_preflight.get("issues", ())
            if isinstance(item, dict) and item.get("severity") == "error"
        ]
        raise ValueError("workspace preflight failed: " + "; ".join(errors))

    material = {
        "workspace": workspace_ref,
        "strategy": strategy_ref,
        "mode": args.mode,
        "params": params,
        "run_config": str(run_config_path) if run_config_path is not None else None,
        "run_config_hash": run_config_hash,
        "at": _now(),
    }
    run_id = f"run_{sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:16]}"
    directory = config.root / PROJECT_STATE_DIR / "run" / run_id
    workspace_snapshot_hash = _stable_hash(_json_safe(projection_snapshot))
    workspace_code_hash = _stable_hash({"entrypoint": workspace_ref, "module": workspace_module.__name__, "callable": workspace_callable_name})
    strategy_hash = _stable_hash({"entrypoint": strategy_ref, "module": strategy_module.__name__, "callable": strategy_callable_name})
    params_hash = _stable_hash(params)
    config_hash = _stable_hash({"params": params, "run_config_hash": run_config_hash, "workspace_code_hash": workspace_code_hash})
    execution_holder: dict[str, object] = {}

    def strategy_runner(_prepared: object):
        execution = _execute_workspace_code_strategy(strategy_entrypoint, projection, params)
        execution_holder["execution"] = execution
        return _strategy_run_result_from_workspace_execution(execution)

    runtime_launch = None
    run_kernel_result = None
    if args.mode == "paper":
        runtime_launch, run_kernel_result = _launch_workspace_paper_run(
            directory,
            run_id,
            workspace_ref,
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
            workspace_ref,
            workspace_snapshot_hash,
            strategy_hash,
            config_hash,
            params,
            strategy_runner,
            run_config=run_config,
            workspace_snapshot=projection_snapshot,
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
    projection_artifact = directory / "projection.json"
    workspace_preflight_artifact = directory / "workspace_preflight.json"
    artifacts = {
        "decisions": str(decision_artifact),
        "summary": str(directory / "reports" / "summary.json"),
    }
    if result is not None:
        artifacts["result"] = str(result_artifact)
    artifacts["projection"] = str(projection_artifact)
    artifacts["workspace_preflight"] = str(workspace_preflight_artifact)
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
        "workspace_preflight": workspace_preflight,
        "confirm_live": bool(getattr(args, "confirm_live", False)),
        "readiness_ref": evidence_table.get("readiness"),
        "promotion_ref": evidence_table.get("promotion"),
    }
    _write_json(snapshot_artifact, projection_snapshot)
    _write_json(projection_artifact, projection_snapshot)
    _write_json(workspace_preflight_artifact, workspace_preflight)
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
        workspace_name=workspace_ref,
        workspace_root=config.root,
        workspace_snapshot_artifact=snapshot_artifact,
        workspace_snapshot_hash=workspace_snapshot_hash,
        strategy={
            "entrypoint": strategy_ref,
            "module": strategy_module.__name__,
            "callable": strategy_callable_name,
            "entrypoint_kind": execution["entrypoint_kind"],
            "hash": strategy_hash,
            "params": params,
            "workspace_entrypoint": workspace_ref,
            "workspace_module": workspace_module.__name__,
            "workspace_callable": workspace_callable_name,
            "workspace_code_hash": workspace_code_hash,
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
    decisions_count = len(decisions) if isinstance(decisions, list) else int(decisions is not None)
    _write_json(directory / "reports" / "summary.json", {
        "run_id": run_id,
        "workspace": workspace_ref,
        "mode": args.mode,
        "strategy": strategy_ref,
        "workspace_entrypoint": workspace_ref,
        "passed": True,
        "decisions_count": decisions_count,
        "result_artifact": str(result_artifact) if result is not None else None,
        "runtime_launch": runtime_launch,
        "workspace_preflight": workspace_preflight,
    })
    return {
        **manifest,
        "decisions_count": decisions_count,
        "workspace_root": str(config.root),
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


def _execute_workspace_code_strategy(strategy_entrypoint: object, projection: object, params: dict[str, str]) -> dict[str, object]:
    strategy = _instantiate_workspace_strategy(strategy_entrypoint, projection, params)
    if not _looks_like_standard_strategy(strategy):
        raise ValueError("run.strategy must resolve to a Strategy with on_start/on_market/on_fill/on_end")
    from kairospy.strategy.runtime import StrategyRuntime

    context = _projection_context(projection, params)
    runtime = StrategyRuntime(strategy)
    decisions: list[object] = []
    decisions.extend(_normalize_decisions(runtime.intents_on_start(context)))
    decisions.extend(_normalize_decisions(runtime.intents_on_market(context)))
    decisions.extend(_normalize_decisions(runtime.intents_on_end(context)))
    return {
        "entrypoint_kind": "workspace_code_strategy",
        "decisions": decisions,
        "result": {
            "strategy_id": getattr(strategy, "strategy_id", ""),
            "projection_nodes": len(getattr(projection, "nodes", ())),
            "features": [
                node.name
                for node in getattr(projection, "features", ())
            ],
        },
    }


def _instantiate_workspace_strategy(strategy_entrypoint: object, projection: object, params: dict[str, str]) -> object:
    if inspect.isclass(strategy_entrypoint):
        try:
            return strategy_entrypoint()
        except TypeError:
            return strategy_entrypoint(projection, params)
    candidate = strategy_entrypoint
    if _looks_like_standard_strategy(candidate):
        return candidate
    try:
        value = strategy_entrypoint()
    except TypeError:
        value = strategy_entrypoint(projection, params)
    return value


def _projection_context(projection: object, params: dict[str, str]):
    from kairospy.identity import InstrumentId
    from kairospy.strategy.protocols import Context
    from kairospy.strategy.views import (
        BudgetView,
        FeatureValue,
        FeatureView,
        MarketView,
        OrderView,
        PortfolioView,
        ReferenceView,
        IntentView,
    )

    now = datetime.now(timezone.utc)
    market_nodes = tuple(getattr(projection, "market", ()) or ())
    feature_nodes = tuple(getattr(projection, "features", ()) or ())
    instrument = InstrumentId(str(params.get("instrument") or params.get("instruments") or "workspace:unknown"))
    market = MarketView(
        now,
        1,
        (instrument,),
        data_binding="workspace_projection",
        available_instruments=(instrument,),
        quality_codes=tuple(
            f"{getattr(node, 'name', 'node')}:{getattr(node, 'kind', 'unknown')}"
            for node in market_nodes
        ),
    )
    features = tuple(
        FeatureValue(
            getattr(node, "name"),
            now,
            tuple(sorted((str(key), value) for key, value in dict(getattr(node, "params", {}) or {}).items())),
            quality="projection",
            input_identity=str(getattr(node, "source", "") or getattr(node, "dataset", "") or ""),
            state_hash=_run_component_hash(getattr(node, "to_dict", lambda: str(node))()),
            available_time=now,
        )
        for node in feature_nodes
    )
    return Context(
        market,
        PortfolioView(timestamp=now),
        FeatureView(now if features else None, now if features else None, features, _run_component_hash([item.feature_id for item in features]) if features else "none"),
        ReferenceView.empty(),
        OrderView.empty(),
        IntentView.empty(),
        BudgetView.empty(),
    )


def _looks_like_standard_strategy(strategy: object) -> bool:
    return all(callable(getattr(strategy, name, None)) for name in ("on_start", "on_market", "on_fill", "on_end"))


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


def _run_live_workspace_market_strategy(
    profile: object,
    prepared: object,
    strategy_entrypoint: object,
    projection: object,
    params: dict[str, str],
):
    from kairospy.infrastructure.storage.codec import to_primitive
    from kairospy.runtime import CanonicalMarketProjection, StrategyRunResult
    from kairospy.strategy.protocols import Context
    from kairospy.strategy.runtime import StrategyRuntime
    from kairospy.strategy.views import (
        BudgetView,
        FeatureView,
        IntentView,
        MarketView,
        OrderView,
        PortfolioView,
        ReferenceView,
    )

    strategy = _instantiate_workspace_strategy(strategy_entrypoint, projection, params)
    if not _looks_like_standard_strategy(strategy):
        raise ValueError("run.strategy must resolve to a Strategy with on_start/on_market/on_fill/on_end")
    runtime = StrategyRuntime(strategy)
    market_projection = CanonicalMarketProjection()
    decisions: list[object] = []
    event_ids: list[str] = []
    last_context = None
    started = False
    for event in profile.market_events(prepared):
        market = market_projection.apply(event)
        if market is None:
            continue
        context = Context(
            MarketView.from_snapshot(market),
            PortfolioView(timestamp=market.timestamp),
            FeatureView.empty(),
            ReferenceView.empty(),
            OrderView.empty(),
            IntentView.empty(),
            BudgetView.empty(),
        )
        if not started:
            decisions.extend(_normalize_decisions(runtime.intents_on_start(context)))
            started = True
        decisions.extend(_normalize_decisions(runtime.intents_on_market(context)))
        event_ids.append(str(getattr(event, "message_id", "")))
        last_context = context
    if last_context is not None:
        decisions.extend(_normalize_decisions(runtime.intents_on_end(last_context)))
    primitive_decisions = tuple(_normalize_decisions(to_primitive(decisions)))
    factor_hash = _run_component_hash(())
    decision_hash = _run_component_hash(to_primitive(primitive_decisions))
    intent_hash = _run_component_hash(())
    audit_hash = _run_component_hash({
        "events": event_ids,
        "factor_hash": factor_hash,
        "decision_hash": decision_hash,
        "intent_hash": intent_hash,
        "context_hash": last_context.context_hash if last_context is not None else "",
    })
    return StrategyRunResult(
        tuple(event_ids),
        (),
        primitive_decisions,
        (),
        factor_hash,
        decision_hash,
        intent_hash,
        audit_hash,
        dict(last_context.view_hashes) if last_context is not None else {},
        last_context.context_hash if last_context is not None else "",
    )


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
    if _run_config_requires_live_trading_enabled(run_config) and getattr(config, "get")("execution.live_trading_enabled", False) is not True:
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
            user_fill_event_source=getattr(provider_ports, "user_fill_event_source", None),
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
        freshness_monitor = _market_freshness_runtime_monitor(store, run_id, market_source)
        if freshness_monitor is not None:
            managed_service_list.append(freshness_monitor.managed_service())
    if provider_ports is not None:
        managed_service_list.append(components.outbox_dispatcher_service(run_id).managed_service())
        managed_service_list.append(components.risk_monitor_service(run_id).managed_service())
        fill_ingestion = components.fill_ingestion_service(run_id)
        if fill_ingestion is not None:
            managed_service_list.append(fill_ingestion.managed_service())
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
    live_views = bindings.get("live_views", {})
    workspace_bindings = {}
    if isinstance(workspace_snapshot, dict):
        workspace_bindings = workspace_snapshot.get("bindings", {})
        if not isinstance(workspace_bindings, dict) or not workspace_bindings:
            workspace_bindings = workspace_snapshot.get("attachments", {})
    selected = None
    for raw_name in market_names:
        name = str(raw_name)
        view = live_views.get(name) if isinstance(live_views, dict) else None
        workspace_binding = workspace_bindings.get(name, {}) if isinstance(workspace_bindings, dict) else {}
        if isinstance(view, dict):
            selected = (name, view, workspace_binding if isinstance(workspace_binding, dict) else {})
            break
        if isinstance(workspace_binding, dict) and workspace_binding.get("kind") == "live_view":
            selected = (name, {}, workspace_binding)
            break
        default_view = _default_live_market_view(config, live, name, workspace_binding)
        if default_view is not None:
            selected = (name, default_view, workspace_binding if isinstance(workspace_binding, dict) else {})
            break
    if selected is None:
        return {}
    name, view, workspace_binding = selected
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


def _default_live_market_view(
    config: object,
    live: dict[str, object],
    name: str,
    workspace_binding: object,
) -> dict[str, object] | None:
    if not isinstance(workspace_binding, dict):
        return None
    dataset = str(workspace_binding.get("dataset") or "")
    if not dataset:
        return None
    try:
        from kairospy.data.quality.freshness import live_view_manifest_path, load_live_view_manifest
        from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT

        root = config.relative_path("paths.lake_root", DEFAULT_LAKE_ROOT)
        path = live_view_manifest_path(root, dataset, "default")
        if not path.exists():
            return None
        manifest = load_live_view_manifest(path)
    except Exception:
        return None
    source = manifest.source if isinstance(manifest.source, dict) else {}
    plane = manifest.live_data_plane if isinstance(manifest.live_data_plane, dict) else {}
    provider = str(source.get("provider") or plane.get("provider") or live.get("provider") or "")
    if provider and str(live.get("provider") or "") and provider != str(live.get("provider")):
        return None
    return {
        "provider": provider,
        "name": name,
        "dataset": dataset,
        "live_view_id": "default",
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
    if action not in {
        "start", "recover", "status", "attach", "stop", "pause", "resume", "reduce-only", "clear-reduce-only",
        "cancel-all", "reconcile", "commands", "kill-switch", "reset-kill-switch", "reload-risk-limits",
        "target-position", "incidents", "close-incident", "metrics", "export", "force-stop",
    }:
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
    if action in {"status", "attach"} and not paths.runtime_database.exists():
        return _run_live_status_payload(action, run_id, paths.runtime_database, None)
    if action == "metrics" and not paths.runtime_database.exists():
        status_payload = _run_live_status_payload("status", run_id, paths.runtime_database, None)
        payload = _run_live_metrics_payload(action, run_id, paths.runtime_database, status_payload)
        return _attach_run_live_metrics_artifact(
            payload,
            output=getattr(args, "output", None),
            prometheus=bool(getattr(args, "prometheus", False)),
        )
    if action == "export" and not paths.runtime_database.exists():
        raise LookupError(f"runtime export requires an existing live runtime database for run_id: {run_id}")
    if action == "incidents" and not paths.runtime_database.exists():
        return _run_live_incidents_payload(action, run_id, paths.runtime_database, (), status="open")
    if action == "close-incident" and not paths.runtime_database.exists():
        raise LookupError(f"runtime incident store not found for run_id: {run_id}")
    store = SQLiteRuntimeStore(paths.runtime_database)

    if action in {"status", "attach"}:
        bus = OperatorCommandBus(store)
        fresh_command = None
        if bool(getattr(args, "fresh", False)):
            fresh_command = bus.submit(
                run_id=run_id,
                command_type=OperatorCommandType.REQUEST_STATUS_SNAPSHOT,
                payload={},
                actor=str(getattr(args, "actor", None) or "cli"),
                reason=str(getattr(args, "reason", None) or "status snapshot requested"),
                idempotency_key=getattr(args, "idempotency_key", None),
                at=datetime.now(timezone.utc),
            )
            wait_seconds = float(getattr(args, "wait", 0.0) or 0.0)
            if wait_seconds > 0:
                fresh_command = _wait_for_operator_command(bus, run_id, fresh_command.command_id, wait_seconds)
        state = store.runtime_state(f"{LiveRunDaemon.STATE_KEY_PREFIX}:{run_id}")
        stop_report = store.runtime_state("runtime_stop:last")
        live_binding = store.runtime_state(f"live_run_config:{run_id}")
        risk_state = store.runtime_state("risk_runtime:last")
        cancel_all_state = store.runtime_state(f"cancel_all:{run_id}:last")
        reconciliation_request = store.runtime_state(f"reconciliation_request:{run_id}:last")
        outbox_state = store.runtime_state(f"order_outbox_dispatcher:{run_id}")
        fill_ingestion_state = store.runtime_state(f"fill_ingestion:{run_id}:last")
        market_freshness_state = store.runtime_state(f"market_freshness:{run_id}:last")
        target_position_state = store.runtime_state(f"target_position:{run_id}:last")
        recovery_state = _runtime_recovery_state(store)
        heartbeat = LiveRunRegistry(store).status(
            run_id,
            at=datetime.now(timezone.utc),
            stale_after_seconds=float(getattr(args, "stale_after_seconds", 5.0) or 5.0),
        )
        payload = _run_live_status_payload(
            action,
            run_id,
            paths.runtime_database,
            state if isinstance(state, dict) else None,
            stop_report if isinstance(stop_report, dict) else None,
            tuple(command.manifest() for command in bus.commands(run_id, limit=5)),
            heartbeat,
            live_binding if isinstance(live_binding, dict) else None,
            risk_state if isinstance(risk_state, dict) else None,
            cancel_all_state if isinstance(cancel_all_state, dict) else None,
            reconciliation_request if isinstance(reconciliation_request, dict) else None,
            fresh_command.manifest() if fresh_command is not None else None,
            recovery_state,
            outbox_state if isinstance(outbox_state, dict) else None,
            fill_ingestion_state if isinstance(fill_ingestion_state, dict) else None,
            market_freshness_state if isinstance(market_freshness_state, dict) else None,
            target_position_state if isinstance(target_position_state, dict) else None,
        )
        _sync_run_live_health_incident(store, run_id, payload, datetime.now(timezone.utc))
        incidents = tuple(item.manifest() for item in store.runtime_incidents(run_id, status="open", limit=5))
        if incidents:
            payload["incidents"] = incidents
            payload["open_incident_count"] = len(incidents)
            payload["metrics"] = _run_live_metrics_summary(payload)
        return payload

    if action == "metrics":
        status_payload = run_live(SimpleNamespace(
            live_action="status",
            run_id=run_id,
            stale_after_seconds=float(getattr(args, "stale_after_seconds", 5.0) or 5.0),
        ))
        status_payload["operator_commands"] = tuple(command.manifest() for command in OperatorCommandBus(store).commands(run_id))
        payload = _run_live_metrics_payload(action, run_id, paths.runtime_database, status_payload)
        return _attach_run_live_metrics_artifact(
            payload,
            output=getattr(args, "output", None),
            prometheus=bool(getattr(args, "prometheus", False)),
        )

    if action == "export":
        status_payload = run_live(SimpleNamespace(
            live_action="status",
            run_id=run_id,
            stale_after_seconds=float(getattr(args, "stale_after_seconds", 5.0) or 5.0),
        ))
        commands = tuple(command.manifest() for command in OperatorCommandBus(store).commands(run_id))
        incidents = tuple(item.manifest() for item in store.runtime_incidents(run_id, status=None))
        status_payload["operator_commands"] = commands
        status_payload["incidents"] = tuple(item for item in incidents if item.get("status") == "open")
        status_payload["open_incident_count"] = len(status_payload["incidents"])
        status_payload["metrics"] = _run_live_metrics_summary(status_payload)
        output = getattr(args, "output", None)
        export_dir = Path(output) if output is not None else paths.root / "exports" / _timestamp_slug(datetime.now(timezone.utc))
        return _export_run_live_artifacts(
            run_id,
            paths.runtime_database,
            export_dir,
            status_payload=status_payload,
            commands=commands,
            incidents=incidents,
            runtime_state=store.runtime_states(),
        )

    if action in {"stop", "force-stop"}:
        timeout_seconds = getattr(args, "timeout_seconds", None)
        force = bool(getattr(args, "force", False)) or action == "force-stop"
        application = KairosApplication(
            ApplicationConfig(Environment.LIVE, paths),
            store,
            runtime_id=run_id,
            probes=(FunctionProbe("live-run-config", lambda: (True, run_id)),),
        )
        daemon = LiveRunDaemon(
            application,
            (),
            run_id=run_id,
            structured_log_path=str(_run_live_structured_log_path(paths.root)),
        )
        snapshot = daemon.request_stop(
            str(getattr(args, "reason", None) or "operator stop requested"),
            actor=str(getattr(args, "actor", None) or "cli"),
            timeout_seconds=float(timeout_seconds) if timeout_seconds is not None else None,
            force=force,
        )
        stop_report = store.runtime_state("runtime_stop:last")
        commands = tuple(command.manifest() for command in OperatorCommandBus(store).commands(run_id, limit=5))
        return {
            **_run_live_snapshot_payload(action, snapshot, paths.runtime_database),
            "status": "stop_requested",
            "stop_requested": True,
            "force": force,
            **({"timeout_seconds": float(timeout_seconds)} if timeout_seconds is not None else {}),
            **({"stop_report": stop_report} if isinstance(stop_report, dict) else {}),
            "operator_commands": commands,
            **({"operator_command": commands[-1]} if commands else {}),
        }

    if action == "commands":
        return _run_live_commands_payload(
            action,
            run_id,
            paths.runtime_database,
            OperatorCommandBus(store),
            limit=int(getattr(args, "limit", 20) or 20),
        )

    if action == "incidents":
        status_filter = str(getattr(args, "status", "open") or "open")
        status = None if status_filter == "all" else status_filter
        incidents = tuple(item.manifest() for item in store.runtime_incidents(
            run_id,
            status=status,
            limit=int(getattr(args, "limit", 20) or 20),
        ))
        return _run_live_incidents_payload(action, run_id, paths.runtime_database, incidents, status=status_filter)

    if action == "close-incident":
        closed = store.close_runtime_incident(
            str(getattr(args, "incident_id", None) or ""),
            actor=str(getattr(args, "actor", None) or "cli"),
            reason=str(getattr(args, "reason", None) or ""),
            at=datetime.now(timezone.utc),
        )
        return {
            "product": "run",
            "operation": "live",
            "live_action": action,
            "run_id": run_id,
            "status": "closed",
            "runtime_database": str(paths.runtime_database),
            "incident": closed.manifest(),
        }

    if action in {
        "pause", "resume", "reduce-only", "clear-reduce-only", "cancel-all", "reconcile",
        "kill-switch", "reset-kill-switch", "reload-risk-limits", "target-position",
    }:
        command_type = {
            "pause": OperatorCommandType.PAUSE_NEW_ORDERS,
            "resume": OperatorCommandType.RESUME,
            "reduce-only": OperatorCommandType.SET_REDUCE_ONLY,
            "clear-reduce-only": OperatorCommandType.CLEAR_REDUCE_ONLY,
            "cancel-all": OperatorCommandType.CANCEL_ALL,
            "reconcile": OperatorCommandType.REQUEST_RECONCILIATION,
            "kill-switch": OperatorCommandType.KILL_SWITCH,
            "reset-kill-switch": OperatorCommandType.RESET_KILL_SWITCH,
            "reload-risk-limits": OperatorCommandType.RELOAD_RISK_LIMITS,
            "target-position": OperatorCommandType.TARGET_POSITION,
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
        if action == "target-position":
            payload = _run_live_target_position_payload(args)
        command = OperatorCommandBus(store).submit(
            run_id=run_id,
            command_type=command_type,
            payload=payload,
            actor=str(getattr(args, "actor", None) or "cli"),
            reason=str(getattr(args, "reason", None) or action),
            idempotency_key=getattr(args, "idempotency_key", None),
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
    if _run_live_start_requires_live_trading_enabled(config, args) and config.get("execution.live_trading_enabled", False) is not True:
        raise ValueError("run live start/recover requires execution.live_trading_enabled = true")
    duration = getattr(args, "duration_seconds", None)
    if _run_live_should_spawn(args, duration):
        if getattr(args, "config", None) is None:
            raise ValueError("background run live start/recover requires --config")
        live_binding = _run_live_spawn_binding_from_run_config(config, args)
        return _spawn_run_live_foreground_process(
            action,
            run_id,
            getattr(args, "config", None),
            paths.runtime_database,
            paths.root,
            args,
            live_binding,
        )
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
            run_lock_path=str(_run_live_lock_path(paths.root)),
            structured_log_path=str(_run_live_structured_log_path(paths.root)),
        )
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


def run_status(args) -> dict[str, object]:
    run_id = _validated_run_id(getattr(args, "run_id", None), "run status")
    live_payload = run_live(SimpleNamespace(
        live_action="status",
        run_id=run_id,
        stale_after_seconds=float(getattr(args, "stale_after_seconds", 5.0) or 5.0),
        fresh=bool(getattr(args, "fresh", False)),
        wait=float(getattr(args, "wait", 0.0) or 0.0),
        actor=getattr(args, "actor", "cli"),
        reason=getattr(args, "reason", None),
    ))
    if live_payload.get("status") != "not_started" or Path(str(live_payload.get("runtime_database", ""))).exists():
        return {
            **live_payload,
            "operation": "status",
            "control_surface": "run",
            "runtime_kind": "live",
        }
    try:
        manifest = _load_run_manifest(Path.cwd(), run_id)
    except FileNotFoundError:
        return {
            "product": "run",
            "operation": "status",
            "run_id": run_id,
            "status": "not_started",
            "runtime_kind": "unknown",
        }
    return {
        "product": "run",
        "operation": "status",
        "run_id": run_id,
        "status": manifest.get("status", "unknown"),
        "mode": manifest.get("mode"),
        "runtime_kind": "artifact",
        "manifest": str(_find_run_manifest(Path.cwd(), run_id)),
    }


def run_stop(args) -> dict[str, object]:
    run_id = _validated_run_id(getattr(args, "run_id", None), "run stop")
    payload = run_live(SimpleNamespace(
        live_action="stop",
        run_id=run_id,
        reason=getattr(args, "reason", None),
        actor=getattr(args, "actor", "cli"),
        timeout_seconds=getattr(args, "timeout_seconds", None),
        force=bool(getattr(args, "force", False)),
    ))
    return {
        **payload,
        "operation": "stop",
        "control_surface": "run",
        "runtime_kind": "live",
    }


def run_force_stop(args) -> dict[str, object]:
    run_id = _validated_run_id(getattr(args, "run_id", None), "run force-stop")
    payload = run_live(SimpleNamespace(
        live_action="force-stop",
        run_id=run_id,
        reason=getattr(args, "reason", None),
        actor=getattr(args, "actor", "cli"),
        timeout_seconds=float(getattr(args, "timeout_seconds", 1.0) or 1.0),
        force=True,
    ))
    return {
        **payload,
        "operation": "force-stop",
        "control_surface": "run",
        "runtime_kind": "live",
    }


def run_pause(args) -> dict[str, object]:
    return _run_operator_action("pause", args)


def run_resume(args) -> dict[str, object]:
    return _run_operator_action("resume", args)


def run_reduce_only(args) -> dict[str, object]:
    return _run_operator_action("reduce-only", args)


def run_clear_reduce_only(args) -> dict[str, object]:
    return _run_operator_action("clear-reduce-only", args)


def run_cancel_all(args) -> dict[str, object]:
    return _run_operator_action("cancel-all", args)


def run_reconcile(args) -> dict[str, object]:
    return _run_operator_action("reconcile", args)


def run_commands(args) -> dict[str, object]:
    run_id = _validated_run_id(getattr(args, "run_id", None), "run commands")
    payload = run_live(SimpleNamespace(
        live_action="commands",
        run_id=run_id,
        limit=getattr(args, "limit", 20),
    ))
    return {
        **payload,
        "operation": "commands",
        "control_surface": "run",
        "runtime_kind": "live",
    }


def run_metrics(args) -> dict[str, object]:
    run_id = _validated_run_id(getattr(args, "run_id", None), "run metrics")
    payload = run_live(SimpleNamespace(
        live_action="metrics",
        run_id=run_id,
        stale_after_seconds=float(getattr(args, "stale_after_seconds", 5.0) or 5.0),
        output=getattr(args, "output", None),
        prometheus=bool(getattr(args, "prometheus", False)),
    ))
    return {
        **payload,
        "operation": "metrics",
        "control_surface": "run",
        "runtime_kind": "live",
    }


def run_export(args) -> dict[str, object]:
    run_id = _validated_run_id(getattr(args, "run_id", None), "run export")
    payload = run_live(SimpleNamespace(
        live_action="export",
        run_id=run_id,
        output=getattr(args, "output", None),
        stale_after_seconds=float(getattr(args, "stale_after_seconds", 5.0) or 5.0),
    ))
    return {
        **payload,
        "operation": "export",
        "control_surface": "run",
        "runtime_kind": "live",
    }


def run_incidents(args) -> dict[str, object]:
    run_id = _validated_run_id(getattr(args, "run_id", None), "run incidents")
    payload = run_live(SimpleNamespace(
        live_action="incidents",
        run_id=run_id,
        status=getattr(args, "status", "open"),
        limit=getattr(args, "limit", 20),
    ))
    return {
        **payload,
        "operation": "incidents",
        "control_surface": "run",
        "runtime_kind": "live",
    }


def run_close_incident(args) -> dict[str, object]:
    run_id = _validated_run_id(getattr(args, "run_id", None), "run close-incident")
    payload = run_live(SimpleNamespace(
        live_action="close-incident",
        run_id=run_id,
        incident_id=getattr(args, "incident_id", None),
        actor=getattr(args, "actor", "cli"),
        reason=getattr(args, "reason", None),
    ))
    return {
        **payload,
        "operation": "close-incident",
        "control_surface": "run",
        "runtime_kind": "live",
    }


def _run_operator_action(action: str, args: object) -> dict[str, object]:
    run_id = _validated_run_id(getattr(args, "run_id", None), f"run {action}")
    payload = run_live(SimpleNamespace(
        live_action=action,
        run_id=run_id,
        reason=getattr(args, "reason", None),
        actor=getattr(args, "actor", "cli"),
        idempotency_key=getattr(args, "idempotency_key", None),
    ))
    return {
        **payload,
        "operation": action,
        "control_surface": "run",
        "runtime_kind": "live",
    }


def _validated_run_id(value: object, label: str) -> str:
    raw = str(value or "")
    run_id = raw.strip()
    if not run_id:
        raise ValueError(f"{label} requires --run-id")
    if raw != run_id or run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
        raise ValueError(f"{label} --run-id must be a single path segment")
    return run_id


def _wait_for_operator_command(bus: object, run_id: str, command_id: str, wait_seconds: float):
    if wait_seconds < 0:
        raise ValueError("status --wait cannot be negative")
    deadline = monotonic() + wait_seconds
    while True:
        for command in bus.commands(run_id, limit=50):
            if command.command_id == command_id:
                if command.terminal or monotonic() >= deadline:
                    return command
                break
        if monotonic() >= deadline:
            for command in bus.commands(run_id, limit=50):
                if command.command_id == command_id:
                    return command
            raise LookupError(f"operator command not found: {command_id}")
        sleep(min(0.05, max(0.0, deadline - monotonic())))


def _runtime_recovery_state(store: object) -> dict[str, object] | None:
    if not hasattr(store, "unresolved_orders") or not hasattr(store, "orders_requiring_venue_recovery"):
        return None
    unresolved = tuple(store.unresolved_orders())
    requiring = tuple(store.orders_requiring_venue_recovery())
    if not unresolved and not requiring:
        return {
            "unresolved_order_count": 0,
            "orders_requiring_recovery_count": 0,
            "unresolved_client_order_ids": (),
            "orders_requiring_recovery_client_order_ids": (),
        }
    return {
        "unresolved_order_count": len(unresolved),
        "orders_requiring_recovery_count": len(requiring),
        "unresolved_client_order_ids": tuple(item.request.client_order_id for item in unresolved),
        "orders_requiring_recovery_client_order_ids": tuple(item.request.client_order_id for item in requiring),
    }


def _run_live_target_position_payload(args: object) -> dict[str, object]:
    raw_legs = tuple(str(item) for item in (getattr(args, "leg", None) or ()))
    if not raw_legs:
        raise ValueError("run live target-position requires at least one --leg")
    return {
        "intent_id": str(getattr(args, "intent_id", None) or ""),
        "legs": tuple(_parse_target_position_leg(item) for item in raw_legs),
    }


def _parse_target_position_leg(value: str) -> dict[str, object]:
    parts = tuple(part.strip() for part in value.split(","))
    if len(parts) != 5 or any(not part for part in parts):
        raise ValueError("--leg must use venue,product,instrument,side,quantity")
    venue, product, instrument, side, quantity_text = parts
    side = side.lower()
    if side not in {"long", "short", "flat"}:
        raise ValueError("--leg side must be long, short or flat")
    quantity = Decimal(quantity_text)
    if quantity <= 0:
        raise ValueError("--leg quantity must be positive")
    return {
        "venue": venue,
        "product": product,
        "instrument": instrument,
        "side": side,
        "quantity": str(quantity),
    }


def _run_live_start_requires_live_trading_enabled(config: object, args: object) -> bool:
    config_path = getattr(args, "config", None)
    if config_path is None:
        return True
    from kairospy.runtime.run_config import load_run_config

    run_config = load_run_config(config_path, project_root=getattr(config, "root", Path.cwd()))
    return _run_config_requires_live_trading_enabled(run_config)


def _run_config_requires_live_trading_enabled(run_config: object) -> bool:
    live = run_config.get("live", {}) if hasattr(run_config, "get") else {}
    if not isinstance(live, dict):
        return True
    bind_ports = live.get("bind_ports", True)
    execution_driver = str(live.get("execution_driver") or "").strip()
    no_execution_drivers = {
        "",
        "none",
        "manual",
        "manual-target-position",
        "manual-cross-venue",
        "manual-cross-venue-preflight",
    }
    return not (bind_ports is False and execution_driver in no_execution_drivers)


def _run_live_should_spawn(args: object, duration_seconds: object) -> bool:
    if not hasattr(args, "foreground"):
        return False
    if bool(getattr(args, "foreground", False)):
        return False
    return duration_seconds is None


def _run_live_spawn_binding_from_run_config(config: object, args: object) -> dict[str, object]:
    from kairospy.runtime.run_config import load_run_config

    run_config = load_run_config(getattr(args, "config"), project_root=getattr(config, "root", Path.cwd()))
    start_args = run_config.to_start_args(
        confirm_live=True,
        supervise_live_services=bool(getattr(args, "supervise_live_services", False)),
        param_overrides=tuple(getattr(args, "param", ()) or ()),
    )
    if start_args.mode != "live":
        raise ValueError("run live start/recover --config requires run.mode = \"live\"")
    run_config_hash = _file_sha256(run_config.path)
    config_hash = _stable_hash({
        "params": _parse_run_params(tuple(getattr(args, "param", ()) or ())),
        "run_config_hash": run_config_hash,
    })
    return {
        "path": str(run_config.path),
        "mode": start_args.mode,
        "workspace": start_args.workspace,
        "strategy": str(start_args.strategy),
        "hash": run_config_hash,
        "run_config_hash": run_config_hash,
        "config_hash": config_hash,
    }


def _spawn_run_live_foreground_process(
    action: str,
    run_id: str,
    config_path: Path | None,
    runtime_database: Path,
    runtime_root: Path,
    args: object,
    live_binding: dict[str, object] | None,
) -> dict[str, object]:
    from kairospy.runtime import StructuredRuntimeLog

    command = _run_live_foreground_command(action, run_id, config_path, args)
    log_file = Path(getattr(args, "log_file", None) or runtime_root / "daemon.log")
    structured_log_file = _run_live_structured_log_path(runtime_root)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    at = datetime.now(timezone.utc)
    store = getattr(args, "_runtime_store", None)
    if store is None:
        from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore

        store = SQLiteRuntimeStore(runtime_database)
    state = {
        "run_id": run_id,
        "phase": "starting",
        "application_status": "created",
        "reason": "spawned foreground daemon process",
        "services": [],
        "stop_requested": False,
        "spawn": {
            "command": command,
            "log_file": str(log_file),
            "structured_log_file": str(structured_log_file),
            "requested_at": at.isoformat(),
        },
    }
    store.set_runtime_state(f"live_run_daemon:{run_id}", state, at)
    structured_log = StructuredRuntimeLog(structured_log_file)
    structured_log.append("daemon_spawn_requested", run_id=run_id, payload=state["spawn"], at=at)
    try:
        with log_file.open("ab") as handle:
            process = subprocess.Popen(
                command,
                cwd=Path.cwd(),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except Exception as error:
        state["phase"] = "failed"
        state["reason"] = str(error)
        state["spawn"]["error_type"] = type(error).__name__
        state["spawn"]["failed_at"] = datetime.now(timezone.utc).isoformat()
        store.set_runtime_state(f"live_run_daemon:{run_id}", state, datetime.now(timezone.utc))
        structured_log.append(
            "daemon_spawn_failed",
            run_id=run_id,
            level="error",
            payload=state["spawn"],
            at=datetime.now(timezone.utc),
        )
        raise
    state["spawn"]["pid"] = process.pid
    store.set_runtime_state(f"live_run_daemon:{run_id}", state, datetime.now(timezone.utc))
    structured_log.append("daemon_spawned", run_id=run_id, payload=state["spawn"], at=datetime.now(timezone.utc))
    return {
        "product": "run",
        "operation": "live",
        "live_action": action,
        "run_id": run_id,
        "status": "spawned",
        "phase": "starting",
        "foreground": False,
        "pid": process.pid,
        "runtime_database": str(runtime_database),
        "state_key": f"live_run_daemon:{run_id}",
        "log_file": str(log_file),
        "structured_log_file": str(structured_log_file),
        "command": command,
        **({"run_config": live_binding} if isinstance(live_binding, dict) else {}),
    }


def _run_live_foreground_command(
    action: str,
    run_id: str,
    config_path: Path | None,
    args: object,
) -> list[str]:
    command = [sys.executable, "-m", "kairospy"]
    output_format = getattr(args, "format", None)
    if output_format:
        command.extend(["--format", str(output_format)])
    language = getattr(args, "lang", None)
    if language:
        command.extend(["--lang", str(language)])
    command.extend(["run", "live", action, "--run-id", run_id, "--foreground"])
    if config_path is not None:
        command.extend(["--config", str(config_path)])
    for item in tuple(getattr(args, "param", ()) or ()):
        command.extend(["--param", str(item)])
    if bool(getattr(args, "confirm_live", False)):
        command.append("--confirm-live")
    poll_seconds = getattr(args, "poll_seconds", None)
    if poll_seconds is not None:
        command.extend(["--poll-seconds", str(poll_seconds)])
    return command


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
    from kairospy.workspace import WorkspaceBuildContext

    run_config = load_run_config(getattr(args, "config"), project_root=getattr(config, "root", Path.cwd()))
    start_args = run_config.to_start_args(
        confirm_live=True,
        supervise_live_services=bool(getattr(args, "supervise_live_services", False)),
        param_overrides=tuple(getattr(args, "param", ()) or ()),
    )
    if start_args.mode != "live":
        raise ValueError("run live start/recover --config requires run.mode = \"live\"")

    params = _parse_run_params(getattr(start_args, "param", ()))
    workspace_ref = str(start_args.workspace)
    strategy_ref = str(start_args.strategy)
    workspace_module, workspace_callable_name, workspace_entrypoint = _load_run_entrypoint(workspace_ref, getattr(config, "root", Path.cwd()))
    strategy_module, strategy_callable_name, _strategy_entrypoint = _load_run_entrypoint(strategy_ref, getattr(config, "root", Path.cwd()))
    workspace_context = WorkspaceBuildContext(
        project_root=getattr(config, "root", Path.cwd()),
        data_root=getattr(config, "relative_path")("paths.lake_root", ".kairos/data"),
    )
    projection = workspace_entrypoint(workspace_context, params)
    if projection is None:
        projection = workspace_context.project()
    workspace_snapshot = projection.to_dict()
    workspace_preflight = projection.preflight("live") if hasattr(projection, "preflight") else {
        "mode": "live",
        "passed": True,
        "issues": [],
    }
    if not workspace_preflight.get("passed", True):
        errors = [
            str(item.get("message") or item.get("code") or item)
            for item in workspace_preflight.get("issues", ())
            if isinstance(item, dict) and item.get("severity") == "error"
        ]
        raise ValueError("workspace preflight failed: " + "; ".join(errors))

    run_id = str(getattr(args, "run_id"))
    workspace_snapshot_hash = _stable_hash(_json_safe(workspace_snapshot))
    strategy_hash = _stable_hash({"entrypoint": strategy_ref, "module": strategy_module.__name__, "callable": strategy_callable_name})
    run_config_hash = _file_sha256(run_config.path)
    config_hash = _stable_hash({
        "params": params,
        "run_config_hash": run_config_hash,
        "workspace_code_hash": _stable_hash({
            "entrypoint": workspace_ref,
            "module": workspace_module.__name__,
            "callable": workspace_callable_name,
        }),
    })
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
            FunctionProbe("workspace", lambda: (True, workspace_ref)),
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
            user_fill_event_source=getattr(provider_ports, "user_fill_event_source", None),
        )
        application.order_recovery = components.order_recovery_service()
        profile = bind_live_runtime_components(components)
    def strategy_runner(prepared: object):
        if market_source is not None:
            return _run_live_workspace_market_strategy(
                profile,
                prepared,
                _strategy_entrypoint,
                projection,
                params,
            )
        execution = _execute_workspace_code_strategy(_strategy_entrypoint, projection, params)
        return _strategy_run_result_from_workspace_execution(execution)

    stop_policy = _strategy_stop_policy_metadata(
        strategy_spec,
        controller_bound=components is not None and strategy_spec is not None,
    )
    strategy_id = getattr(strategy_spec, "strategy_id", workspace_ref)
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
    service = (
        _live_workspace_market_strategy_service(
            run_id,
            market_source.event_source,
            _strategy_entrypoint,
            projection,
            params,
        )
        if market_source is not None else
        LiveRunKernelService(
            store,
            RunKernel(profile),
            request,
            strategy_runner,
            artifact_writer=artifact_writer,
            clock=application.clock,
        ).managed_service()
    )
    stop_handler = _run_live_stop_handler(config, args, application, store, run_id)
    if stop_handler is None and components is not None and strategy_spec is not None:
        stop_controller = components.stop_controller(strategy_spec)

        def stop_handler(reason: str = "manual"):
            return stop_controller.execute(reason)
    operator_command_handler = None
    if components is not None:
        coordinator = components.execution_coordinator()

        def operator_command_handler(command: object):
            from kairospy.runtime import OperatorCommandType
            from kairospy.runtime.application import RuntimeStatus

            command_type = OperatorCommandType(getattr(command, "command_type"))
            if command_type is OperatorCommandType.REQUEST_RECONCILIATION:
                return _run_live_apply_reconciliation_command(
                    daemon,
                    command,
                    components.reconciliation_services(),
                )
            if command_type is not OperatorCommandType.CANCEL_ALL:
                return None
            if application.status in {
                RuntimeStatus.READY,
                RuntimeStatus.RUNNING,
                RuntimeStatus.DEGRADED,
                RuntimeStatus.REDUCE_ONLY,
            } and application.status is not RuntimeStatus.REDUCE_ONLY:
                application.degrade(str(getattr(command, "reason")), reduce_only=True)
            result = coordinator.cancel_all_orders(
                tuple(getattr(components, "accounts", ()) or application.accounts),
                str(getattr(command, "reason")),
            )
            at = application.clock.now()
            store.set_runtime_state(
                f"cancel_all:{run_id}:last",
                {
                    "run_id": run_id,
                    "status": "succeeded" if not result.failures else "partial_failure",
                    "actor": str(getattr(command, "actor")),
                    "reason": str(getattr(command, "reason")),
                    "updated_at": at.isoformat(),
                    "cancelled_client_order_ids": result.cancelled_client_order_ids,
                    "failures": result.failures,
                },
                at,
            )
            snapshot = daemon.mark_reduce_only(str(getattr(command, "reason")))
            return {
                "phase": snapshot.phase.value,
                "desired_state": "reduce_only",
                "cancel_all_requested": True,
                "cancelled_orders": result.cancelled_client_order_ids,
                "failures": result.failures,
            }

    fill_ingestion_service = components.fill_ingestion_service(run_id) if components is not None else None
    market_freshness_monitor = (
        _market_freshness_runtime_monitor(store, run_id, market_source)
        if market_source is not None else None
    )
    managed_services = (
        *((market_source.managed_services if market_source is not None else ())),
        *((market_freshness_monitor.managed_service(),) if market_freshness_monitor is not None else ()),
        *((components.outbox_dispatcher_service(run_id).managed_service(),) if components is not None else ()),
        *((components.risk_monitor_service(run_id).managed_service(),) if components is not None else ()),
        *((fill_ingestion_service.managed_service(),) if fill_ingestion_service is not None else ()),
        *(tuple(
            monitor.managed_service()
            for monitor in components.reconciliation_monitor_services(run_id)
        ) if components is not None else ()),
        service,
    )
    binding = {
        "path": str(run_config.path),
        "hash": run_config_hash,
        "run_config_hash": run_config_hash,
        "config_hash": config_hash,
        "workspace": workspace_ref,
        "profile_id": profile.profile_id,
        "provider_binding": bool(provider_ports),
        "market_binding": bool(market_source),
        "managed_service_names": tuple(getattr(item, "name", str(item)) for item in managed_services),
        **({"stop_policy": stop_policy} if stop_policy is not None else {}),
    }
    store.set_runtime_state(f"live_run_config:{run_id}", binding, datetime.now(timezone.utc))
    daemon = LiveRunDaemon(
        application,
        managed_services,
        run_id=run_id,
        stop_handler=stop_handler,
        operator_command_handler=operator_command_handler,
        process_config_hash=config_hash,
        run_lock_path=str(_run_live_lock_path(paths.root)),
        structured_log_path=str(_run_live_structured_log_path(paths.root)),
    )
    return daemon, binding


def _run_live_structured_log_path(runtime_root: Path) -> Path:
    return Path(runtime_root) / "runtime.jsonl"


def _run_live_lock_path(runtime_root: Path) -> Path:
    return Path(runtime_root) / "run.lock"


def _live_workspace_market_strategy_service(
    run_id: str,
    event_source: object,
    strategy_entrypoint: object,
    projection: object,
    params: dict[str, str],
):
    from kairospy.runtime import ManagedServiceSpec

    async def run() -> None:
        from kairospy.runtime import CanonicalMarketProjection
        from kairospy.strategy.protocols import Context
        from kairospy.strategy.runtime import StrategyRuntime
        from kairospy.strategy.views import (
            BudgetView,
            FeatureView,
            IntentView,
            MarketView,
            OrderView,
            PortfolioView,
            ReferenceView,
        )

        if not hasattr(event_source, "events") or not callable(event_source.events):
            raise ValueError("live market strategy service requires event_source.events()")
        strategy = _instantiate_workspace_strategy(strategy_entrypoint, projection, params)
        if not _looks_like_standard_strategy(strategy):
            raise ValueError("run.strategy must resolve to a Strategy with on_start/on_market/on_fill/on_end")
        runtime = StrategyRuntime(strategy)
        market_projection = CanonicalMarketProjection()
        started = False
        last_context = None
        try:
            async for event in event_source.events():
                market = market_projection.apply(event)
                if market is None:
                    continue
                context = Context(
                    MarketView.from_snapshot(market),
                    PortfolioView(timestamp=market.timestamp),
                    FeatureView.empty(),
                    ReferenceView.empty(),
                    OrderView.empty(),
                    IntentView.empty(),
                    BudgetView.empty(),
                )
                if not started:
                    runtime.intents_on_start(context)
                    started = True
                runtime.intents_on_market(context)
                last_context = context
        finally:
            if last_context is not None:
                runtime.intents_on_end(last_context)

    return ManagedServiceSpec(f"strategy-run:{run_id}", run)


def _run_live_stop_handler(config: object, args: object, application: object, store: object, run_id: str):
    handler = getattr(args, "_stop_handler", None)
    if handler is not None:
        return handler
    factory = getattr(args, "_stop_handler_factory", None)
    if factory is not None:
        return factory(application, store, run_id)
    return None


def _market_freshness_runtime_monitor(store: object, run_id: str, market_source: object):
    from kairospy.runtime import MarketFreshnessRuntimeMonitorService

    required = ("name", "dataset", "live_view_id", "manifest_path")
    if not all(hasattr(market_source, name) for name in required):
        return None
    return MarketFreshnessRuntimeMonitorService(
        store,
        run_id=run_id,
        name=str(getattr(market_source, "name")),
        dataset=str(getattr(market_source, "dataset")),
        live_view_id=str(getattr(market_source, "live_view_id")),
        manifest_path=getattr(market_source, "manifest_path"),
    )


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
                stop_payload = getattr(stop_command, "payload", {}) if stop_command is not None else {}
                stop_payload = stop_payload if isinstance(stop_payload, dict) else {}
                timeout_seconds = float(stop_payload.get("timeout_seconds") or 5.0)
                force_stop = bool(stop_payload.get("force", False))
                try:
                    stopped = await daemon.stop(reason="manual", timeout_seconds=timeout_seconds, force=force_stop)
                except Exception as error:
                    if stop_command is not None:
                        daemon.fail_operator_command(stop_command, error)
                    raise
                completed_command = (
                    daemon.complete_operator_command(stop_command, {
                        "phase": getattr(stopped.phase, "value", str(stopped.phase)),
                        "application_status": getattr(stopped.application_status, "value", str(stopped.application_status)),
                        "timeout_seconds": timeout_seconds,
                        "force": force_stop,
                    })
                    if stop_command is not None else None
                )
                return {
                    **_run_live_snapshot_payload(action, stopped, None),
                    **_run_live_stop_report_payload(daemon),
                    "status": "stopped",
                    "stop_requested": True,
                    "force": force_stop,
                    "timeout_seconds": timeout_seconds,
                    **({"operator_command": completed_command.manifest()} if completed_command is not None else {}),
                    "started": _run_live_snapshot_payload(action, snapshot, None),
                }
            operator_command = daemon.claim_operator_command(
                OperatorCommandType.PAUSE_NEW_ORDERS,
                OperatorCommandType.RESUME,
                OperatorCommandType.SET_REDUCE_ONLY,
                OperatorCommandType.CLEAR_REDUCE_ONLY,
                OperatorCommandType.CANCEL_ALL,
                OperatorCommandType.REQUEST_STATUS_SNAPSHOT,
                OperatorCommandType.REQUEST_RECONCILIATION,
                OperatorCommandType.KILL_SWITCH,
                OperatorCommandType.RESET_KILL_SWITCH,
                OperatorCommandType.RELOAD_RISK_LIMITS,
                OperatorCommandType.TARGET_POSITION,
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
            try:
                daemon.heartbeat(phase="running", desired_state="running")
            except Exception as error:
                failed = await daemon.fail_closed(
                    f"runtime heartbeat failed: {type(error).__name__}: {error}",
                )
                return {
                    **_run_live_snapshot_payload(action, failed, None),
                    **_run_live_stop_report_payload(daemon),
                    "status": "failed",
                    "fault": {
                        "error_type": type(error).__name__,
                        "message": str(error),
                    },
                    "started": _run_live_snapshot_payload(action, snapshot, None),
                }
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
    handler = getattr(daemon, "operator_command_handler", None)
    if handler is not None:
        handled = handler(command)
        if handled is not None:
            return handled
    switch = KillSwitch((), getattr(daemon, "clock", None), daemon.application.store)
    if command_type is OperatorCommandType.PAUSE_NEW_ORDERS:
        at = daemon.clock.now()
        daemon.application.store.set_runtime_state(
            "risk_runtime:last",
            {
                "run_id": daemon.run_id,
                "status": "paused",
                "actor": str(getattr(command, "actor")),
                "reason": str(getattr(command, "reason")),
                "updated_at": at.isoformat(),
            },
            at,
        )
        snapshot = daemon.status()
        return {
            "phase": snapshot.phase.value,
            "desired_state": "paused",
            "paused": True,
        }
    if command_type is OperatorCommandType.RESUME:
        at = daemon.clock.now()
        daemon.application.store.set_runtime_state(
            "risk_runtime:last",
            {
                "run_id": daemon.run_id,
                "status": "ok",
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
            "paused": False,
        }
    if command_type is OperatorCommandType.SET_REDUCE_ONLY:
        if daemon.application.status in {
            RuntimeStatus.READY,
            RuntimeStatus.RUNNING,
            RuntimeStatus.DEGRADED,
            RuntimeStatus.REDUCE_ONLY,
        } and daemon.application.status is not RuntimeStatus.REDUCE_ONLY:
            daemon.application.degrade(str(getattr(command, "reason")), reduce_only=True)
        snapshot = daemon.mark_reduce_only(str(getattr(command, "reason")))
        return {
            "phase": snapshot.phase.value,
            "desired_state": "reduce_only",
            "reduce_only": True,
        }
    if command_type is OperatorCommandType.CLEAR_REDUCE_ONLY:
        if daemon.application.status is RuntimeStatus.REDUCE_ONLY:
            daemon.application.clear_reduce_only(str(getattr(command, "reason")))
        snapshot = daemon.mark_running(str(getattr(command, "reason")))
        return {
            "phase": snapshot.phase.value,
            "desired_state": "running",
            "reduce_only": False,
        }
    if command_type is OperatorCommandType.CANCEL_ALL:
        if daemon.application.status in {
            RuntimeStatus.READY,
            RuntimeStatus.RUNNING,
            RuntimeStatus.DEGRADED,
            RuntimeStatus.REDUCE_ONLY,
        } and daemon.application.status is not RuntimeStatus.REDUCE_ONLY:
            daemon.application.degrade(str(getattr(command, "reason")), reduce_only=True)
        at = daemon.clock.now()
        daemon.application.store.set_runtime_state(
            f"cancel_all:{daemon.run_id}:last",
            {
                "run_id": daemon.run_id,
                "status": "requested",
                "actor": str(getattr(command, "actor")),
                "reason": str(getattr(command, "reason")),
                "updated_at": at.isoformat(),
                "note": "global venue cancellation adapter not yet bound; runtime entered reduce-only",
            },
            at,
        )
        snapshot = daemon.mark_reduce_only(str(getattr(command, "reason")))
        return {
            "phase": snapshot.phase.value,
            "desired_state": "reduce_only",
            "cancel_all_requested": True,
            "cancelled_orders": (),
            "failures": (),
        }
    if command_type is OperatorCommandType.REQUEST_STATUS_SNAPSHOT:
        snapshot = daemon.status()
        at = daemon.clock.now()
        state = snapshot.manifest()
        daemon.application.store.set_runtime_state(f"status_snapshot:{daemon.run_id}:last", state, at)
        return {
            "phase": snapshot.phase.value,
            "desired_state": snapshot.phase.value,
            "snapshot_hash": snapshot.snapshot_hash,
        }
    if command_type is OperatorCommandType.REQUEST_RECONCILIATION:
        at = daemon.clock.now()
        daemon.application.store.set_runtime_state(
            f"reconciliation_request:{daemon.run_id}:last",
            {
                "run_id": daemon.run_id,
                "status": "requested",
                "actor": str(getattr(command, "actor")),
                "reason": str(getattr(command, "reason")),
                "updated_at": at.isoformat(),
            },
            at,
        )
        snapshot = daemon.status()
        return {
            "phase": snapshot.phase.value,
            "desired_state": snapshot.phase.value,
            "reconciliation_requested": True,
        }
    if command_type is OperatorCommandType.TARGET_POSITION:
        payload = dict(getattr(command, "payload", {}) or {})
        legs = tuple(payload.get("legs") or ())
        if not legs:
            raise ValueError("target position command requires legs")
        at = daemon.clock.now()
        state = {
            "run_id": daemon.run_id,
            "status": "accepted",
            "intent_id": str(payload.get("intent_id") or getattr(command, "command_id")),
            "legs": legs,
            "actor": str(getattr(command, "actor")),
            "reason": str(getattr(command, "reason")),
            "updated_at": at.isoformat(),
            "execution_status": "not_submitted",
            "note": "target position accepted by runtime control plane; strategy/execution translation is not enabled yet",
        }
        daemon.application.store.set_runtime_state(f"target_position:{daemon.run_id}:last", state, at)
        snapshot = daemon.status()
        return {
            "phase": snapshot.phase.value,
            "desired_state": snapshot.phase.value,
            "target_position_accepted": True,
            "intent_id": state["intent_id"],
            "leg_count": len(legs),
            "execution_status": "not_submitted",
        }
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


def _run_live_apply_reconciliation_command(
    daemon: object,
    command: object,
    reconciliation_services: object,
) -> dict[str, object]:
    from kairospy.governance.reconciliation import reconciliation_payload
    from kairospy.runtime.application import RuntimeStatus

    services = dict(reconciliation_services)
    reports = []
    mismatches = []
    unknown_external_open_order_ids = []
    store = daemon.application.store
    run_id = daemon.run_id
    at = daemon.application.clock.now()
    for account, service in services.items():
        report = service.reconcile(account)
        payload = reconciliation_payload(report, run_id)
        store.set_runtime_state(f"reconciliation:{run_id}:{account.value}", payload, report.checked_at)
        store.set_runtime_state("reconciliation:last", payload, report.checked_at)
        reports.append(payload)
        unknown_external_open_order_ids.extend(tuple(payload["unknown_external_open_order_ids"]))
        if not report.matched:
            mismatches.append(account.value)
    if mismatches and daemon.application.status in {
        RuntimeStatus.READY,
        RuntimeStatus.RUNNING,
        RuntimeStatus.DEGRADED,
        RuntimeStatus.REDUCE_ONLY,
    } and daemon.application.status is not RuntimeStatus.REDUCE_ONLY:
        daemon.application.degrade(
            "reconciliation mismatches: " + ",".join(mismatches),
            reduce_only=True,
        )
    unique_unknown_external_open_order_ids = tuple(dict.fromkeys(unknown_external_open_order_ids))
    state = {
        "run_id": run_id,
        "status": "matched" if not mismatches else "mismatched",
        "actor": str(getattr(command, "actor")),
        "reason": str(getattr(command, "reason")),
        "updated_at": at.isoformat(),
        "unknown_external_open_order_ids": unique_unknown_external_open_order_ids,
        "unknown_external_open_order_count": len(unique_unknown_external_open_order_ids),
        "reports": tuple(reports),
    }
    store.set_runtime_state(f"reconciliation_request:{run_id}:last", state, at)
    snapshot = daemon.mark_reduce_only(str(getattr(command, "reason"))) if mismatches else daemon.status()
    return {
        "phase": snapshot.phase.value,
        "desired_state": "reduce_only" if mismatches else snapshot.phase.value,
        "reconciliation_requested": True,
        "matched": not mismatches,
        "mismatched_accounts": tuple(mismatches),
        "unknown_external_open_order_ids": unique_unknown_external_open_order_ids,
    }


def _run_live_status_payload(
    action: str,
    run_id: str,
    runtime_database: Path,
    state: dict[str, object] | None,
    stop_report: dict[str, object] | None = None,
    operator_commands: tuple[dict[str, object], ...] = (),
    heartbeat: dict[str, object] | None = None,
    live_binding: dict[str, object] | None = None,
    risk_state: dict[str, object] | None = None,
    cancel_all_state: dict[str, object] | None = None,
    reconciliation_request: dict[str, object] | None = None,
    status_snapshot_command: dict[str, object] | None = None,
    recovery_state: dict[str, object] | None = None,
    outbox_state: dict[str, object] | None = None,
    fill_ingestion_state: dict[str, object] | None = None,
    market_freshness_state: dict[str, object] | None = None,
    target_position_state: dict[str, object] | None = None,
) -> dict[str, object]:
    if state is None:
        structured_log_file = _run_live_structured_log_path(runtime_database.parent.parent)
        payload = {
            "product": "run",
            "operation": "live",
            "live_action": action,
            "run_id": run_id,
            "status": "not_started",
            "phase": "created",
            "runtime_database": str(runtime_database),
            "state_key": f"live_run_daemon:{run_id}",
            "structured_log_file": str(structured_log_file),
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
        if risk_state is not None:
            payload["risk_state"] = risk_state
        if cancel_all_state is not None:
            payload["cancel_all"] = cancel_all_state
        if reconciliation_request is not None:
            payload["reconciliation_request"] = reconciliation_request
        if status_snapshot_command is not None:
            payload["status_snapshot_command"] = status_snapshot_command
        if recovery_state is not None:
            payload["recovery_state"] = recovery_state
        if outbox_state is not None:
            payload["outbox_state"] = outbox_state
        if fill_ingestion_state is not None:
            payload["fill_ingestion_state"] = fill_ingestion_state
        if market_freshness_state is not None:
            payload["market_freshness_state"] = market_freshness_state
        if target_position_state is not None:
            payload["target_position"] = target_position_state
        payload["health"] = _run_live_health_summary(
            payload,
            heartbeat=heartbeat,
            risk_state=risk_state,
            recovery_state=recovery_state,
            reconciliation_request=reconciliation_request,
        )
        payload["metrics"] = _run_live_metrics_summary(payload)
        return payload
    status = str(state.get("phase", "unknown"))
    if heartbeat is not None:
        status = str(heartbeat.get("status", status))
    spawn = state.get("spawn") if isinstance(state.get("spawn"), dict) else {}
    structured_log_file = str(spawn.get("structured_log_file") or _run_live_structured_log_path(runtime_database.parent.parent))
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
        "structured_log_file": structured_log_file,
        **({"log_file": str(spawn.get("log_file"))} if spawn.get("log_file") else {}),
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
    if risk_state is not None:
        payload["risk_state"] = risk_state
    if cancel_all_state is not None:
        payload["cancel_all"] = cancel_all_state
    if reconciliation_request is not None:
        payload["reconciliation_request"] = reconciliation_request
    if status_snapshot_command is not None:
        payload["status_snapshot_command"] = status_snapshot_command
    if recovery_state is not None:
        payload["recovery_state"] = recovery_state
    if outbox_state is not None:
        payload["outbox_state"] = outbox_state
    if fill_ingestion_state is not None:
        payload["fill_ingestion_state"] = fill_ingestion_state
    if market_freshness_state is not None:
        payload["market_freshness_state"] = market_freshness_state
    if target_position_state is not None:
        payload["target_position"] = target_position_state
    payload["health"] = _run_live_health_summary(
        payload,
        heartbeat=heartbeat,
        risk_state=risk_state,
        recovery_state=recovery_state,
        reconciliation_request=reconciliation_request,
    )
    payload["metrics"] = _run_live_metrics_summary(payload)
    return payload


def _run_live_metrics_payload(
    action: str,
    run_id: str,
    runtime_database: Path,
    status_payload: dict[str, object],
) -> dict[str, object]:
    return {
        "product": "run",
        "operation": "live",
        "live_action": action,
        "run_id": run_id,
        "status": "ok",
        "runtime_database": str(runtime_database),
        "metrics": _run_live_metrics_summary(status_payload),
        "health": status_payload.get("health"),
    }


def _attach_run_live_metrics_artifact(
    payload: dict[str, object],
    *,
    output: str | Path | None,
    prometheus: bool,
) -> dict[str, object]:
    if not prometheus and output is None:
        return payload
    rendered: str
    metrics_format: str
    if prometheus:
        rendered = _run_live_prometheus_metrics(
            str(payload.get("run_id") or ""),
            payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {},
        )
        metrics_format = "prometheus"
    else:
        rendered = json.dumps(_json_safe(payload.get("metrics", {})), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        metrics_format = "json"
    payload["metrics_format"] = metrics_format
    if output is None:
        payload["metrics_text"] = rendered
        payload["artifact_hash"] = sha256(rendered.encode("utf-8")).hexdigest()
        return payload
    artifact = Path(output)
    _write_text(artifact, rendered)
    payload["artifact"] = str(artifact)
    payload["artifact_hash"] = _file_sha256(artifact)
    return payload


def _run_live_prometheus_metrics(run_id: str, metrics: dict[str, object]) -> str:
    labels = f'run_id="{_prometheus_label_escape(run_id)}"'
    lines = [
        "# HELP kairospy_run_info Runtime metrics export metadata.",
        "# TYPE kairospy_run_info gauge",
        f"kairospy_run_info{{{labels}}} 1",
    ]
    names = {
        "heartbeat_age_seconds": "kairospy_run_heartbeat_age_seconds",
        "heartbeat_stale": "kairospy_run_heartbeat_stale",
        "operator_command_count": "kairospy_run_operator_command_count",
        "operator_command_backlog": "kairospy_run_operator_command_backlog",
        "service_count": "kairospy_run_service_count",
        "service_restart_count": "kairospy_run_service_restart_count",
        "failed_service_count": "kairospy_run_failed_service_count",
        "risk_blocked": "kairospy_run_risk_blocked",
        "risk_reason_count": "kairospy_run_risk_reason_count",
        "unresolved_order_count": "kairospy_run_unresolved_order_count",
        "orders_requiring_recovery_count": "kairospy_run_orders_requiring_recovery_count",
        "open_incident_count": "kairospy_run_open_incident_count",
        "outbox_pending_count": "kairospy_run_outbox_pending_count",
        "outbox_dispatching_count": "kairospy_run_outbox_dispatching_count",
        "outbox_unknown_count": "kairospy_run_outbox_unknown_count",
        "outbox_backlog_count": "kairospy_run_outbox_backlog_count",
        "order_submit_latency_last_ms": "kairospy_run_order_submit_latency_last_ms",
        "order_submit_latency_max_ms": "kairospy_run_order_submit_latency_max_ms",
        "order_ack_latency_last_ms": "kairospy_run_order_ack_latency_last_ms",
        "order_ack_latency_max_ms": "kairospy_run_order_ack_latency_max_ms",
        "fill_ingestion_latency_last_ms": "kairospy_run_fill_ingestion_latency_last_ms",
        "fill_ingestion_latency_max_ms": "kairospy_run_fill_ingestion_latency_max_ms",
        "market_freshness_passed": "kairospy_run_market_freshness_passed",
        "market_freshness_max_age_seconds": "kairospy_run_market_freshness_max_age_seconds",
        "market_freshness_updated_age_seconds": "kairospy_run_market_freshness_updated_age_seconds",
        "market_event_age_seconds": "kairospy_run_market_event_age_seconds",
        "market_freshness_channel_failure_count": "kairospy_run_market_freshness_channel_failure_count",
    }
    for key, metric_name in names.items():
        value = metrics.get(key)
        if value is None:
            continue
        lines.append(f"# TYPE {metric_name} gauge")
        lines.append(f"{metric_name}{{{labels}}} {_prometheus_value(value)}")
    health_status = str(metrics.get("health_status") or "unknown")
    status_labels = f'{labels},status="{_prometheus_label_escape(health_status)}"'
    lines.append("# TYPE kairospy_run_health_status gauge")
    lines.append(f"kairospy_run_health_status{{{status_labels}}} 1")
    market_freshness_status = str(metrics.get("market_freshness_status") or "unknown")
    market_labels = f'{labels},status="{_prometheus_label_escape(market_freshness_status)}"'
    lines.append("# TYPE kairospy_run_market_freshness_status gauge")
    lines.append(f"kairospy_run_market_freshness_status{{{market_labels}}} 1")
    run_status = str(metrics.get("run_status") or "unknown")
    run_phase = str(metrics.get("run_phase") or "unknown")
    run_labels = (
        f'{labels},status="{_prometheus_label_escape(run_status)}",'
        f'phase="{_prometheus_label_escape(run_phase)}"'
    )
    lines.append("# TYPE kairospy_run_status gauge")
    lines.append(f"kairospy_run_status{{{run_labels}}} 1")
    return "\n".join(lines) + "\n"


def _prometheus_value(value: object) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    try:
        return str(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "0"


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _prometheus_label_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _run_live_metrics_summary(payload: dict[str, object]) -> dict[str, object]:
    heartbeat = payload.get("heartbeat")
    risk_state = payload.get("risk_state")
    recovery_state = payload.get("recovery_state")
    outbox_state = payload.get("outbox_state")
    fill_ingestion_state = payload.get("fill_ingestion_state")
    market_freshness_state = payload.get("market_freshness_state")
    commands = payload.get("operator_commands", ())
    services = payload.get("services", ())
    incidents = payload.get("incidents", ())
    metrics: dict[str, object] = {
        "run_status": str(payload.get("status") or "unknown"),
        "run_phase": str(payload.get("phase") or "unknown"),
        "health_status": (
            str(payload.get("health", {}).get("status"))
            if isinstance(payload.get("health"), dict) else "unknown"
        ),
        "heartbeat_age_seconds": None,
        "heartbeat_stale": False,
        "operator_command_count": 0,
        "operator_command_backlog": 0,
        "service_count": 0,
        "service_restart_count": 0,
        "failed_service_count": 0,
        "risk_blocked": False,
        "risk_reason_count": 0,
        "unresolved_order_count": 0,
        "orders_requiring_recovery_count": 0,
        "open_incident_count": int(payload.get("open_incident_count") or 0),
        "outbox_pending_count": 0,
        "outbox_dispatching_count": 0,
        "outbox_unknown_count": 0,
        "outbox_backlog_count": 0,
        "order_submit_latency_last_ms": None,
        "order_submit_latency_max_ms": None,
        "order_ack_latency_last_ms": None,
        "order_ack_latency_max_ms": None,
        "fill_ingestion_latency_last_ms": None,
        "fill_ingestion_latency_max_ms": None,
        "market_freshness_status": "unknown",
        "market_freshness_passed": False,
        "market_freshness_max_age_seconds": None,
        "market_freshness_updated_age_seconds": None,
        "market_event_age_seconds": None,
        "market_freshness_channel_failure_count": 0,
    }
    if isinstance(heartbeat, dict):
        metrics["heartbeat_age_seconds"] = heartbeat.get("heartbeat_age_seconds")
        metrics["heartbeat_stale"] = bool(heartbeat.get("stale"))
    if isinstance(commands, (tuple, list)):
        metrics["operator_command_count"] = len(commands)
        metrics["operator_command_backlog"] = sum(
            1 for command in commands
            if isinstance(command, dict) and command.get("status") in {"pending", "claimed", "accepted", "running"}
        )
    if isinstance(services, (tuple, list)):
        metrics["service_count"] = len(services)
        restart_count = 0
        failed_count = 0
        for service in services:
            if not isinstance(service, dict):
                continue
            restart_count += int(service.get("restart_count") or 0)
            if str(service.get("status") or "").endswith("failed"):
                failed_count += 1
        metrics["service_restart_count"] = restart_count
        metrics["failed_service_count"] = failed_count
    if isinstance(risk_state, dict):
        metrics["risk_blocked"] = str(risk_state.get("status") or risk_state.get("phase") or "") == "blocking"
        reasons = risk_state.get("reasons", ())
        metrics["risk_reason_count"] = len(reasons) if isinstance(reasons, (tuple, list)) else int(bool(reasons))
    if isinstance(recovery_state, dict):
        metrics["unresolved_order_count"] = int(recovery_state.get("unresolved_order_count") or 0)
        metrics["orders_requiring_recovery_count"] = int(recovery_state.get("orders_requiring_recovery_count") or 0)
    if isinstance(incidents, (tuple, list)):
        metrics["open_incident_count"] = len(incidents)
    if isinstance(outbox_state, dict):
        for key in (
            "outbox_pending_count",
            "outbox_dispatching_count",
            "outbox_unknown_count",
            "outbox_backlog_count",
        ):
            metrics[key] = int(outbox_state.get(key) or 0)
        for key in (
            "order_submit_latency_last_ms",
            "order_submit_latency_max_ms",
            "order_ack_latency_last_ms",
            "order_ack_latency_max_ms",
        ):
            metrics[key] = _optional_float(outbox_state.get(key))
    if isinstance(fill_ingestion_state, dict):
        for key in ("fill_ingestion_latency_last_ms", "fill_ingestion_latency_max_ms"):
            metrics[key] = _optional_float(fill_ingestion_state.get(key))
    if isinstance(market_freshness_state, dict):
        metrics["market_freshness_status"] = str(market_freshness_state.get("freshness_status") or "unknown")
        metrics["market_freshness_passed"] = bool(market_freshness_state.get("freshness_passed"))
        metrics["market_freshness_max_age_seconds"] = _optional_float(market_freshness_state.get("freshness_max_age_seconds"))
        metrics["market_freshness_updated_age_seconds"] = _optional_float(market_freshness_state.get("freshness_updated_age_seconds"))
        metrics["market_event_age_seconds"] = _optional_float(market_freshness_state.get("market_event_age_seconds"))
        metrics["market_freshness_channel_failure_count"] = int(market_freshness_state.get("channel_failure_count") or 0)
    return metrics


def _run_live_health_summary(
    payload: dict[str, object],
    *,
    heartbeat: dict[str, object] | None = None,
    risk_state: dict[str, object] | None = None,
    recovery_state: dict[str, object] | None = None,
    reconciliation_request: dict[str, object] | None = None,
) -> dict[str, object]:
    status = str(payload.get("status") or payload.get("phase") or "unknown")
    reasons: list[str] = []
    severity = "ok"
    if status in {"not_started", "stopped"}:
        severity = "inactive"
    if status in {"stale", "failed", "reduce_only", "unknown_external_state", "failed_start"}:
        severity = _max_health_severity(severity, status)
        reasons.append(f"runtime_{status}")
    if bool(payload.get("stop_requested")):
        severity = _max_health_severity(severity, "stopping")
        reasons.append("stop_requested")
    if heartbeat is not None and bool(heartbeat.get("stale")):
        severity = _max_health_severity(severity, "stale")
        reasons.append("heartbeat_stale")
    if risk_state is not None:
        risk_status = str(risk_state.get("status") or risk_state.get("phase") or "")
        if risk_status in {"blocking", "failed", "stale"}:
            severity = _max_health_severity(severity, risk_status)
            reasons.append(f"risk_{risk_status}")
        raw_reasons = risk_state.get("reasons", ())
        if isinstance(raw_reasons, (tuple, list)):
            reasons.extend(str(item) for item in raw_reasons)
        elif raw_reasons:
            reasons.append(str(raw_reasons))
    if recovery_state is not None:
        unresolved_count = int(recovery_state.get("unresolved_order_count") or 0)
        if unresolved_count:
            severity = _max_health_severity(severity, "blocking")
            reasons.append("unresolved_orders")
        requiring_count = int(recovery_state.get("orders_requiring_recovery_count") or 0)
        if requiring_count and unresolved_count:
            reasons.append("orders_requiring_venue_recovery")
    if reconciliation_request is not None and str(reconciliation_request.get("status")) == "mismatched":
        severity = _max_health_severity(severity, "blocking")
        reasons.append("reconciliation_mismatch")
        if int(reconciliation_request.get("unknown_external_open_order_count") or 0):
            reasons.append("unknown_external_open_orders")
    services = payload.get("services", ())
    failed_services = []
    if isinstance(services, (tuple, list)):
        for service in services:
            if not isinstance(service, dict):
                continue
            service_status = str(service.get("status") or "")
            if service_status.endswith("failed") or service_status == "failed":
                failed_services.append(str(service.get("name") or "unknown"))
    if failed_services:
        severity = _max_health_severity(severity, "failed")
        reasons.append("managed_service_failed")
    unique_reasons = tuple(dict.fromkeys(reason for reason in reasons if reason))
    return {
        "status": severity,
        "healthy": severity == "ok" and not unique_reasons,
        "reasons": unique_reasons,
        "failed_services": tuple(failed_services),
    }


def _max_health_severity(current: str, candidate: str) -> str:
    order = {
        "ok": 0,
        "inactive": 1,
        "stopping": 2,
        "reduce_only": 3,
        "stale": 4,
        "blocking": 5,
        "unknown_external_state": 5,
        "failed_start": 5,
        "failed": 6,
    }
    return candidate if order.get(candidate, 0) > order.get(current, 0) else current


def _sync_run_live_health_incident(store: object, run_id: str, payload: dict[str, object], at: datetime) -> None:
    if not all(hasattr(store, name) for name in ("record_runtime_incident", "runtime_incidents", "close_runtime_incident")):
        return
    health = payload.get("health")
    if not isinstance(health, dict):
        return
    incident_id = f"runtime-health:{run_id}"
    status = str(health.get("status") or "unknown")
    healthy = bool(health.get("healthy"))
    if healthy or status in {"ok", "inactive"}:
        for incident in store.runtime_incidents(run_id, status="open", limit=25):
            if getattr(incident, "incident_id", None) == incident_id:
                store.close_runtime_incident(
                    incident_id,
                    actor="system",
                    reason="runtime health recovered",
                    at=at,
                )
                break
        return
    severity = "critical" if status in {"blocking", "failed"} else "warning"
    store.record_runtime_incident(
        incident_id=incident_id,
        run_id=run_id,
        severity=severity,
        title=f"runtime health {status}",
        details={
            "health": health,
            "status": payload.get("status"),
            "phase": payload.get("phase"),
            "reason": payload.get("reason"),
            "snapshot_hash": payload.get("snapshot_hash"),
        },
        at=at,
    )


def _run_live_commands_payload(
    action: str,
    run_id: str,
    runtime_database: Path,
    bus: object,
    *,
    limit: int,
) -> dict[str, object]:
    if limit <= 0:
        raise ValueError("run live commands --limit must be positive")
    commands = tuple(command.manifest() for command in bus.commands(run_id, limit=limit))
    return {
        "product": "run",
        "operation": "live",
        "live_action": action,
        "run_id": run_id,
        "status": "ok",
        "runtime_database": str(runtime_database),
        "operator_commands": commands,
        "commands_count": len(commands),
    }


def _run_live_incidents_payload(
    action: str,
    run_id: str,
    runtime_database: Path,
    incidents: tuple[dict[str, object], ...],
    *,
    status: str,
) -> dict[str, object]:
    return {
        "product": "run",
        "operation": "live",
        "live_action": action,
        "run_id": run_id,
        "status": "ok",
        "runtime_database": str(runtime_database),
        "incident_status": status,
        "incidents": incidents,
        "incidents_count": len(incidents),
    }


def _export_run_live_artifacts(
    run_id: str,
    runtime_database: Path,
    export_dir: Path,
    *,
    status_payload: dict[str, object],
    commands: tuple[dict[str, object], ...],
    incidents: tuple[dict[str, object], ...],
    runtime_state: dict[str, object],
) -> dict[str, object]:
    export_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "status": export_dir / "status.json",
        "metrics": export_dir / "metrics.json",
        "commands": export_dir / "commands.json",
        "incidents": export_dir / "incidents.json",
        "runtime_state": export_dir / "runtime_state.json",
    }
    runtime_log = _run_live_structured_log_path(runtime_database.parent.parent)
    if runtime_log.exists():
        files["runtime_log"] = export_dir / "runtime_log.jsonl"
    _write_json(files["status"], status_payload)
    _write_json(files["metrics"], status_payload.get("metrics", {}))
    _write_json(files["commands"], {"run_id": run_id, "operator_commands": commands, "commands_count": len(commands)})
    _write_json(files["incidents"], {"run_id": run_id, "incidents": incidents, "incidents_count": len(incidents)})
    _write_json(files["runtime_state"], {"run_id": run_id, "runtime_state": runtime_state})
    if "runtime_log" in files:
        _write_text(files["runtime_log"], runtime_log.read_text(encoding="utf-8"))
    artifact_hashes = {name: _file_sha256(path) for name, path in files.items()}
    manifest = {
        "schema_version": 1,
        "product": "run",
        "operation": "export",
        "runtime_kind": "live",
        "run_id": run_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "runtime_database": str(runtime_database),
        "files": {name: str(path) for name, path in files.items()},
        "artifact_hashes": artifact_hashes,
        "status": status_payload.get("status"),
        "health": status_payload.get("health"),
        "metrics": status_payload.get("metrics"),
    }
    manifest["export_hash"] = _stable_hash(_json_safe({key: value for key, value in manifest.items() if key != "export_hash"}))
    manifest_path = export_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "product": "run",
        "operation": "live",
        "live_action": "export",
        "run_id": run_id,
        "status": "exported",
        "runtime_database": str(runtime_database),
        "artifact": str(export_dir),
        "manifest": str(manifest_path),
        "export_hash": manifest["export_hash"],
        "artifact_hashes": artifact_hashes,
        "files": manifest["files"],
    }


def _timestamp_slug(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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


def _write_text(path: str | Path, payload: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(target)


def _args(root: str | Path, **kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(lake_root=Path(root), **kwargs)
