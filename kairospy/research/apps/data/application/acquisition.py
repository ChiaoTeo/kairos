"""Explicit execution of reviewed data acquisition plans."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Mapping

from kairospy.investment.apps.market.application.cli import MarketCliApplication
from kairospy.investment.apps.reference.application.cli import ReferenceCliApplication
from kairospy.system.apps.workspace.application import Workspace
from .catalog import (
    DatasetCatalogApplication,
    dataset_id_for_requirement,
    event_payload,
    event_time,
)
from .models import (
    AcquisitionStep,
    DataAcquisitionPlan,
    DataRequirement,
    DatasetRef,
    DatasetSetRef,
)


@dataclass(frozen=True, slots=True)
class DataAcquisitionStepExecution:
    index: int
    owner: str
    kind: str
    subject: str
    provider: str | None
    status: str
    attempts: int
    started_at: str | None = None
    updated_at: str | None = None
    datasets: tuple[DatasetRef, ...] = ()
    error: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> DataAcquisitionStepExecution:
        raw_datasets = value.get("datasets", [])
        if not isinstance(raw_datasets, list):
            raise ValueError("data acquisition step datasets must be an array")
        datasets: list[DatasetRef] = []
        for raw_dataset in raw_datasets:
            if not isinstance(raw_dataset, Mapping):
                raise ValueError("data acquisition step dataset must be an object")
            datasets.append(DatasetRef.from_dict(raw_dataset))
        return cls(
            index=_required_int(value, "index"),
            owner=_required_string(value, "owner"),
            kind=_required_string(value, "kind"),
            subject=_required_string(value, "subject"),
            provider=_optional_string(value, "provider"),
            status=_required_string(value, "status"),
            attempts=_required_int(value, "attempts"),
            started_at=_optional_string(value, "started_at"),
            updated_at=_optional_string(value, "updated_at"),
            datasets=tuple(datasets),
            error=_optional_string(value, "error"),
        )

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "index": self.index,
            "owner": self.owner,
            "kind": self.kind,
            "subject": self.subject,
            "provider": self.provider,
            "status": self.status,
            "attempts": self.attempts,
        }
        for key, item in (
            ("started_at", self.started_at),
            ("updated_at", self.updated_at),
            ("error", self.error),
        ):
            if item is not None:
                value[key] = item
        if self.datasets:
            value["datasets"] = [dataset.as_dict() for dataset in self.datasets]
        return value


@dataclass(frozen=True, slots=True)
class DataAcquisitionExecution:
    project_id: str
    plan_hash: str
    status: str
    started_at: str
    max_concurrency: int
    steps: tuple[DataAcquisitionStepExecution, ...]
    completed_at: str | None = None
    result: DatasetSetRef | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> DataAcquisitionExecution:
        if value.get("schema_version") != 1:
            raise ValueError("data acquisition execution schema is unsupported")
        raw_steps = value.get("steps")
        if not isinstance(raw_steps, list):
            raise ValueError("data acquisition execution steps must be an array")
        steps: list[DataAcquisitionStepExecution] = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, Mapping):
                raise ValueError("data acquisition execution step must be an object")
            steps.append(DataAcquisitionStepExecution.from_mapping(raw_step))
        raw_result = value.get("result")
        if raw_result is not None and not isinstance(raw_result, Mapping):
            raise ValueError("data acquisition execution result must be an object")
        max_concurrency = value.get("max_concurrency", 1)
        if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int):
            raise ValueError("data acquisition max_concurrency must be an integer")
        return cls(
            project_id=_required_string(value, "project_id"),
            plan_hash=_required_string(value, "plan_hash"),
            status=_required_string(value, "status"),
            started_at=_required_string(value, "started_at"),
            max_concurrency=max_concurrency,
            steps=tuple(steps),
            completed_at=_optional_string(value, "completed_at"),
            result=(
                DatasetSetRef.from_dict(raw_result)
                if isinstance(raw_result, Mapping)
                else None
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": 1,
            "project_id": self.project_id,
            "plan_hash": self.plan_hash,
            "status": self.status,
            "started_at": self.started_at,
            "max_concurrency": self.max_concurrency,
            "steps": [step.as_dict() for step in self.steps],
        }
        if self.completed_at is not None:
            value["completed_at"] = self.completed_at
        if self.result is not None:
            value["result"] = self.result.as_dict()
        return value


@dataclass(frozen=True, slots=True)
class DataAcquisitionApplication:
    """Owner-routing executor; provider selection remains explicit in the plan."""

    workspace: Workspace
    catalog: DatasetCatalogApplication

    async def execute(
        self, plan: DataAcquisitionPlan, *, max_concurrency: int = 1
    ) -> DatasetSetRef:
        if plan.project_id != self.workspace.workspace_id:
            raise ValueError("data acquisition plan belongs to another Project")
        if isinstance(max_concurrency, bool) or max_concurrency <= 0:
            raise ValueError("data acquisition max_concurrency must be positive")
        journal = self._load_journal(plan)
        journal["max_concurrency"] = max_concurrency
        if max_concurrency > 1:
            return await self._execute_concurrently(
                plan, journal, max_concurrency=max_concurrency
            )
        published: list[DatasetRef] = []
        for index, step in enumerate(plan.steps):
            resolved, _ = self.catalog.resolve((step.requirement,))
            if resolved is not None:
                published.extend(resolved.members)
                self._record_step(
                    journal,
                    index,
                    status="reused",
                    datasets=resolved.members,
                )
                self._write_journal(plan.plan_hash, journal)
                continue
            self._record_step(journal, index, status="running")
            self._write_journal(plan.plan_hash, journal)
            try:
                result = await self._execute_step(
                    step.owner,
                    step.provider,
                    step.requirement,
                    plan.plan_hash,
                    index,
                )
            except Exception as error:
                self._record_step(
                    journal,
                    index,
                    status="failed",
                    error=f"{type(error).__name__}: {error}",
                )
                self._write_journal(plan.plan_hash, journal)
                raise
            published.append(result)
            self._record_step(
                journal,
                index,
                status="published",
                datasets=(result,),
            )
            self._write_journal(plan.plan_hash, journal)
        return self._complete(plan, journal, published)

    async def _execute_concurrently(
        self,
        plan: DataAcquisitionPlan,
        journal: dict[str, Any],
        *,
        max_concurrency: int,
    ) -> DatasetSetRef:
        published: list[DatasetRef] = []
        pending: list[tuple[int, AcquisitionStep]] = []
        for index, step in enumerate(plan.steps):
            resolved, _ = self.catalog.resolve((step.requirement,))
            if resolved is not None:
                published.extend(resolved.members)
                self._record_step(
                    journal, index, status="reused", datasets=resolved.members
                )
                continue
            self._record_step(journal, index, status="running")
            pending.append((index, step))
        self._write_journal(plan.plan_hash, journal)

        semaphore = asyncio.Semaphore(max_concurrency)

        async def run(index: int, step: AcquisitionStep) -> tuple[int, DatasetRef]:
            async with semaphore:
                return index, await self._execute_step(
                    step.owner,
                    step.provider,
                    step.requirement,
                    plan.plan_hash,
                    index,
                )

        values = await asyncio.gather(
            *(run(index, step) for index, step in pending),
            return_exceptions=True,
        )
        first_error: BaseException | None = None
        for (index, _step), value in zip(pending, values, strict=True):
            if isinstance(value, BaseException):
                self._record_step(
                    journal,
                    index,
                    status="failed",
                    error=f"{type(value).__name__}: {value}",
                )
                if first_error is None:
                    first_error = value
                continue
            _, ref = value
            published.append(ref)
            self._record_step(journal, index, status="published", datasets=(ref,))
        if first_error is not None:
            journal["status"] = "failed"
            self._write_journal(plan.plan_hash, journal)
            raise first_error
        self._write_journal(plan.plan_hash, journal)
        return self._complete(plan, journal, published)

    async def _execute_step(
        self,
        owner: str,
        provider: str | None,
        requirement: DataRequirement,
        plan_hash: str,
        index: int,
    ) -> DatasetRef:
        if provider != "massive":
            raise NotImplementedError(
                f"data acquisition provider is not implemented: {provider}"
            )
        if owner == "market":
            return await self._execute_massive_market(requirement, plan_hash, index)
        if owner == "reference":
            return await self._execute_massive_reference(requirement, plan_hash, index)
        raise NotImplementedError(f"data acquisition owner is not implemented: {owner}")

    def _complete(
        self,
        plan: DataAcquisitionPlan,
        journal: dict[str, Any],
        published: list[DatasetRef],
    ) -> DatasetSetRef:
        resolved, missing = self.catalog.resolve(plan.requirements)
        if resolved is not None:
            result = resolved
        else:
            members = {ref.identity: ref for ref in (*plan.satisfied, *published)}
            if not members:
                raise ValueError("data acquisition plan produced no datasets")
            if missing:
                raise ValueError(
                    "data acquisition completed but requirements remain unresolved: "
                    + "; ".join(item.reason for item in missing)
                )
            result = DatasetSetRef(tuple(members[key] for key in sorted(members)))
        journal["status"] = "complete"
        journal["completed_at"] = _now()
        journal["result"] = result.as_dict()
        self._write_journal(plan.plan_hash, journal)
        return result

    def execution(self, plan_hash: str) -> DataAcquisitionExecution:
        path = self._journal_path(plan_hash)
        if not path.is_file():
            raise FileNotFoundError(
                f"data acquisition execution does not exist: {plan_hash}"
            )
        value: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("data acquisition execution journal is invalid")
        return DataAcquisitionExecution.from_mapping(value)

    def _load_journal(self, plan: DataAcquisitionPlan) -> dict[str, Any]:
        path = self._journal_path(plan.plan_hash)
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("plan_hash") != plan.plan_hash:
                raise ValueError("data acquisition execution journal is invalid")
            return value
        return {
            "schema_version": 1,
            "project_id": plan.project_id,
            "plan_hash": plan.plan_hash,
            "status": "running",
            "started_at": _now(),
            "steps": [
                {
                    "index": index,
                    "owner": step.owner,
                    "kind": step.requirement.kind,
                    "subject": step.requirement.subject,
                    "provider": step.provider,
                    "status": "planned",
                    "attempts": 0,
                }
                for index, step in enumerate(plan.steps)
            ],
        }

    @staticmethod
    def _record_step(
        journal: dict[str, Any],
        index: int,
        *,
        status: str,
        datasets: tuple[DatasetRef, ...] = (),
        error: str | None = None,
    ) -> None:
        step = journal["steps"][index]
        if status == "running":
            step["attempts"] = int(step.get("attempts", 0)) + 1
            step["started_at"] = _now()
            step.pop("error", None)
        step["status"] = status
        step["updated_at"] = _now()
        if datasets:
            step["datasets"] = [item.as_dict() for item in datasets]
        if error is not None:
            step["error"] = error
            journal["status"] = "failed"
        else:
            journal["status"] = "running"

    def _journal_path(self, plan_hash: str) -> Path:
        if len(plan_hash) != 64 or any(
            character not in "0123456789abcdef" for character in plan_hash
        ):
            raise ValueError("data acquisition plan hash is invalid")
        return self.workspace.paths.child(
            "state", "data", "acquisitions", plan_hash, "execution.json"
        )

    def _write_journal(self, plan_hash: str, journal: Mapping[str, Any]) -> None:
        path = self._journal_path(plan_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(journal, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    async def _execute_massive_market(
        self,
        requirement: DataRequirement,
        plan_hash: str,
        index: int,
    ) -> DatasetRef:
        if requirement.kind not in {"bar", "quote", "trade"}:
            raise NotImplementedError(
                f"Massive historical kind is not implemented: {requirement.kind}"
            )
        if (
            requirement.start_time_unix_nanos is None
            or requirement.end_time_unix_nanos is None
        ):
            raise ValueError(
                "Massive historical acquisition requires a bounded interval"
            )
        symbol = _parameter(requirement, "symbol")
        instrument_id = _parameter(requirement, "instrument_id")
        market_type = requirement.product or requirement.parameters.get(
            "market_type", "equity"
        )
        if market_type == "options":
            market_type = "option"
        staging = self.workspace.paths.child(
            "state",
            "data",
            "acquisitions",
            plan_hash,
            f"{index:04d}-{requirement.kind}.jsonl",
        )
        arguments = [
            "download",
            "--provider",
            "massive",
            "--credential-id",
            requirement.parameters.get("credential_id", "massive-readonly"),
            "--market-type",
            market_type,
            "--data-kind",
            requirement.kind,
            "--symbol",
            symbol,
            "--instrument-id",
            instrument_id,
            "--start",
            str(requirement.start_time_unix_nanos // 1_000_000),
            "--end",
            str(requirement.end_time_unix_nanos // 1_000_000),
            "--dataset-id",
            f"acquisition-{plan_hash[:16]}-{index}",
            "--file",
            str(staging),
        ]
        if network_id := requirement.parameters.get("network_id"):
            arguments.extend(("--network-id", network_id))
        if interval := requirement.parameters.get("interval"):
            arguments.extend(("--interval", interval))
        if requirement.parameters.get("adjusted", "false").lower() == "true":
            arguments.append("--adjusted")
        if endpoint := requirement.parameters.get("endpoint"):
            arguments.extend(("--endpoint", endpoint))
        result = await asyncio.to_thread(
            MarketCliApplication(self.workspace).run, arguments
        )
        staged_events = tuple(
            json.loads(line)
            for line in staging.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        if int(result.get("event_count", -1)) != len(staged_events):
            raise ValueError("provider manifest and staged event count differ")
        events = tuple(
            event
            for event in staged_events
            if (
                (timestamp := event_time(event)) is not None
                and requirement.start_time_unix_nanos
                <= timestamp
                <= requirement.end_time_unix_nanos
            )
        )
        quality = _validate_market_events(events, requirement)
        quality = {
            **quality,
            "provider_event_count": len(staged_events),
            "out_of_window_events": len(staged_events) - len(events),
        }
        dataset_id = dataset_id_for_requirement(requirement)
        return self.catalog.publish_partitions(
            dataset_id=dataset_id,
            owner=requirement.owner,
            kind=requirement.kind,
            subject=requirement.subject,
            product=requirement.product,
            source=requirement.source,
            venue=requirement.venue,
            cadence=requirement.cadence,
            schema_version=requirement.schema_version or "1",
            quality_status="validated",
            partitions=_event_date_partitions(events),
            lineage={
                "provider": "massive",
                "credential_id": requirement.parameters.get(
                    "credential_id", "massive-readonly"
                ),
                "source_symbol": symbol,
                "provider_manifest": result,
                "acquisition_plan_hash": plan_hash,
                "documentation": {
                    "quotes": "https://massive.com/docs/rest/options/trades-quotes/quotes",
                    "trades": "https://massive.com/docs/rest/options/trades-quotes/trades",
                    "bars": "https://massive.com/docs/rest/options/aggregates/custom-bars",
                }.get(requirement.kind),
            },
            quality_report=quality,
            coverage_start_time_unix_nanos=requirement.start_time_unix_nanos,
            coverage_end_time_unix_nanos=requirement.end_time_unix_nanos,
        )

    async def _execute_massive_reference(
        self,
        requirement: DataRequirement,
        plan_hash: str,
        index: int,
    ) -> DatasetRef:
        if requirement.kind not in {"option-contract", "cash-dividend"}:
            raise NotImplementedError(
                f"Massive Reference kind is not implemented: {requirement.kind}"
            )
        if (
            requirement.start_time_unix_nanos is None
            or requirement.end_time_unix_nanos is None
        ):
            raise ValueError("Reference acquisition requires a bounded as-of interval")
        if requirement.kind == "cash-dividend":
            return await self._execute_massive_dividends(requirement, plan_hash, index)
        underlying = requirement.parameters.get("underlying", requirement.subject)
        as_of = _parameter(requirement, "as_of")
        expiration_start = _parameter(requirement, "expiration_start")
        expiration_end = _parameter(requirement, "expiration_end")
        staging = self.workspace.paths.child(
            "state",
            "data",
            "acquisitions",
            plan_hash,
            f"{index:04d}-option-contract.jsonl",
        )
        arguments = [
            "prepare-option-contracts",
            "--underlying",
            underlying,
            "--as-of",
            as_of,
            "--expiration-start",
            expiration_start,
            "--expiration-end",
            expiration_end,
            "--option-right",
            requirement.parameters.get("option_right", "put"),
            "--credential-id",
            requirement.parameters.get("credential_id", "massive-readonly"),
            "--file",
            str(staging),
        ]
        if endpoint := requirement.parameters.get("endpoint"):
            arguments.extend(("--endpoint", endpoint))
        result = await asyncio.to_thread(
            ReferenceCliApplication(self.workspace).run, arguments
        )
        events = tuple(
            json.loads(line)
            for line in staging.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        quality = _validate_reference_contracts(events, requirement)
        if int(result.get("record_count", -1)) != len(events):
            raise ValueError("Reference provider manifest and staged count differ")
        snapshot_id = str(result.get("snapshot_id") or "").strip()
        if not snapshot_id:
            raise ValueError("Reference provider manifest has no snapshot identity")
        return self.catalog.publish_partitions(
            dataset_id=dataset_id_for_requirement(requirement),
            owner=requirement.owner,
            kind=requirement.kind,
            subject=requirement.subject,
            product=requirement.product,
            source=requirement.source,
            venue=requirement.venue,
            cadence=requirement.cadence,
            schema_version=requirement.schema_version or "1",
            quality_status="validated",
            reference_snapshot_id=snapshot_id,
            partitions=_event_date_partitions(events),
            lineage={
                "provider": "massive",
                "credential_id": requirement.parameters.get(
                    "credential_id", "massive-readonly"
                ),
                "provider_manifest": result,
                "acquisition_plan_hash": plan_hash,
                "point_in_time": {
                    "as_of": as_of,
                    "expiration_start": expiration_start,
                    "expiration_end": expiration_end,
                },
                "documentation": "https://massive.com/docs/rest/options/contracts",
            },
            quality_report=quality,
            coverage_start_time_unix_nanos=requirement.start_time_unix_nanos,
            coverage_end_time_unix_nanos=requirement.end_time_unix_nanos,
        )

    async def _execute_massive_dividends(
        self,
        requirement: DataRequirement,
        plan_hash: str,
        index: int,
    ) -> DatasetRef:
        ticker = requirement.parameters.get("ticker", requirement.subject).upper()
        start_date = _parameter(requirement, "start_date")
        end_date = _parameter(requirement, "end_date")
        staging = self.workspace.paths.child(
            "state",
            "data",
            "acquisitions",
            plan_hash,
            f"{index:04d}-cash-dividend.jsonl",
        )
        arguments = [
            "prepare-dividends",
            "--ticker",
            ticker,
            "--start-date",
            start_date,
            "--end-date",
            end_date,
            "--credential-id",
            requirement.parameters.get("credential_id", "massive-readonly"),
            "--file",
            str(staging),
        ]
        if endpoint := requirement.parameters.get("endpoint"):
            arguments.extend(("--endpoint", endpoint))
        result = await asyncio.to_thread(
            ReferenceCliApplication(self.workspace).run, arguments
        )
        events = tuple(
            json.loads(line)
            for line in staging.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        quality = _validate_cash_dividends(events, ticker)
        if int(result.get("record_count", -1)) != len(events):
            raise ValueError("Reference provider manifest and staged count differ")
        return self.catalog.publish_partitions(
            dataset_id=dataset_id_for_requirement(requirement),
            owner=requirement.owner,
            kind=requirement.kind,
            subject=requirement.subject,
            product=requirement.product,
            source=requirement.source,
            venue=requirement.venue,
            cadence=requirement.cadence,
            schema_version=requirement.schema_version or "1",
            quality_status="validated",
            reference_snapshot_id=(
                f"reference.cash-dividend/{ticker}/{start_date}/{end_date}"
            ),
            partitions=_event_date_partitions(events),
            lineage={
                "provider": "massive",
                "credential_id": requirement.parameters.get(
                    "credential_id", "massive-readonly"
                ),
                "provider_manifest": result,
                "acquisition_plan_hash": plan_hash,
                "documentation": "https://massive.com/docs/rest/stocks/corporate-actions/dividends",
            },
            quality_report=quality,
            coverage_start_time_unix_nanos=requirement.start_time_unix_nanos,
            coverage_end_time_unix_nanos=requirement.end_time_unix_nanos,
        )


def _parameter(requirement: DataRequirement, name: str) -> str:
    value = requirement.parameters.get(name, "").strip()
    if not value:
        raise ValueError(f"Massive acquisition requires parameter: {name}")
    return value


def _required_string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"data acquisition {key} must be a non-empty string")
    return result


def _optional_string(value: Mapping[str, Any], key: str) -> str | None:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, str) or not result:
        raise ValueError(f"data acquisition {key} must be a non-empty string")
    return result


def _required_int(value: Mapping[str, Any], key: str) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, int):
        raise ValueError(f"data acquisition {key} must be an integer")
    return result


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _event_date_partitions(
    events: tuple[Mapping[str, Any], ...],
) -> tuple[tuple[str, tuple[Mapping[str, Any], ...]], ...]:
    partitions: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        timestamp = event_time(event)
        if timestamp is None:
            key = "event-date=unknown"
        else:
            date = datetime.fromtimestamp(timestamp / 1_000_000_000, tz=UTC).date()
            key = f"event-date={date.isoformat()}"
        partitions.setdefault(key, []).append(event)
    if not partitions:
        return (("event-date=empty", ()),)
    return tuple((key, tuple(partitions[key])) for key in sorted(partitions))


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _validate_market_events(
    events: tuple[Mapping[str, Any], ...], requirement: DataRequirement
) -> dict[str, Any]:
    start = requirement.start_time_unix_nanos
    end = requirement.end_time_unix_nanos
    if start is None or end is None:
        raise ValueError("market quality validation requires a bounded interval")
    if not events:
        raise ValueError("market acquisition returned no facts for requested coverage")
    invalid = 0
    crossed = 0
    duplicates = 0
    seen: set[str] = set()
    first: int | None = None
    last: int | None = None
    for event in events:
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":"))
        duplicates += int(encoded in seen)
        seen.add(encoded)
        timestamp = event_time(event)
        if timestamp is None or not (start <= timestamp <= end):
            invalid += 1
        else:
            first = timestamp if first is None else min(first, timestamp)
            last = timestamp if last is None else max(last, timestamp)
        payload = event_payload(event)
        if requirement.kind == "quote":
            bid = _decimal(payload.get("bid_price"))
            ask = _decimal(payload.get("ask_price"))
            if bid is None and ask is None:
                invalid += 1
            if (bid is not None and bid < 0) or (ask is not None and ask < 0):
                invalid += 1
            if bid is not None and ask is not None and bid > ask:
                crossed += 1
        elif requirement.kind == "trade":
            price = _decimal(payload.get("price"))
            quantity = _decimal(payload.get("quantity"))
            if price is None or quantity is None or price <= 0 or quantity <= 0:
                invalid += 1
    if invalid or crossed:
        raise ValueError(
            f"market acquisition failed quality validation: invalid={invalid}, crossed={crossed}"
        )
    return {
        "event_count": len(events),
        "first_event_time_unix_nanos": first,
        "last_event_time_unix_nanos": last,
        "duplicate_events": duplicates,
        "invalid_events": invalid,
        "crossed_quotes": crossed,
    }


def _validate_reference_contracts(
    events: tuple[Mapping[str, Any], ...], requirement: DataRequirement
) -> dict[str, Any]:
    invalid = 0
    duplicates = 0
    seen_instruments: set[str] = set()
    seen_symbols: set[str] = set()
    expected_underlying = requirement.parameters.get(
        "underlying", requirement.subject
    ).upper()
    for event in events:
        if event.get("kind") != "option-contract":
            invalid += 1
        instrument_id = str(event.get("instrument_id") or "")
        external_symbol = str(
            event.get("external_symbol") or event.get("provider_symbol") or ""
        )
        duplicates += int(
            instrument_id in seen_instruments or external_symbol in seen_symbols
        )
        seen_instruments.add(instrument_id)
        seen_symbols.add(external_symbol)
        if not instrument_id or not external_symbol:
            invalid += 1
        if str(event.get("underlying") or "").upper() != expected_underlying:
            invalid += 1
        if not event.get("expiry_unix_nanos") or not event.get("strike"):
            invalid += 1
        if event.get("option_right") not in {"P", "C"}:
            invalid += 1
        try:
            if Decimal(str(event.get("contract_multiplier"))) <= 0:
                invalid += 1
        except Exception:
            invalid += 1
        timestamp = event_time(event)
        if timestamp is None:
            invalid += 1
    if invalid or duplicates:
        raise ValueError(
            "Reference acquisition failed quality validation: "
            f"invalid={invalid}, duplicates={duplicates}"
        )
    return {
        "event_count": len(events),
        "reference_match_rate": 1.0,
        "identity_time_completeness": 1.0,
        "invalid_contracts": invalid,
        "duplicate_contracts": duplicates,
        "point_in_time_as_of": requirement.parameters.get("as_of"),
    }


def _validate_cash_dividends(
    events: tuple[Mapping[str, Any], ...], ticker: str
) -> dict[str, Any]:
    invalid = 0
    duplicates = 0
    seen: set[str] = set()
    for event in events:
        dividend_id = str(event.get("dividend_id") or "")
        duplicates += int(dividend_id in seen)
        seen.add(dividend_id)
        if event.get("kind") != "cash-dividend" or not dividend_id:
            invalid += 1
        if str(event.get("ticker") or "").upper() != ticker:
            invalid += 1
        if event_time(event) is None or not event.get("ex_dividend_date"):
            invalid += 1
        amount = _decimal(
            event.get("split_adjusted_cash_amount") or event.get("cash_amount")
        )
        if amount is None or amount <= 0:
            invalid += 1
    if invalid or duplicates:
        raise ValueError(
            "Reference dividend acquisition failed quality validation: "
            f"invalid={invalid}, duplicates={duplicates}"
        )
    return {
        "event_count": len(events),
        "reference_match_rate": 1.0,
        "identity_time_completeness": 1.0,
        "invalid_dividends": invalid,
        "duplicate_dividends": duplicates,
    }
