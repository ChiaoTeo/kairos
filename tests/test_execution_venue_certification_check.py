from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path


CHECKER = (
    Path(__file__).parents[1] / "scripts/check/check_execution_venue_certification.py"
)


def _check(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(root)],
        text=True,
        capture_output=True,
        check=False,
    )


def _workspace(tmp_path: Path, *, transaction_mark: str) -> Path:
    root = tmp_path / "repository"
    records = root / "docs/integrations/execution-certifications"
    records.mkdir(parents=True)
    matrix = root / "docs/integrations/execution-venue-certification.md"
    matrix.write_text(
        """# Execution venue certification

## Current matrix

| Provider | Execution channel | Submit/cancel outcome | Composition | Recovery | Transaction certification | Current conclusion |
| --- | --- | --- | --- | --- | --- | --- |
| Binance | Spot | D | C | R | """
        + transaction_mark
        + """ | Test row. |

## Transaction certification record
""",
        encoding="utf-8",
    )
    return root


def _write_valid_record(root: Path, *, environment: str = "testnet") -> Path:
    artifact = (
        root
        / "docs/integrations/execution-certifications/evidence/binance-spot-testnet.json"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"redacted":true}\n', encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    record = (
        root
        / "docs/integrations/execution-certifications/binance-spot-testnet-20260826.toml"
    )
    authorization = (
        'authorization_reference = "approval:change-42"\n'
        if environment == "live"
        else ""
    )
    record.write_text(
        f"""version = 1
certification_id = "binance-spot-testnet-20260826"
provider = "Binance"
execution_channel = "Spot"
environment = "{environment}"
product = "BTCUSDT spot"
account_mode = "spot"
credential_permission_class = "trade enabled; withdrawal disabled"
started_at = "2026-08-26T10:00:00Z"
completed_at = "2026-08-26T10:10:00Z"
client_order_id = "cert-redacted-1"
remote_order_id = "9876"
terminal_outcome = "filled"
lifecycle_observations = ["accepted", "partially_filled", "filled"]
fee_mapping = "verified"
cleanup_result = "no open order; test position closed"
residual_impact = "testnet fee only"
{authorization}
[evidence]
submit_observed = true
private_event_observed = true
query_reconciled = true
response_loss_or_disconnect_recovered = true
restart_reconciled = true
no_duplicate_order = true
quantity_mapping_verified = true
cleanup_complete = true

[[artifacts]]
kind = "redacted_event_trace"
path = "docs/integrations/execution-certifications/evidence/binance-spot-testnet.json"
sha256 = "{digest}"
""",
        encoding="utf-8",
    )
    return record


def test_empty_matrix_and_record_set_is_valid(tmp_path: Path) -> None:
    root = _workspace(tmp_path, transaction_mark="")

    result = _check(root)

    assert result.returncode == 0, result.stderr


def test_matrix_rejects_invalid_local_evidence_mark(tmp_path: Path) -> None:
    root = _workspace(tmp_path, transaction_mark="")
    matrix = root / "docs/integrations/execution-venue-certification.md"
    matrix.write_text(
        matrix.read_text(encoding="utf-8").replace(
            "| Binance | Spot | D | C | R |",
            "| Binance | Spot | fixture | C | R |",
        ),
        encoding="utf-8",
    )

    result = _check(root)

    assert result.returncode == 1
    assert "invalid deterministic evidence mark for Binance/Spot: fixture" in result.stderr


def test_matrix_evidence_levels_are_cumulative(tmp_path: Path) -> None:
    root = _workspace(tmp_path, transaction_mark="")
    matrix = root / "docs/integrations/execution-venue-certification.md"
    matrix.write_text(
        matrix.read_text(encoding="utf-8").replace(
            "| Binance | Spot | D | C | R |", "| Binance | Spot | D |  | R |"
        ),
        encoding="utf-8",
    )

    result = _check(root)

    assert result.returncode == 1
    assert (
        "recovery evidence for Binance/Spot requires deterministic and composition evidence"
        in result.stderr
    )


def test_transaction_certification_requires_all_local_levels(tmp_path: Path) -> None:
    root = _workspace(tmp_path, transaction_mark="T")
    matrix = root / "docs/integrations/execution-venue-certification.md"
    matrix.write_text(
        matrix.read_text(encoding="utf-8").replace(
            "| Binance | Spot | D | C | R | T |",
            "| Binance | Spot | D | C |  | T |",
        ),
        encoding="utf-8",
    )
    _write_valid_record(root)

    result = _check(root)

    assert result.returncode == 1
    assert "transaction certification for Binance/Spot requires D/C/R evidence" in result.stderr


def test_matrix_cannot_claim_transaction_without_record(tmp_path: Path) -> None:
    root = _workspace(tmp_path, transaction_mark="T")

    result = _check(root)

    assert result.returncode == 1
    assert "matrix marks Binance/Spot as T without a valid record" in result.stderr


def test_complete_content_addressed_record_certifies_matrix_row(tmp_path: Path) -> None:
    root = _workspace(tmp_path, transaction_mark="T")
    _write_valid_record(root)

    result = _check(root)

    assert result.returncode == 0, result.stderr


def test_incomplete_recovery_evidence_cannot_certify(tmp_path: Path) -> None:
    root = _workspace(tmp_path, transaction_mark="T")
    record = _write_valid_record(root)
    record.write_text(
        record.read_text(encoding="utf-8").replace(
            "restart_reconciled = true", "restart_reconciled = false"
        ),
        encoding="utf-8",
    )

    result = _check(root)

    assert result.returncode == 1
    assert "evidence.restart_reconciled must be true" in result.stderr
    assert "matrix marks Binance/Spot as T without a valid record" in result.stderr


def test_valid_record_and_matrix_mark_are_bidirectional(tmp_path: Path) -> None:
    root = _workspace(tmp_path, transaction_mark="")
    _write_valid_record(root)

    result = _check(root)

    assert result.returncode == 1
    assert "valid record exists for Binance/Spot but matrix is not T" in result.stderr


def test_live_record_requires_explicit_authorization_reference(tmp_path: Path) -> None:
    root = _workspace(tmp_path, transaction_mark="T")
    record = _write_valid_record(root, environment="live")
    record.write_text(
        record.read_text(encoding="utf-8").replace(
            'authorization_reference = "approval:change-42"\n', ""
        ),
        encoding="utf-8",
    )

    result = _check(root)

    assert result.returncode == 1
    assert "authorization_reference must be a non-empty string" in result.stderr


def test_record_rejects_secret_bearing_keys(tmp_path: Path) -> None:
    root = _workspace(tmp_path, transaction_mark="T")
    record = _write_valid_record(root)
    record.write_text(
        record.read_text(encoding="utf-8") + 'api_secret = "must-not-be-recorded"\n',
        encoding="utf-8",
    )

    result = _check(root)

    assert result.returncode == 1
    assert "forbidden secret-bearing key: artifacts[0].api_secret" in result.stderr


def test_record_rejects_artifact_digest_mismatch(tmp_path: Path) -> None:
    root = _workspace(tmp_path, transaction_mark="T")
    record = _write_valid_record(root)
    text = record.read_text(encoding="utf-8")
    digest_start = text.index('sha256 = "') + len('sha256 = "')
    record.write_text(
        text[:digest_start] + ("0" * 64) + text[digest_start + 64 :],
        encoding="utf-8",
    )

    result = _check(root)

    assert result.returncode == 1
    assert "sha256 does not match" in result.stderr
