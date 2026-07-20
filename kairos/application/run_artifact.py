from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

from kairos.storage.codec import to_primitive

from .strategy_run_loop import StrategyRunResult


RUN_ARTIFACT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class RunArtifact:
    path: Path
    payload: dict[str, object]

    @property
    def artifact_hash(self) -> str:
        return str(self.payload["artifact_hash"])


class RunArtifactRepository:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def write(self, *, mode: str, input_identity: str, strategy_id: str, strategy_version: str,
              config: dict[str, object], result: StrategyRunResult,
              execution: dict[str, object] | None = None, attribution:object|None=None) -> RunArtifact:
        payload = {
            "schema_version": RUN_ARTIFACT_SCHEMA_VERSION,
            "mode": mode, "input_identity": input_identity,
            "strategy_id": strategy_id, "strategy_version": strategy_version,
            "config": to_primitive(config),
            "event_message_ids": list(result.event_message_ids),
            "factor_snapshots": to_primitive(result.factor_snapshots),
            "decisions": to_primitive(result.decisions),
            "economic_intents": to_primitive(result.economic_intents),
            "factor_hash": result.factor_hash, "decision_hash": result.decision_hash,
            "intent_hash": result.intent_hash, "strategy_run_audit_hash": result.audit_hash,
            "execution": to_primitive(execution or {}),
            "attribution":to_primitive(attribution) if attribution is not None else None,
        }
        artifact_hash = _hash(payload); payload["artifact_hash"] = artifact_hash
        path = self.root/mode/artifact_hash/"manifest.json"; path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError("run artifact hash refers to conflicting content")
        if not path.exists():
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
            temporary.replace(path)
        return RunArtifact(path, payload)

    def load(self, path: str | Path) -> RunArtifact:
        target = Path(path); payload = json.loads(target.read_text(encoding="utf-8"))
        if payload.get("schema_version") != RUN_ARTIFACT_SCHEMA_VERSION:
            raise ValueError("unsupported run artifact schema")
        expected = payload.pop("artifact_hash", None); actual = _hash(payload); payload["artifact_hash"] = expected
        if expected != actual: raise ValueError("run artifact content hash mismatch")
        self._verify_component_hashes(payload)
        return RunArtifact(target, payload)

    @staticmethod
    def explain(artifact: RunArtifact, *, at: str | None = None) -> dict[str, object]:
        payload = artifact.payload; factors = list(payload["factor_snapshots"]); decisions = list(payload["decisions"])
        intents = list(payload["economic_intents"])
        if at is not None:
            factors = [item for item in factors if _at_or_before(item["as_of"],at)]
            decisions = [item for item in decisions if _at_or_before(item["timestamp"],at)]
            intents = [item for item in intents if _at_or_before(item["decision_time"],at)]
        return {
            "artifact": str(artifact.path), "artifact_hash": artifact.artifact_hash,
            "mode": payload["mode"], "strategy_id": payload["strategy_id"],
            "input_identity": payload["input_identity"], "at": at,
            "factor": factors[-1] if factors else None,
            "decision": decisions[-1] if decisions else None,
            "economic_intent": intents[-1] if intents else None,
            "execution": payload["execution"],
            "attribution":payload.get("attribution"),
            "hashes": {name: payload[name] for name in (
                "factor_hash", "decision_hash", "intent_hash", "strategy_run_audit_hash",
            )},
        }

    @staticmethod
    def _verify_component_hashes(payload: dict[str, object]) -> None:
        expected = {
            "factor_hash": _hash(payload["factor_snapshots"]),
            "decision_hash": _hash(payload["decisions"]),
            "intent_hash": _hash(payload["economic_intents"]),
        }
        for name, actual in expected.items():
            if payload.get(name) != actual: raise ValueError(f"run artifact {name} mismatch")
        audit = _hash({"events": payload["event_message_ids"], **expected})
        if payload.get("strategy_run_audit_hash") != audit:
            raise ValueError("run artifact strategy audit hash mismatch")


def _hash(value: object) -> str:
    return sha256(json.dumps(
        to_primitive(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()


def _at_or_before(value, at)->bool:
    def parse(item):
        if isinstance(item,dict) and "$datetime" in item:item=item["$datetime"]
        return datetime.fromisoformat(str(item).replace("Z","+00:00"))
    return parse(value)<=parse(at)
