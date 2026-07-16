from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
from pathlib import Path

from trading.domain.strategy_contract import StrategyLifecycle, StrategySpec
from trading.execution.policy import ExecutionPolicy


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
    target: StrategyLifecycle
    research_result_paths: tuple[str, ...]
    evidence_hashes: tuple[str, ...]
    approved_by: str
    capital_limit: Decimal
    rollback_condition: str
    approved_at: str
    gate_passed: bool
    gate_reasons: tuple[str,...]=()

    def __post_init__(self) -> None:
        if not self.research_result_paths or len(self.research_result_paths)!=len(self.evidence_hashes):
            raise ValueError("promotion requires matching research paths and hashes")
        if not self.approved_by or self.capital_limit<=0 or not self.rollback_condition:
            raise ValueError("approver, capital limit, and rollback condition are required")
        if not self.gate_passed:raise ValueError("promotion evidence gate did not pass")


class StrategyRegistry:
    def __init__(self, root: str | Path = "data/strategies") -> None:
        self.root=Path(root)

    def register(self, spec: StrategySpec, policy: ExecutionPolicy) -> Path:
        if spec.lifecycle is not StrategyLifecycle.DRAFT:
            raise ValueError("new strategy versions must be registered as DRAFT and promoted with evidence")
        if policy.policy_id not in spec.required_execution_capabilities:
            raise ValueError("strategy spec must name its execution policy as a required capability")
        directory=self._directory(spec);directory.mkdir(parents=True,exist_ok=True)
        existing=directory/"strategy_spec.json"
        if existing.exists():
            current=json.loads(existing.read_text(encoding="utf-8"))
            if current.get("strategy_spec_hash")!=spec.spec_hash:
                raise ValueError("registered strategy version has different semantics")
            policy_path=directory/"execution_policy.json"
            if policy_path.exists() and json.loads(policy_path.read_text(encoding="utf-8"))!=_jsonable(asdict(policy)):
                raise ValueError("registered strategy version has a different execution policy")
            if current.get("lifecycle")!=StrategyLifecycle.DRAFT.value and spec.lifecycle is StrategyLifecycle.DRAFT:
                return directory
        _write(directory/"strategy_spec.json",{**_jsonable(asdict(spec)),"strategy_spec_hash":spec.spec_hash})
        _write(directory/"execution_policy.json",_jsonable(asdict(policy)))
        self._refresh_manifest(directory)
        return directory

    def promote(self, spec: StrategySpec, target: StrategyLifecycle, evidence: PromotionEvidence) -> StrategySpec:
        if evidence.target is not target: raise ValueError("promotion evidence target mismatch")
        directory=self._directory(spec)
        if not (directory/"strategy_spec.json").exists(): raise FileNotFoundError("strategy must be registered before promotion")
        for path,expected in zip(evidence.research_result_paths,evidence.evidence_hashes):
            actual=hashlib.sha256(Path(path).read_bytes()).hexdigest()
            if actual!=expected: raise ValueError(f"promotion evidence hash mismatch: {path}")
        promoted=spec.promote(target)
        records=directory/"promotions.jsonl";records.parent.mkdir(parents=True,exist_ok=True)
        with records.open("a",encoding="utf-8") as handle:
            handle.write(json.dumps({"from":spec.lifecycle.value,"to":target.value,
                "strategy_spec_hash":spec.spec_hash,"evidence":_jsonable(asdict(evidence))},sort_keys=True)+"\n")
        _write(directory/"strategy_spec.json",{**_jsonable(asdict(promoted)),"strategy_spec_hash":promoted.spec_hash})
        self._refresh_manifest(directory)
        return promoted

    def _directory(self,spec):return self.root/spec.strategy_id/spec.version

    @staticmethod
    def _refresh_manifest(directory):
        files={}
        for path in sorted(directory.iterdir()):
            if path.name=="manifest.json" or not path.is_file():continue
            files[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        _write(directory/"manifest.json",{"schema_version":1,"files":files,
            "generated_at":datetime.now(timezone.utc).isoformat()})


def _write(path,payload):path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
def _jsonable(value):
    if isinstance(value,dict):return {key:_jsonable(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)):return [_jsonable(item) for item in value]
    if isinstance(value,Enum):return value.value
    if isinstance(value,Decimal):return str(value)
    return value
