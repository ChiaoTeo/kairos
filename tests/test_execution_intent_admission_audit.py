from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import sqlite3

import pytest

from kairospy.application.execution import (
    IntentAdmissionEvidence,
    TargetPositionRequest,
)
from kairospy.application.execution.services.intent_admission_audit import (
    IntentAdmissionAudit,
)


def test_execution_admission_audit_persists_original_effective_and_hashes(
    tmp_path: Path,
) -> None:
    original = TargetPositionRequest(
        "BTCUSDT", Decimal("2"), account_id="main", intent_id="intent"
    )
    effective = replace(original, quantity=Decimal("1"))
    path = tmp_path / "admission.sqlite3"
    audit = IntentAdmissionAudit(path)
    evidence = IntentAdmissionEvidence(
        decision_id="decision",
        request_id="request",
        intent_id="intent",
        source="decision_agent",
        outcome="revised",
        original_intent=original,
        effective_intent=effective,
        submission_status="accepted",
    )

    audit.record(evidence)
    audit.record(replace(evidence, submission_status="duplicate"))
    audit.close()

    connection = sqlite3.connect(path)
    row = connection.execute(
        """
        SELECT original_intent_json, effective_intent_json,
               original_hash, effective_hash, submission_status
        FROM intent_admission_audit
        """
    ).fetchone()
    assert row is not None
    assert '"quantity":"2"' in row[0]
    assert '"quantity":"1"' in row[1]
    assert row[2] != row[3]
    assert row[4] == "duplicate"


def test_execution_admission_audit_rejects_changed_idempotent_evidence(
    tmp_path: Path,
) -> None:
    request = TargetPositionRequest("BTCUSDT", Decimal("1"), intent_id="intent")
    audit = IntentAdmissionAudit(tmp_path / "admission.sqlite3")
    evidence = IntentAdmissionEvidence(
        "decision",
        "request",
        "intent",
        "decision_agent",
        "approved",
        request,
        request,
        "accepted",
    )
    audit.record(evidence)

    with pytest.raises(ValueError, match="reused"):
        audit.record(
            replace(evidence, effective_intent=replace(request, quantity=Decimal("2")))
        )

    audit.close()
