from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from examples.runtime.sma_paper_session import run


class SmaPaperSessionTests(unittest.TestCase):
    def test_canonical_capture_drives_durable_paper_and_offline_replay(self)->None:
        with tempfile.TemporaryDirectory() as directory:result=run(Path(directory))
        self.assertEqual(result["mode"],"paper-trading");self.assertGreater(result["orders"],0)
        self.assertGreater(result["fills"],0);self.assertTrue(result["restart_ready"])
        self.assertTrue(result["capture_replay_passed"]);self.assertTrue(all(result["comparisons"].values()))


if __name__=="__main__":unittest.main()
