from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from kairospy.application import RunArtifactRepository
from kairospy.product_workflow import _governed_run, fixture_sma_bars


class RunArtifactTests(unittest.TestCase):
    def test_artifact_verifies_and_explains_one_decision(self) -> None:
        bars=fixture_sma_bars();result=asyncio.run(_governed_run("fixture:sma-bars-v1",bars,5,15,Decimal("100000")))
        with tempfile.TemporaryDirectory() as directory:
            repository=RunArtifactRepository(directory)
            written=repository.write(mode="backtest",input_identity="fixture:sma-bars-v1",
                strategy_id="sma-cross-v1",strategy_version="1.2.0",
                config={"fast":5,"slow":15},result=result)
            loaded=repository.load(written.path); explanation=repository.explain(loaded,at=bars[20].end.isoformat())

            self.assertEqual(loaded.artifact_hash,written.artifact_hash)
            self.assertIsNotNone(explanation["factor"]);self.assertIsNotNone(explanation["decision"])
            self.assertIn("$datetime",explanation["factor"]["as_of"])

    def test_artifact_rejects_tampering(self) -> None:
        bars=fixture_sma_bars();result=asyncio.run(_governed_run("fixture:sma-bars-v1",bars,5,15,Decimal("100000")))
        with tempfile.TemporaryDirectory() as directory:
            repository=RunArtifactRepository(directory);artifact=repository.write(mode="backtest",input_identity="fixture:sma-bars-v1",
                strategy_id="sma-cross-v1",strategy_version="1.2.0",config={"fast":5,"slow":15},result=result)
            payload=json.loads(artifact.path.read_text());payload["factor_snapshots"][-1]["values"][0][1]="999"
            artifact.path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError,"hash mismatch"):
                repository.load(artifact.path)


if __name__=="__main__":unittest.main()
