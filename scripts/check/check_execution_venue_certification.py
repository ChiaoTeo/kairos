#!/usr/bin/env python3
"""Validate durable Execution venue transaction certifications and matrix marks."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[2]
MATRIX = Path("docs/integrations/execution-venue-certification.md")
RECORDS = Path("docs/integrations/execution-certifications")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
FORBIDDEN_KEY_PARTS = (
    "api_key",
    "api_secret",
    "access_token",
    "private_key",
    "passphrase",
    "credential_value",
)
REQUIRED_TRUE_EVIDENCE = (
    "submit_observed",
    "private_event_observed",
    "query_reconciled",
    "response_loss_or_disconnect_recovered",
    "restart_reconciled",
    "no_duplicate_order",
    "quantity_mapping_verified",
    "cleanup_complete",
)
TERMINAL_OUTCOMES = {"filled", "canceled", "rejected", "expired"}
FEE_MAPPING = {"verified", "not_applicable"}


def _text(value: object, name: str, failures: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        failures.append(f"{name} must be a non-empty string")
        return ""
    return value.strip()


def _timestamp(value: object, name: str, failures: list[str]) -> datetime | None:
    text = _text(value, name, failures)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        failures.append(f"{name} must be an RFC3339 timestamp")
        return None
    if parsed.tzinfo is None:
        failures.append(f"{name} must include a timezone")
        return None
    return parsed


def _matrix_rows(path: Path, failures: list[str]) -> dict[tuple[str, str], bool]:
    if not path.is_file():
        failures.append(f"missing certification matrix: {path}")
        return {}
    rows: dict[tuple[str, str], bool] = {}
    in_matrix = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "## Current matrix":
            in_matrix = True
            continue
        if in_matrix and line.startswith("## "):
            break
        if not in_matrix or not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 7 or cells[0] in {"Provider", "---"}:
            continue
        key = (cells[0], cells[1])
        if key in rows:
            failures.append(f"duplicate certification matrix row: {key[0]}/{key[1]}")
            continue
        deterministic, composition, recovery = cells[2:5]
        for label, value, mark in (
            ("deterministic", deterministic, "D"),
            ("composition", composition, "C"),
            ("recovery", recovery, "R"),
        ):
            if value not in {"", mark}:
                failures.append(
                    f"invalid {label} evidence mark for {key[0]}/{key[1]}: {value}"
                )
        transaction = cells[5]
        if transaction not in {"", "T", "not applicable"}:
            failures.append(
                f"invalid transaction certification mark for {key[0]}/{key[1]}: {transaction}"
            )
        if composition == "C" and deterministic != "D":
            failures.append(
                f"composition evidence for {key[0]}/{key[1]} requires deterministic evidence"
            )
        if recovery == "R" and (deterministic, composition) != ("D", "C"):
            failures.append(
                f"recovery evidence for {key[0]}/{key[1]} requires deterministic and composition evidence"
            )
        if transaction == "T" and (deterministic, composition, recovery) != (
            "D",
            "C",
            "R",
        ):
            failures.append(
                f"transaction certification for {key[0]}/{key[1]} requires D/C/R evidence"
            )
        rows[key] = transaction == "T"
    if not rows:
        failures.append("certification matrix has no provider rows")
    return rows


def _forbidden_keys(value: object, prefix: str = "") -> list[str]:
    failures: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            lowered = str(key).lower()
            if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                failures.append(f"forbidden secret-bearing key: {name}")
            failures.extend(_forbidden_keys(nested, name))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            failures.extend(_forbidden_keys(nested, f"{prefix}[{index}]"))
    return failures


def _validate_artifacts(
    root: Path,
    record_path: Path,
    value: object,
    failures: list[str],
) -> None:
    name = record_path.relative_to(root)
    if not isinstance(value, list) or not value:
        failures.append(
            f"{name}: artifacts must contain at least one redacted artifact"
        )
        return
    records_root = (root / RECORDS).resolve()
    for index, artifact in enumerate(value):
        prefix = f"{name}: artifacts[{index}]"
        if not isinstance(artifact, Mapping):
            failures.append(f"{prefix} must be a table")
            continue
        _text(artifact.get("kind"), f"{prefix}.kind", failures)
        relative = _text(artifact.get("path"), f"{prefix}.path", failures)
        digest = _text(artifact.get("sha256"), f"{prefix}.sha256", failures).lower()
        if digest and SHA256_RE.fullmatch(digest) is None:
            failures.append(
                f"{prefix}.sha256 must be 64 lowercase hexadecimal characters"
            )
        if not relative:
            continue
        artifact_path = (root / relative).resolve()
        if not artifact_path.is_relative_to(records_root):
            failures.append(f"{prefix}.path must stay under {RECORDS}")
            continue
        if not artifact_path.is_file():
            failures.append(f"{prefix}.path does not exist: {relative}")
            continue
        actual = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if digest and actual != digest:
            failures.append(f"{prefix}.sha256 does not match {relative}")


def _validate_record(
    root: Path,
    path: Path,
    matrix: Mapping[tuple[str, str], bool],
) -> tuple[tuple[str, str] | None, list[str]]:
    failures: list[str] = []
    name = path.relative_to(root)
    try:
        record = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        return None, [f"{name}: invalid TOML: {error}"]
    failures.extend(f"{name}: {failure}" for failure in _forbidden_keys(record))
    if record.get("version") != 1:
        failures.append(f"{name}: version must be 1")
    certification_id = _text(
        record.get("certification_id"), f"{name}: certification_id", failures
    )
    if certification_id and certification_id != path.stem:
        failures.append(f"{name}: certification_id must equal the file stem")
    provider = _text(record.get("provider"), f"{name}: provider", failures)
    channel = _text(
        record.get("execution_channel"), f"{name}: execution_channel", failures
    )
    key = (provider, channel) if provider and channel else None
    if key is not None and key not in matrix:
        failures.append(f"{name}: no matrix row exists for {provider}/{channel}")
    environment = _text(
        record.get("environment"), f"{name}: environment", failures
    ).lower()
    if environment not in {"live", "testnet", "demo", "paper"}:
        failures.append(f"{name}: environment must be live, testnet, demo, or paper")
    if environment == "live":
        _text(
            record.get("authorization_reference"),
            f"{name}: authorization_reference",
            failures,
        )
    for field in (
        "product",
        "account_mode",
        "credential_permission_class",
        "client_order_id",
        "remote_order_id",
        "cleanup_result",
        "residual_impact",
    ):
        _text(record.get(field), f"{name}: {field}", failures)
    started = _timestamp(record.get("started_at"), f"{name}: started_at", failures)
    completed = _timestamp(
        record.get("completed_at"), f"{name}: completed_at", failures
    )
    if started is not None and completed is not None and completed < started:
        failures.append(f"{name}: completed_at must not precede started_at")
    outcome = _text(
        record.get("terminal_outcome"), f"{name}: terminal_outcome", failures
    ).lower()
    if outcome not in TERMINAL_OUTCOMES:
        failures.append(
            f"{name}: terminal_outcome must be one of {sorted(TERMINAL_OUTCOMES)}"
        )
    lifecycle = record.get("lifecycle_observations")
    if (
        not isinstance(lifecycle, list)
        or not lifecycle
        or any(not isinstance(item, str) or not item.strip() for item in lifecycle)
    ):
        failures.append(
            f"{name}: lifecycle_observations must be a non-empty string array"
        )
    else:
        normalized = {item.strip().lower() for item in lifecycle}
        if "accepted" not in normalized:
            failures.append(f"{name}: lifecycle_observations must include accepted")
        if outcome and outcome not in normalized:
            failures.append(f"{name}: lifecycle_observations must include {outcome}")
    fee_mapping = _text(
        record.get("fee_mapping"), f"{name}: fee_mapping", failures
    ).lower()
    if fee_mapping not in FEE_MAPPING:
        failures.append(f"{name}: fee_mapping must be verified or not_applicable")
    evidence = record.get("evidence")
    if not isinstance(evidence, Mapping):
        failures.append(f"{name}: evidence must be a table")
    else:
        for field in REQUIRED_TRUE_EVIDENCE:
            if evidence.get(field) is not True:
                failures.append(f"{name}: evidence.{field} must be true")
    _validate_artifacts(root, path, record.get("artifacts"), failures)
    return key, failures


def validate(root: Path = ROOT) -> list[str]:
    failures: list[str] = []
    matrix = _matrix_rows(root / MATRIX, failures)
    records_root = root / RECORDS
    if not records_root.is_dir():
        failures.append(f"missing certification records directory: {records_root}")
        return failures
    certified_rows: set[tuple[str, str]] = set()
    identifiers: set[str] = set()
    for path in sorted(records_root.glob("*.toml")):
        if path.stem in identifiers:
            failures.append(f"duplicate certification_id/file stem: {path.stem}")
        identifiers.add(path.stem)
        key, record_failures = _validate_record(root, path, matrix)
        failures.extend(record_failures)
        if key is not None and not record_failures:
            certified_rows.add(key)
    for key, marked in matrix.items():
        if marked and key not in certified_rows:
            failures.append(
                f"matrix marks {key[0]}/{key[1]} as T without a valid record"
            )
        if not marked and key in certified_rows:
            failures.append(
                f"valid record exists for {key[0]}/{key[1]} but matrix is not T"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="validate Execution venue transaction certification records"
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    failures = validate(root)
    if failures:
        print("Execution venue certification checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    records = len(list((root / RECORDS).glob("*.toml")))
    print(
        f"Execution venue certification checks passed ({records} transaction records)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
