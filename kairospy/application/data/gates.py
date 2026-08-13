"""Architecture Gate 1: deterministic Dataset trust evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .catalog import DatasetCatalogApplication, event_payload, normalize_kind
from .models import DatasetSetRef
from .readers import DatasetReaderApplication


@dataclass(frozen=True, slots=True)
class DataGateCheck:
    name: str
    status: str
    detail: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class DataTrustGateReport:
    status: str
    composition_hash: str
    checks: tuple[DataGateCheck, ...]

    @property
    def failed_checks(self) -> tuple[str, ...]:
        return tuple(check.name for check in self.checks if check.status == "failed")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "gate": "data-trust",
            "status": self.status,
            "composition_hash": self.composition_hash,
            "failed_checks": list(self.failed_checks),
            "checks": [asdict(check) for check in self.checks],
        }


@dataclass(frozen=True, slots=True)
class DataTrustGateApplication:
    catalog: DatasetCatalogApplication

    def evaluate(
        self,
        dataset_set: DatasetSetRef,
        *,
        required_kinds: tuple[str, ...] = (),
        require_point_in_time_policy: bool = True,
    ) -> DataTrustGateReport:
        checks: list[DataGateCheck] = []

        def record(name: str, passed: bool, **detail: Any) -> None:
            checks.append(
                DataGateCheck(
                    name=name,
                    status="passed" if passed else "failed",
                    detail=detail,
                )
            )

        available_kinds = {
            normalize_kind(member.kind) for member in dataset_set.members
        }
        required = {normalize_kind(kind) for kind in required_kinds}
        missing = sorted(required - available_kinds)
        record(
            "required_atomic_kinds",
            not missing,
            required=sorted(required),
            available=sorted(available_kinds),
            missing=missing,
        )

        policy = dict(dataset_set.composition_policy)
        expected_policy = {
            "time_alignment": "point-in-time",
            "conflict_policy": "reject",
        }
        policy_matches = all(
            policy.get(key) == value for key, value in expected_policy.items()
        )
        record(
            "point_in_time_composition_policy",
            not require_point_in_time_policy or policy_matches,
            required=require_point_in_time_policy,
            expected=expected_policy,
            actual=policy,
        )

        for member in dataset_set.members:
            prefix = member.identity
            try:
                description = self.catalog.describe(member)
            except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
                record(
                    f"manifest_integrity:{prefix}",
                    False,
                    error=f"{type(error).__name__}: {error}",
                )
                continue
            record(
                f"manifest_integrity:{prefix}",
                True,
                content_hash=member.content_hash,
                event_count=member.event_count,
                partition_count=len(description.partitions),
            )
            record(
                f"lineage:{prefix}",
                bool(description.lineage),
                lineage=description.lineage,
            )
            record(
                f"quality:{prefix}",
                member.quality_status in {"validated", "trusted"}
                and bool(description.quality_report),
                quality_status=member.quality_status,
                quality_report=description.quality_report,
            )
            if normalize_kind(member.kind) == "option-contract":
                record(
                    f"point_in_time_reference:{prefix}",
                    bool(member.reference_snapshot_id),
                    reference_snapshot_id=member.reference_snapshot_id,
                )

        has_option_reference = any(
            normalize_kind(member.kind) == "option-contract"
            for member in dataset_set.members
        )
        option_market_members = tuple(
            member
            for member in dataset_set.members
            if normalize_kind(member.kind) in {"quote", "trade", "option-greeks"}
            and (
                member.product == "options"
                or (has_option_reference and "option" in member.subject.lower())
            )
        )
        if option_market_members:
            try:
                snapshot = DatasetReaderApplication(self.catalog).snapshot(
                    DatasetReaderApplication(self.catalog).plan(dataset_set)
                )
                reference_availability: dict[str, list[int]] = {}
                for event in snapshot.scan("option-contract"):
                    payload = event_payload(event)
                    instrument_id = str(payload.get("instrument_id") or "")
                    available_at = payload.get("available_at_unix_nanos")
                    if instrument_id and available_at is not None:
                        reference_availability.setdefault(instrument_id, []).append(
                            int(available_at)
                        )

                total = 0
                matched = 0
                missing_identity_time = 0
                unmatched: set[str] = set()
                future_only: set[str] = set()
                option_market_kinds = {
                    normalize_kind(member.kind) for member in option_market_members
                }
                for kind in sorted(option_market_kinds):
                    for event in snapshot.scan(kind):
                        payload = event_payload(event)
                        total += 1
                        instrument_id = str(payload.get("instrument_id") or "")
                        observed_at = payload.get("observed_at_unix_nanos")
                        if not instrument_id or observed_at is None:
                            missing_identity_time += 1
                            continue
                        available = reference_availability.get(instrument_id, ())
                        if not available:
                            unmatched.add(instrument_id)
                            continue
                        if not any(value <= int(observed_at) for value in available):
                            future_only.add(instrument_id)
                            continue
                        matched += 1
                record(
                    "option_market_point_in_time_reference_match",
                    total > 0
                    and matched == total
                    and missing_identity_time == 0
                    and not unmatched
                    and not future_only,
                    option_market_event_count=total,
                    matched_event_count=matched,
                    reference_instrument_count=len(reference_availability),
                    identity_time_completeness=(
                        (total - missing_identity_time) / total if total else 0.0
                    ),
                    reference_match_rate=(matched / total if total else 0.0),
                    missing_identity_time=missing_identity_time,
                    unmatched_instruments=sorted(unmatched),
                    future_only_instruments=sorted(future_only),
                )
            except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
                record(
                    "option_market_point_in_time_reference_match",
                    False,
                    error=f"{type(error).__name__}: {error}",
                )

        failed = any(check.status == "failed" for check in checks)
        return DataTrustGateReport(
            status="failed" if failed else "passed",
            composition_hash=dataset_set.composition_hash,
            checks=tuple(checks),
        )

    def publish(
        self,
        dataset_set: DatasetSetRef,
        *,
        required_kinds: tuple[str, ...] = (),
        require_point_in_time_policy: bool = True,
    ) -> DataTrustGateReport:
        """Evaluate and atomically persist reviewable Gate 1 evidence."""

        report = self.evaluate(
            dataset_set,
            required_kinds=required_kinds,
            require_point_in_time_policy=require_point_in_time_policy,
        )
        path = self._report_path(dataset_set.composition_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
        return report

    def report(self, composition_hash: str) -> Mapping[str, Any]:
        """Read previously published Gate 1 evidence without re-evaluation."""

        path = self._report_path(composition_hash)
        if not path.is_file():
            raise FileNotFoundError(
                f"data trust report does not exist: {composition_hash}"
            )
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("data trust report must be an object")
        if value.get("schema_version") != 2:
            raise ValueError("data trust report schema is stale; publish Gate 1 again")
        return value

    def _report_path(self, composition_hash: str) -> Path:
        if len(composition_hash) != 64 or any(
            character not in "0123456789abcdef" for character in composition_hash
        ):
            raise ValueError("Dataset Set composition hash is invalid")
        return self.catalog.workspace.paths.child(
            "state", "data", "gates", f"{composition_hash}.json"
        )


__all__ = [
    "DataGateCheck",
    "DataTrustGateApplication",
    "DataTrustGateReport",
]
