from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).parents[1]
SECRET = re.compile(rb"(?:ma_[A-Za-z0-9]{24,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,})")


class RepositoryHygieneTests(unittest.TestCase):
    def test_notebook_checkpoints_are_not_present(self):
        files = [path for path in ROOT.rglob("*") if path.is_file() and ".ipynb_checkpoints" in path.parts]
        self.assertEqual(files, [])

    def test_examples_and_docs_do_not_contain_common_live_secret_shapes(self):
        matches = []
        for root in (ROOT / "examples", ROOT / "docs", ROOT / "README.md"):
            paths = (root,) if root.is_file() else root.rglob("*")
            for path in paths:
                if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
                    continue
                if SECRET.search(path.read_bytes()):
                    matches.append(str(path.relative_to(ROOT)))
        self.assertEqual(matches, [])


if __name__ == "__main__":
    unittest.main()
