"""Scenario 5-6: canonical capture -> durable Live Paper -> offline decision replay."""

from __future__ import annotations

from argparse import Namespace
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from kairospy.product_workflow import replay_sma_capture,run_sma_paper_workflow


def run(root:Path)->dict[str,object]:
    paper=run_sma_paper_workflow(Namespace(capture=None,fixture=True,run_root=root/"runtime",
        artifact_root=root/"artifacts",lake_root=root,fast=5,slow=15,initial_cash=Decimal("100000"),
        fee_bps=Decimal("10"),account_id="sma-paper-example",base_asset="BTC",quote_asset="USDT"))
    replay=replay_sma_capture(Namespace(artifact=Path(paper["artifact"]),capture=Path(paper["capture"])))
    return {**paper,"capture_replay_passed":replay["passed"],"comparisons":replay["comparisons"]}


def main()->None:
    with tempfile.TemporaryDirectory() as directory:print(json.dumps(run(Path(directory)),indent=2,sort_keys=True))


if __name__=="__main__":main()
