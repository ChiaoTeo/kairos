"""Scenario 8: governed skew factor -> bound Bull Put Strategy -> executable backtest/replay."""

from __future__ import annotations

from datetime import datetime,timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from trading.backtest.engine import BacktestEngine
from trading.backtest.mock import make_mock_dataset
from trading.backtest.repository import BacktestRepository
from trading.backtest.result import BacktestConfig
from trading.features import FactorRegistry,OptionSkewFactorConfig,OptionSkewFactorRuntime,snapshots_hash
from trading.pricing import ValuationService
from trading.research import StudyWorkspace,StudyWorkspaceRepository
from trading.risk.limits import RiskLimits
from trading.storage.codec import to_primitive
from trading.strategies import StrategyImplementation,StrategyRegistry
from trading.strategies.bull_put_spread import BullPutSpreadConfig,BullPutSpreadStrategy
from trading.strategies.specs import bull_put_strategy_spec


def _hash(value)->str:return sha256(json.dumps(to_primitive(value),sort_keys=True,separators=(",",":")).encode()).hexdigest()


def run(root:Path)->dict[str,object]:
    dataset=make_mock_dataset();catalog=dataset.reference_catalog();identity=dataset.manifest.dataset_id
    studies=StudyWorkspaceRepository(root);studies.create(StudyWorkspace("spxw-skew-bull-put","1.0.0",
        "High point-in-time 25-delta put/ATM skew may support a bull put spread trade proxy",identity,
        dataset.manifest.content_hash,"timestamp",dataset.manifest.start.isoformat(),dataset.manifest.end.isoformat(),
        created_at=datetime(2026,7,17,tzinfo=timezone.utc).isoformat()))
    candidate=studies.freeze("spxw-skew-bull-put","1.0.0")
    factor_config=OptionSkewFactorConfig(minimum_rank_history=0)
    research_factor=OptionSkewFactorRuntime(catalog,factor_config,input_identity=identity)
    valuation=ValuationService(catalog,max_quote_age_seconds=Decimal("120"));factor_snapshots=[]
    for market in dataset.slices:
        valued,snapshot=valuation.value(market)
        if value:=research_factor.update_market(valued,snapshot):factor_snapshots.append(value)
    factor_dir=FactorRegistry(root/"factors").register(research_factor.spec)
    strategy_config=BullPutSpreadConfig(signal_factor_id=research_factor.spec.factor_id,minimum_skew_rank=Decimal("0.5"))
    spec,policy=bull_put_strategy_spec(strategy_config)
    implementation=StrategyImplementation("trading.strategies.bull_put_spread:BullPutSpreadStrategy",
        sha256((ROOT/"trading/strategies/bull_put_spread.py").read_bytes()).hexdigest())
    strategy_dir=StrategyRegistry(root/"strategies").register(spec,policy,implementation=implementation,
        factor_specs=(research_factor.spec,))
    config=BacktestConfig(dataset.manifest.start,dataset.manifest.end)
    def execute(model="conservative"):
        from dataclasses import replace
        factor=OptionSkewFactorRuntime(catalog,factor_config,input_identity=identity)
        result=BacktestEngine(dataset,replace(config,fill_model=model),BullPutSpreadStrategy(strategy_config),
            RiskLimits(),factor_runtimes=(factor,)).run()
        result.metrics["strategy_spec_hash"]=spec.spec_hash;result.metrics["execution_policy_id"]=policy.policy_id
        result.metrics["factor_spec_hash"]=factor.spec.spec_hash;return result
    conservative=execute();stress=execute("stress");repository=BacktestRepository(root/"backtests")
    conservative_dir=repository.save(conservative,strategy_config=strategy_config,risk_limits=RiskLimits())
    stress_dir=repository.save(stress,strategy_config=strategy_config,risk_limits=RiskLimits())
    replay=execute();replay_equal=_hash(conservative)==_hash(replay)
    return {"study_candidate":(candidate/"manifest.json").exists(),"research_evidence":"TRADE_PROXY_ONLY",
        "synthetic_mechanics_only":dataset.manifest.synthetic,"factor_release":str(factor_dir),
        "factor_hash":snapshots_hash(tuple(factor_snapshots)),"strategy_release":str(strategy_dir),
        "strategy_version":spec.version,"strategy_spec_hash":spec.spec_hash,"factor_spec_hash":research_factor.spec.spec_hash,
        "conservative_run":str(conservative_dir),"stress_run":str(stress_dir),"conservative_fills":len(conservative.fills),
        "stress_fills":len(stress.fills),"formal_strategy_consumed_factor":any("skew_rank=" in d.reason or d.action=="open" for d in conservative.strategy_decisions),
        "replay_equal":replay_equal,"replay_hash":_hash(replay)}


def main():
    with tempfile.TemporaryDirectory() as directory:print(json.dumps(run(Path(directory)),indent=2,sort_keys=True))


if __name__=="__main__":main()
