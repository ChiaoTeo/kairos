"""Register and run formal Covered Call and Spot/Perpetual Carry reference strategies."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from kairospy.backtest.reference_scenarios import run_reference_scenario
from kairospy.strategies import StrategyRegistry
from kairospy.strategies.specs import register_builtin_strategies


def run(root:Path)->dict[str,object]:
    strategy_root=root/"strategies";register_builtin_strategies(strategy_root);registry=StrategyRegistry(strategy_root)
    values={}
    for short,strategy_id in (("covered-call","covered-call-v1"),("spot-perp-carry","spot-perpetual-carry-v1")):
        release=registry.load(strategy_id,"1.1.0");conservative=run_reference_scenario(short,"conservative")
        replay=run_reference_scenario(short,"conservative");stress=run_reference_scenario(short,"stress")
        values[short]={"release_complete":release.strategy_id==strategy_id,"implementation":release.implementation.import_path,
            "economic_replay_equal":conservative==replay,"stress_is_worse":stress.final_cash<conservative.final_cash,
            "ledger_transactions":conservative.ledger_transactions,"audit_hash":conservative.audit_hash,
            "strategy_spec_hash":conservative.strategy_spec_hash,"execution_policy_id":conservative.execution_policy_id}
    protective=registry.load("protective-put-v1","1.1.0")
    return {"strategies":values,"protective_put_release_complete":protective.strategy_id=="protective-put-v1"}


def main():
    with tempfile.TemporaryDirectory() as directory:print(json.dumps(run(Path(directory)),indent=2,sort_keys=True))


if __name__=="__main__":main()
