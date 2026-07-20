from __future__ import annotations

import argparse
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
from dataclasses import replace
from pathlib import Path

from kairospy.domain.capability import TimeInForce
from kairospy.domain.strategy_contract import StrategyLifecycle
from kairospy.execution.policy import ExecutionMode,ExecutionPolicy
from kairospy.strategies.btc_iron_condor import BtcIronCondorStrategy
from kairospy.strategies.registry import PromotionEvidence,StrategyRegistry
from kairospy.strategies.promotion import evaluate_promotion_artifacts


def register(root: str|Path="data"):
    root=Path(root);evidence=root/"studies"/"btc_deribit_iron_condor_trade_proxy_v1"/"1.0.0"/"results.json"
    if not evidence.exists():raise FileNotFoundError("run governed iron-condor study before strategy registration")
    study_spec_hash=__import__("json").loads((evidence.parent/"study_spec.json").read_text())["spec_hash"]
    strategy=BtcIronCondorStrategy(study_spec_hash=study_spec_hash).strategy_spec
    policy=ExecutionPolicy("taker-combo-v1","1.0.0",ExecutionMode.TAKER,TimeInForce.IOC,Decimal("15"),
        order_latency_ms=250,slippage_model="top_of_book",fee_schedule="deribit_study_v1")
    registry=StrategyRegistry(root/"strategies");directory=root/"strategies"/strategy.strategy_id/strategy.version
    promotions=directory/"promotions.jsonl"
    if promotions.exists():
        records=[__import__("json").loads(line) for line in promotions.read_text().splitlines() if line]
        if records and records[-1]["to"]==StrategyLifecycle.STUDY_VALIDATED.value and records[-1].get("evidence",{}).get("gate_passed") is True:
            return directory,replace(strategy,lifecycle=StrategyLifecycle.STUDY_VALIDATED)
    directory=registry.register(strategy,policy)
    supporting=(root/"studies"/"btc_skew_predictability_v1"/"1.0.0"/"results.json",
                root/"studies"/"btc_term_vrp_v1"/"1.0.0"/"results.json")
    payloads=tuple(__import__("json").loads(path.read_text()) for path in supporting);gate=evaluate_promotion_artifacts(StrategyLifecycle.STUDY_VALIDATED,payloads)
    promotion=PromotionEvidence(StrategyLifecycle.STUDY_VALIDATED,tuple(str(path) for path in supporting),
        tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in supporting),"governed-study-gate",
        Decimal("10000"),"signal evidence or data lineage invalidated",datetime.now(timezone.utc).isoformat(),gate.passed,gate.reasons)
    return directory,registry.promote(strategy,StrategyLifecycle.STUDY_VALIDATED,promotion)


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument("--data-root",type=Path,default=Path("data"));args=parser.parse_args(argv)
    directory,spec=register(args.data_root);print(f"{directory}: {spec.lifecycle.value} {spec.spec_hash}")


if __name__=="__main__":main()
