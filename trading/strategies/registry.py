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
from trading.features.runtime import FactorSpec


@dataclass(frozen=True, slots=True)
class StrategyImplementation:
    import_path: str
    code_hash: str

    def __post_init__(self) -> None:
        if not self.import_path.strip() or len(self.code_hash) != 64:
            raise ValueError("strategy implementation import path and SHA-256 hash are required")


@dataclass(frozen=True, slots=True)
class StrategyRelease:
    directory: Path
    strategy_spec: dict[str, object]
    execution_policy: dict[str, object]
    implementation: StrategyImplementation
    factor_bindings: tuple[dict[str, object], ...]

    @property
    def strategy_id(self) -> str:
        return str(self.strategy_spec["strategy_id"])

    @property
    def version(self) -> str:
        return str(self.strategy_spec["version"])


@dataclass(frozen=True,slots=True)
class StrategyReleaseStatus:
    strategy_id:str
    version:str
    lifecycle:str|None
    complete:bool
    missing_files:tuple[str,...]
    active:bool
    next_promotion:str|None
    latest_promotion_bundle:str|None=None


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

    def register(self, spec: StrategySpec, policy: ExecutionPolicy, *,
                 implementation: StrategyImplementation | None = None,
                 factor_specs: tuple[FactorSpec, ...] = ()) -> Path:
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
        if implementation is not None:
            _write_immutable(directory/"implementation.json", _jsonable(asdict(implementation)))
        if implementation is not None:
            bindings = [{"factor_id": item.factor_id, "version": item.version,
                         "factor_spec_hash": item.spec_hash} for item in factor_specs]
            _write_immutable(directory/"factor_bindings.json", bindings)
        self._refresh_manifest(directory)
        return directory

    def load(self, strategy_id: str, version: str) -> StrategyRelease:
        directory = self.root / strategy_id / version
        required = ("strategy_spec.json", "execution_policy.json", "implementation.json", "factor_bindings.json")
        missing = [name for name in required if not (directory/name).exists()]
        if missing:
            raise FileNotFoundError(f"strategy release is incomplete: {', '.join(missing)}")
        implementation = StrategyImplementation(**json.loads((directory/"implementation.json").read_text(encoding="utf-8")))
        bindings = json.loads((directory/"factor_bindings.json").read_text(encoding="utf-8"))
        return StrategyRelease(
            directory,
            json.loads((directory/"strategy_spec.json").read_text(encoding="utf-8")),
            json.loads((directory/"execution_policy.json").read_text(encoding="utf-8")),
            implementation,
            tuple(bindings),
        )

    def status(self,strategy_id:str,version:str)->StrategyReleaseStatus:
        directory=self.root/strategy_id/version
        required=("strategy_spec.json","execution_policy.json","implementation.json","factor_bindings.json","manifest.json")
        missing=tuple(name for name in required if not (directory/name).exists())
        payload=json.loads((directory/"strategy_spec.json").read_text(encoding="utf-8")) if (directory/"strategy_spec.json").exists() else {}
        lifecycle=payload.get("lifecycle");active=self.active_version(strategy_id)==version
        latest_bundle = self._latest_promotion_bundle(directory)
        next_stage={
            "DRAFT":"RESEARCH_VALIDATED","RESEARCH_VALIDATED":"TRADE_PROXY_VALIDATED",
            "TRADE_PROXY_VALIDATED":"EXECUTABLE_BACKTEST_VALIDATED","EXECUTABLE_BACKTEST_VALIDATED":"ROBUSTNESS_VALIDATED",
            "ROBUSTNESS_VALIDATED":"PAPER_APPROVED","PAPER_APPROVED":"LIVE_LIMITED","LIVE_LIMITED":"LIVE_APPROVED",
        }.get(lifecycle)
        return StrategyReleaseStatus(strategy_id,version,lifecycle,not missing,missing,active,next_stage,latest_bundle)

    def active_version(self,strategy_id:str)->str|None:
        path=self.root/strategy_id/"active.json"
        return str(json.loads(path.read_text(encoding="utf-8"))["version"]) if path.exists() else None

    def activate(self,strategy_id:str,version:str,*,actor:str,reason:str)->Path:
        if not actor.strip() or not reason.strip():raise ValueError("strategy activation requires actor and reason")
        self.load(strategy_id,version)
        directory=self.root/strategy_id;previous=self.active_version(strategy_id)
        record={"strategy_id":strategy_id,"version":version,"previous_version":previous,"actor":actor,"reason":reason,
            "activated_at":datetime.now(timezone.utc).isoformat()}
        history=directory/"activations.jsonl"
        with history.open("a",encoding="utf-8") as handle:handle.write(json.dumps(record,sort_keys=True)+"\n")
        target=directory/"active.json";temporary=target.with_suffix(".json.tmp");_write(temporary,record);temporary.replace(target)
        return target

    def rollback(self,strategy_id:str,*,actor:str,reason:str)->Path:
        path=self.root/strategy_id/"activations.jsonl"
        if not path.exists():raise RuntimeError("strategy has no activation history")
        records=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not records or not records[-1].get("previous_version"):raise RuntimeError("strategy has no previous active version")
        return self.activate(strategy_id,records[-1]["previous_version"],actor=actor,reason=f"rollback: {reason}")

    def promote(self, spec: StrategySpec, target: StrategyLifecycle, evidence: PromotionEvidence) -> StrategySpec:
        if evidence.target is not target: raise ValueError("promotion evidence target mismatch")
        directory=self._directory(spec)
        if not (directory/"strategy_spec.json").exists(): raise FileNotFoundError("strategy must be registered before promotion")
        for path,expected in zip(evidence.research_result_paths,evidence.evidence_hashes):
            actual=hashlib.sha256(Path(path).read_bytes()).hexdigest()
            if actual!=expected: raise ValueError(f"promotion evidence hash mismatch: {path}")
        promoted=spec.promote(target)
        records=directory/"promotions.jsonl";records.parent.mkdir(parents=True,exist_ok=True)
        bundle=self._write_promotion_bundle(directory,spec,target,evidence)
        with records.open("a",encoding="utf-8") as handle:
            handle.write(json.dumps({"from":spec.lifecycle.value,"to":target.value,
                "strategy_spec_hash":spec.spec_hash,"evidence":_jsonable(asdict(evidence)),
                "evidence_bundle":str(bundle.relative_to(directory))},sort_keys=True)+"\n")
        _write(directory/"strategy_spec.json",{**_jsonable(asdict(promoted)),"strategy_spec_hash":promoted.spec_hash})
        self._refresh_manifest(directory)
        return promoted

    def _directory(self,spec):return self.root/spec.strategy_id/spec.version

    @staticmethod
    def _latest_promotion_bundle(directory: Path) -> str | None:
        path=directory/"promotions.jsonl"
        if not path.exists():return None
        records=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not records:return None
        bundle=records[-1].get("evidence_bundle")
        return str(directory/bundle) if bundle else None

    @staticmethod
    def _write_promotion_bundle(directory: Path, spec: StrategySpec, target: StrategyLifecycle,
                                evidence: PromotionEvidence) -> Path:
        payload={
            "schema_version":1,
            "kind":"strategy_promotion_evidence_bundle",
            "strategy_id":spec.strategy_id,
            "version":spec.version,
            "from":spec.lifecycle.value,
            "to":target.value,
            "strategy_spec_hash":spec.spec_hash,
            "evidence":_jsonable(asdict(evidence)),
            "created_at":datetime.now(timezone.utc).isoformat(),
        }
        material=json.dumps(payload,ensure_ascii=True,sort_keys=True,separators=(",",":")).encode()
        bundle_hash=hashlib.sha256(material).hexdigest()
        payload["bundle_hash"]=bundle_hash
        target_dir=directory/"promotion-bundles"/f"{target.value.lower()}-{bundle_hash[:16]}"
        target_dir.mkdir(parents=True,exist_ok=True)
        _write(target_dir/"manifest.json",payload)
        return target_dir/"manifest.json"

    @staticmethod
    def _refresh_manifest(directory):
        files={}
        for path in sorted(directory.iterdir()):
            if path.name=="manifest.json" or not path.is_file():continue
            files[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        _write(directory/"manifest.json",{"schema_version":1,"files":files,
            "generated_at":datetime.now(timezone.utc).isoformat()})


def _write(path,payload):path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
def _write_immutable(path,payload):
    if path.exists() and json.loads(path.read_text(encoding="utf-8"))!=payload:
        raise ValueError(f"registered strategy release file has different semantics: {path.name}")
    if not path.exists():_write(path,payload)
def _jsonable(value):
    if isinstance(value,dict):return {key:_jsonable(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)):return [_jsonable(item) for item in value]
    if isinstance(value,Enum):return value.value
    if isinstance(value,Decimal):return str(value)
    return value
