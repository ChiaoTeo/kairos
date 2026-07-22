from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from kairospy.surface.cli.main import main


class DataProductExperienceTests(unittest.TestCase):
    def test_file_dataset_workflow_uses_dataset_store_terms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "signals.csv"
            source.write_text(
                "event_time,instrument_id,signal\n"
                "2026-01-01T00:00:00+00:00,BTC,1\n",
                encoding="utf-8",
            )

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "add", str(source), "--name", "research.signal", "--time", "event_time",
                ]), 0)
                added = json.loads(output.getvalue())

            self.assertEqual(added["dataset"], "research.signal")
            self.assertEqual(added["historical"]["status"], "ready")
            self.assertTrue((root / "datasets" / "research" / "signal" / "data").exists())
            self.assertFalse((root / "releases").exists())
            self.assertFalse((root / "current.ref").exists())

            for action in ("search", "describe", "doctor", "metadata", "diagnostics"):
                with self.subTest(action=action):
                    args = ["--lake-root", directory, "--format", "json", "data", action]
                    if action in {"describe", "doctor", "metadata"}:
                        args.append("research.signal")
                    with StringIO() as output, redirect_stdout(output):
                        self.assertEqual(main(args), 0)
                        payload = json.loads(output.getvalue())
                    encoded = json.dumps(payload, sort_keys=True)
                    self.assertNotIn("release_id", encoded)
                    self.assertNotIn("content_hash", encoded)
                    self.assertNotIn("quality_level", encoded)

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json",
                    "data", "query", "research.signal", "--limit", "1",
                ]), 0)
                queried = json.loads(output.getvalue())
            self.assertEqual(queried["rows"][0]["signal"], 1)

            tmp = root / "datasets" / "research" / "signal" / "tmp" / "stale"
            tmp.mkdir(parents=True)
            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main(["--lake-root", directory, "--format", "json", "data", "repair-index"]), 0)
                repaired = json.loads(output.getvalue())
            self.assertEqual(repaired["status"], "rebuilt")
            self.assertTrue((root / "index" / "cache.sqlite3").exists())

            with StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--lake-root", directory, "--format", "json", "data", "clean-tmp", "--dataset", "research.signal",
                ]), 0)
                cleaned = json.loads(output.getvalue())
            self.assertEqual(cleaned["count"], 1)
            self.assertFalse(tmp.exists())

    def test_removed_commands_report_removed_instead_of_release_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            commands = [
                ("releases", ["data", "releases"]),
                ("freeze", ["data", "freeze", "--workspace", "w", "--dataset", "d", "--output", str(Path(directory) / "x.json")]),
                ("compare", ["data", "compare", "--first", "a", "--second", "b"]),
                ("audit-artifact", ["data", "audit-artifact", "--artifact", str(Path(directory) / "artifact.json")]),
            ]
            for action, args in commands:
                with self.subTest(action=action):
                    with StringIO() as output, redirect_stdout(output):
                        self.assertEqual(main(["--lake-root", directory, "--format", "json", *args]), 2)
                        payload = json.loads(output.getvalue())
                    self.assertEqual(payload["status"], "removed")
                    self.assertNotIn("ready_for_workspace", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
