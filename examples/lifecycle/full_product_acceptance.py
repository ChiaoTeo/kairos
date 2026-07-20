"""Run all eight documented product scenarios with deterministic local evidence."""

from __future__ import annotations

from argparse import Namespace
import asyncio
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from examples.backtest.governed_sma import run as run_backtest
from examples.operations.manual_order import run as run_manual
from examples.studies.sma_factor_lifecycle import run as run_study
from examples.runtime.sma_historical_simulation import run as run_simulation
from examples.runtime.sma_paper_session import run as run_paper
from examples.strategy.bull_put_spread_lifecycle import run as run_option
from examples.strategy.multi_asset_reference_lifecycle import run as run_multi_asset
from examples.strategy.multi_strategy_portfolio import run as run_multi_strategy


def run(root:Path)->dict[str,object]:
    study=run_study(root/"study")
    backtest=asyncio.run(run_backtest(Namespace(lake_root=str(root),dataset=None,start=None,end=None,fast=5,slow=15,fee_bps=Decimal("10"))))
    simulation=asyncio.run(run_simulation(root/"simulation"))
    paper=run_paper(root/"paper")
    manual=run_manual(root/"manual")
    option=run_option(root/"option")
    multi=run_multi_asset(root/"multi-asset")
    multi_strategy=run_multi_strategy()
    scenarios={
        "1_explore":study["sandbox_workspace"],
        "2_factor_release":study["factor_release"] and study["batch_replay_equal"],
        "3_strategy_backtest":backtest["economic_intents"]>0,
        "4_historical_simulation":simulation["restart_ready"] and simulation["fills"]>0,
        "5_live_paper":paper["restart_ready"] and paper["fills"]>0,
        "6_capture_replay":paper["capture_replay_passed"],
        "7_manual_order":manual["accepted"] and manual["actor_recorded"] and manual["reason_recorded"],
        "8_complex_option":option["formal_strategy_consumed_factor"] and option["replay_equal"],
    }
    parity=all(backtest[name]==simulation[name] for name in ("factor_hash","decision_hash","intent_hash"))
    releases=all(item["release_complete"] for item in multi["strategies"].values())
    portfolio=multi_strategy["virtual_ownership_preserved"] and multi_strategy["account_net_quantity"]=="75"
    return {"passed":all(scenarios.values()) and parity and releases and portfolio,"scenarios":scenarios,
        "sma_execution_boundary_parity":parity,
        "multi_asset_releases":releases,
        "multi_strategy_portfolio":portfolio,
        "evidence":{"study":study,"backtest":backtest,"simulation":simulation,"paper":paper,
            "manual":manual,"option":option,"multi_asset":multi,"multi_strategy":multi_strategy}}


def main():
    with tempfile.TemporaryDirectory() as directory:print(json.dumps(run(Path(directory)),indent=2,sort_keys=True))


if __name__=="__main__":main()
