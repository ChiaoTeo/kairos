from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IntentAdmissionEvidence:
    """Execution-owned audit input for one governed Intent admission."""

    decision_id: str
    request_id: str
    intent_id: str
    source: str
    outcome: str
    original_intent: object
    effective_intent: object

    def __post_init__(self) -> None:
        if self.source != "decision_agent":
            raise ValueError("Unsupported Intent admission evidence source")
        if self.outcome not in {"approved", "revised"}:
            raise ValueError("Unsupported Intent admission evidence outcome")
        for name in ("decision_id", "request_id", "intent_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"Intent admission evidence {name} is required")


__all__ = ["IntentAdmissionEvidence"]
